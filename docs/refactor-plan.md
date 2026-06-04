# 小红书发布工具 重构方案

> 状态：**定稿 v4**（2026-06-02，含二轮审查发现 A–F 修订）
> 作者：重构设计 session ｜ 审核人：levin
> 范围：术语统一 + 流水线阶段重构（4 阶段 + 鉴权前置）+ 输出契约改造 + 发布链路提速 + 文案链路精简 + 可观测性 + VPS/systemd 运行化

## 审核决策记录（已拍板）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 性能慢的根因 | 单篇内部无效等待（浏览器冷启动 + 死 sleep），**当前发布之间无间隔** |
| 2 | 反检测间隔 | 主动引入，每篇之间随机 **30–90s**，可配置 |
| 3 | 调度模型 | 准备（每天 1 次）+ 发布（每天 2 批 × 20 篇），分两类 systemd timer |
| 4 | 文案生成思路 | **视觉 LLM 分析图片 → LLM 根据「商品信息 + 图片分析」写笔记**；其余文案模块全删 |
| 5 | merchant/consumer 概念 | **彻底移除**，consumer 从未真正实现 |
| 6 | 术语 | 全代码消除 `phase*`，改业务语义命名 |
| 7 | 阶段划分 | 4 个流水线阶段（商品信息获取 → 内容生成 → 制定发布计划 → 发布笔记）+ 鉴权作为贯穿前置；③④拆分理由=调度边界 |
| 8 | CLI 命名 | 全采纳改名（`fetch-products` / `publish` 等） |
| 9 | 输出契约 | **不再输出 JSON 给 AI**；改为面向 systemd/人的日志 + 退出码；`--verbose` 开详细日志 |
| 10 | 可观测性 | 默认仅失败打包证据；`--verbose` 时 stderr 实时 + 全篇步骤明细/trace 落盘；**不做主动告警** |

> **后续反转（2026-06）**：决策 10 的「不做主动告警」已被**有意反转**——需求变化所致（VPS 无人值守需要主动感知成败）。新增飞书群机器人 webhook 告警，挂在 cli.py 命令收尾，失败隔离不影响退出码。详见 `docs/feishu-notify-design.md` 与 `docs/feishu-notify-impl-plan.md`。原决策 10 的其余部分（证据/`--verbose`）不变。

**二轮审查补充决策（A–F）：**

| # | 发现 | 结论 |
|---|---|---|
| A | originality 闸门与文案精简冲突（保留硬闸门 + 新 prompt 不产 OriginalityCheck = 发布全挂） | **简化为纯相似度查重**：去掉"新核心输入+2 支持性差异"主观闸门与历史查重，只保留"与已发布/已生成草稿的相似度"检查 |
| B | publish 部分失败的退出码未定义 | **有成功即 0**：≥1 篇成功 `exit 0`（失败计入日志）；全失败 `exit 1`；登录态失效 `exit 2` |
| C | 会话复用 vs 风控特征权衡被忽略 | 列为风险；设计预留"每 N 篇重建会话"可配置项，上线后按风控反馈调 |
| D | 性能基线/实跑验收有真实发布副作用 | 基线用历史 records 时间戳估算或跑到"点发布前"为止；不为测耗时真发笔记 |
| E | 文案验收缺对比基线 | 动 prompt 前先导出现状 contents 文案存档作人工对比基线 |
| F | M3 命名搬运 publish 与 M5/M6 重写重叠 | publish 的命名/结构落位推迟到 M5 重写时一起做；M3 只重命名 products/content |

---

## 1. 背景与目标

系统部署在 VPS，由 systemd timer 定时驱动，自动在小红书商家后台发布商品种草笔记。三大痛点：**慢**（20 篇 ≈ 1 小时）、**发布常失败且无法事后定位**、**功能与术语臃肿**。调用方已从"AI 消费 JSON"变为"systemd 无人值守"。

目标：

- **提速**：消除单篇内部无效耗时，单批 20 篇在保留反检测间隔下显著缩短总时长。
- **可靠**：单篇失败隔离、断点续传、登录态失效优雅降级。
- **可观测**：默认失败打包完整证据；`--verbose` 全程详细日志，上线初期开启用于优化。
- **精简**：命令面收窄到 4 流水线阶段命令 + login/auth；删除 consumer 概念与空转的文案模块。
- **清晰**：消除 `phase*`，全代码用业务语义命名。
- **运行化**：标准 systemd service/timer 模板，准备与发布分离调度。

### 非目标

- 不引入分布式/多账号/数据库，保持单机文件存储。
- 不改登录方式（人工扫码 + auth-state 迁移）。
- 视觉 LLM 图片分析（带缓存）与 LLM 文案生成两个**核心动作保留**；改的是它们的输入组织与外围空转模块。

---

## 2. 架构总览：4 个流水线阶段 + 鉴权（贯穿前置）

鉴权不是流水线的一步，而是被"商品信息获取"和"发布笔记"依赖的横切能力——每次用浏览器前确认登录态有效。它不产出业务数据，故不编号为流水线阶段：

```
登录鉴权（贯穿前置）   login / auth     人工扫码 + auth-state 迁移；probe 校验登录态
                                        ↑ 被下面 ② ④ 在打开浏览器前调用
```

四个真正的流水线阶段，每个消费上一个的产物：

```
① 商品信息获取    fetch-products    Playwright 抓商品列表 + 下载主图/详情图（断点续传）→ products.json
② 内容生成        generate-content  视觉LLM分析图片 → LLM(商品信息+图片分析)写笔记 → 配图 → contents.json
③ 制定发布计划    plan-publish      按候选与去重规则生成当日发布计划 → publish-plan.json
④ 发布笔记        publish           一次浏览器会话批量发布，带反检测间隔，失败隔离 + 续传 → records
```

**为什么 ③ 与 ④ 是两个独立阶段**：纯流水线语义上，plan 是无副作用的快速计算，本可作为 publish 的隐含子步骤（现状代码也确实如此兜底）。拆分的真实理由是**调度边界**——见下方 systemd 调度：plan 归"准备批"（每天 1 次），publish 归"发布批"（每天 2 次黄金时段）。准备批一次性把"今天发什么"算定，发布批只管执行既定计划。

调度（systemd）：

```
xhs-prepare.timer  每天 1 次（低峰）  → fetch-products → generate-content → plan-publish
xhs-publish.timer  每天 2 次（黄金时段）→ publish --count 20
```

---

## 3. 现状诊断（带代码证据）

### 3.1 慢：每篇冷启动整个浏览器
`run_phase3_plan`（`phase3.py:582-641`）循环里每个条目调 `run_phase3`，后者每次 `with merchant_context(...)`（`phase3.py:496`）重新 `sync_playwright()` 启动 Chromium、`open_product_list_page`（`browser.py:142`）重新 `goto` 首页 + 等登录态 + 进商品列表页。**20 篇 = 20 次冷启动 + 20 次首页导航**。

### 3.2 慢：遍地固定 `wait_for_timeout`
`merchant.py` 上传多图死等 8s、单图每张 3s，切 tab/填写每步 2s，发布后固定 2s……单篇累计死等 30–60s，且非条件等待。

### 3.3 当前发布之间没有反检测间隔
`run_phase3_plan` 循环（`phase3.py:584`）发完直接发下一篇，无 sleep。故"1 小时 20 篇"的慢 100% 来自单篇内部。

### 3.4 可观测性缺失
失败仅落一张截图 + HTML（`phase3.py:407`），records 里 `error` 是字符串，无步骤、无耗时、无 trace。systemd 无人值守下失败无法复盘。

### 3.5 输出契约对 systemd 已无意义
所有命令经 `emit_json`（`cli.py:42`）输出大段 JSON，原为给 AI 消费。调用方改为 systemd 后，JSON 无人读，反而淹没 journal。退出码才是 systemd 关心的信号。

### 3.6 文案链路大半空转（关键发现）
`references/history-notes/` **为空目录**，导致：

| 模块 | 名义作用 | 实际状态 |
|---|---|---|
| `history_notes` | few-shot 风格参考 | **素材空，全链空转**，prompt `history_refs[:3]` 为空 |
| `trend_signals` | 趋势热词 | history 空 → 退化 `build_fallback_hot_notes_analysis` 低质兜底 |
| `hot_notes` | 热门笔记分析 | 同上；仅 `infer_search_keyword` 还有用 |
| `image_facts` | PIL 颜色/亮度 | 视觉 LLM 的降级兜底 |
| `facts_builder` | 打包快照喂 LLM | 打包的大半是空/兜底数据 |
| `image_semantics` | **视觉 LLM 分析图片** | ✅ 真正在工作，sha256 长期缓存 |
| `content_gen` | **LLM 写文案** | ✅ 真正在工作 |

**撑起文案的就是「商品信息 + 视觉 LLM 图片分析」**，其余删掉对输出几乎无影响。

### 3.7 术语与概念臃肿
`phase1/2/3` 命名不表意；merchant/consumer 二分中 consumer 从未实现却散落各处（`config.py / browser.py / auth.py / models.py / cli.py`）。

---

## 4. 重构设计

### 4.1 术语统一与命名

全代码消除 `phase*`，按业务语义命名。**CLI 命令全部改名**（详见 §6 映射表）。核心：

```
fetch-products      （原 prepare-products）
generate-content    （不变，已直观）
plan-publish        （不变）
publish             （原 run-publish-plan）
login / auth        （不变）
```

### 4.2 输出契约重定义

- **删除 `emit_json` 与全部 JSON 包装模型**（`SkillError` / `Phase1Success` / `Phase2Success` / `Phase3*Success` 等）。
- **退出码**是 systemd 的主信号：`0` 成功；`2` 登录态失效/需人工；`1` 其他失败。
  - **publish 部分失败语义**（发现 B）：批量发布 ≥1 篇成功即 `exit 0`（失败数计入日志/汇总，不让 systemd 因个别失败标红）；全部失败 `exit 1`；登录态失效 `exit 2`。准备阶段命令（fetch/generate/plan）维持"出错即非 0"。
- **默认输出**：面向人的简洁进度/结果日志（如 `[publish] 12/20 成功，3 失败，证据见 .../evidence/`）。
- **`--verbose`**：详细日志（见 §4.3）。
- 领域模型（`ProductSummary` / `ContentDraft` / plan / records 等）保留，仅删 JSON 响应壳，`models.py` 大幅瘦身。

> CLAUDE.md 中"CLI 子命令均通过 emit_json() 输出"的约定随之更新为"日志 + 退出码"。

### 4.3 可观测性：默认证据 + `--verbose`

两档：

- **默认（精简）**：失败才打包证据到按天目录；stdout/stderr 出简洁汇总。
- **`--verbose`（详细）**：
  - 每篇每步实时打到 **stderr**（systemd journal 可直接 `journalctl` 看）；
  - **所有篇（含成功）**的步骤明细 + Playwright trace 落盘到证据目录；
  - 上线初期开启用于优化，稳定后关闭。

证据目录结构：

```
xiaohongshu-data/publish/<date>/evidence/<product_id>-<angle>-<HHMMSS>/
  ├─ trace.zip      # Playwright tracing（步骤/DOM快照/network/console）
  ├─ screenshot.png
  ├─ page.html
  └─ steps.jsonl    # 每步 step/status/elapsed_ms/detail
```

trace：默认仅失败保留；`--verbose` 时全保留。

### 4.4 发布链路提速

整批共享一个浏览器会话：

```
publish --count 20
└─ 打开 1 次浏览器 + 进 1 次商品列表页（PublishSession）
   └─ for 每个 pending 条目:
        ├─ 独立 tab 开发布页 → 上传 → 填写 → 加话题 → 绑商品 → 发布 → 校验
        ├─ 即时写 records + 更新 plan（断点续传）
        ├─ 失败 → 打包证据，标记 failed，关 tab，继续（隔离）
        └─ sleep(random.uniform(30, 90))   ← 反检测间隔（可配置）
   └─ 关浏览器，输出汇总
```

- **会话复用**：浏览器与列表页只初始化一次（`publish/session.py`）。
- **条件等待**：所有 `wait_for_timeout(N)` 改 `wait_for_selector / expect / wait_for_function / expect_event`，配合合理超时；仅极少数平台节流点保留小幅固定等待。
- **反检测间隔**：每篇之间随机 30–90s（配置项，可调）。

> **诚实估算**：单篇有效操作受页面响应 + 图片上传带宽限制，约 40–70s/篇（上传 9 图不可压到秒级）。叠加间隔后单批 20 篇约 20–40 分钟，较当前 ~60 分钟明显改善，且间隔变成真实风控而非伪装的低效。不承诺"秒发"。

> ⚠️ **会话复用 vs 风控特征的权衡（发现 C）**：会话复用省掉启动开销，但把"20 个独立冷启动会话"变成"1 个会话持续 20–40 分钟、20 次高频发布"。两者的风控指纹不同，无法先验断定哪个更安全——取决于小红书检测逻辑。设计上**预留 `publish_session_recycle_every` 配置**（每发 N 篇重建一次会话，默认值上线后按风控反馈调整：设为很大=整批一个会话，设为 1=退化回每篇一会话）。这是提速与风控之间的可调旋钮，不是非此即彼。

### 4.5 文案链路精简

精简后内容生成 = **`vision`（图片分析，带缓存）→ `llm`（商品信息 + 图片分析 → 笔记）→ `images`（配图）**。

- **保留**：`image_semantics`→`content/vision.py`；`content_gen`→`content/llm.py`（prompt 重写为纯「商品信息 + 图片分析」，去掉 history/trend）；`image_allocation`→`content/images.py`。
- **删除**：`trend_signals` / `hot_notes` / `history_notes` / `image_facts` / `facts_builder` / `phase2_report` 及对应数据文件。
- `infer_search_keyword`（关键词推断）若 llm 还需要，迁入 `content/llm.py` 作小工具函数；否则一并删。
- **originality 闸门简化（发现 A）**：当前 `originality.py` 是发布前硬闸门，要求每篇含"1 个新核心输入 + 2 个支持性差异"，且这套字段是旧 prompt 专门让 LLM 生成的——新 prompt 不再产出它，否则 `assert_publishable_originality` 会让每篇发布抛错（发布全挂）。**改为纯相似度查重**：
  - 删除"新核心输入 / 支持性差异 / core_input_type"等主观闸门逻辑与对应 `OriginalityCheck` 字段；
  - 删除依赖 history_style_refs 的历史查重（素材已删）；
  - 保留并作为发布前软/硬闸门：**与已发布笔记、本批已生成草稿的标题/正文相似度检查**（`similarity_ratio` + 阈值），防止 LLM 批量产出雷同被平台判重；
  - `content/llm.py` 的 prompt 不再背负 OriginalityCheck schema，专注"商品信息 + 图片分析 → 笔记"。
- **验收**：相同商品输入下，generate-content 仍产出可发布的 `contents.json`（结构与发布链路兼容），且相似度查重能挡住雷同稿。

### 4.6 健壮性

- **单篇失败隔离**：异常 → 记 failed + 打包证据 + 继续，不中断整批。
- **断点续传**：每篇即时写 plan/records；进程中断后 `publish` 复用未发完 plan 续发（`reconcile_publish_plan_with_records` 雏形保留强化）。
- **登录态失效**：批次开始校验一次；失效则整批退出 `exit 2` 并打包证据，不逐篇空转。
- **页面自愈**：单篇发布页异常时回到常驻列表页重开，不拖垮整批。

### 4.7 功能精简与 consumer 移除

- **删命令**：`prepare-trends` / `publish-note` / `list-publish-candidates`（信息并入 `plan-publish` 输出）。
- **删 consumer/SiteName 概念**：`consumer.py`、`login consumer`、auth 的 consumer 选项、`SiteName` 类型、各 `*_consumer_*` 路径/context 全删，固定商家端单站点。`hot_notes` 的 consumer 联网分支随文案精简一并删（已核实不在 phase2 运行路径，删后文案不变）。

### 4.8 运行化（systemd）

`deploy/` 提供模板 + 说明：

- `xhs-prepare.service` + `.timer`：每天 1 次 → `fetch-products && generate-content && plan-publish`；prepare 失败不应产脏数据，退出码语义明确。
- `xhs-publish.service` + `.timer`：每天 2 次黄金时段 → `publish --count 20`；headless、`WorkingDirectory=仓库根`、`EnvironmentFile=.env`、失败等下一次 timer 而非自动重启。

---

## 5. 目标代码结构

```
src/xhs_poster/
  cli.py                 # Typer 入口：4 阶段命令 + login/auth；日志 + 退出码（无 JSON）
  config.py              # 清理 phase*/consumer/文案删模块路径；加间隔/verbose/trace 配置
  models.py              # 删 JSON 壳与 SiteName；保留领域模型 + 发布模型增强
  logging.py             # 统一日志（默认简洁 / --verbose 详细，输出 stderr）
  auth.py                # 登录鉴权，单站点
  browser.py             # 浏览器会话原语 + 条件等待工具（去 consumer）
  products/              # ② 商品信息获取（原 phase1 + merchant 抓图 + image_pipeline/assets）
    fetch.py · page.py · images.py
  content/               # ③ 内容生成（精简）
    generate.py          #   编排：图片分析 → 写文案 → 配图
    vision.py            #   视觉 LLM 图片分析（原 image_semantics，带缓存）
    llm.py               #   LLM 文案（原 content_gen，prompt 重写）
    images.py            #   配图分配（原 image_allocation）
  publish/               # ⑤ 发布（重写）
    plan.py              #   ④ 计划与候选（含原 list-candidates 信息）
    session.py · page.py · records.py · evidence.py
  originality.py         # 简化为纯相似度查重（与已发布/已生成草稿比对），生成时与发布前各用一次
  deploy/                # systemd service/timer 模板 + 部署文档
```

**删除文件**：`consumer.py` `trend_signals.py` `hot_notes.py` `history_notes.py` `image_facts.py` `facts_builder.py` `phase2_report.py`。

---

## 6. 命名映射表（实施对照）

**CLI 命令**：

| 阶段 | 旧 | 新 |
|---|---|---|
| 登录鉴权 | `login merchant` / `auth probe\|export\|import` | `login` / `auth probe\|export\|import`（去 site 参数） |
| 商品信息获取 | `prepare-products` | `fetch-products` |
| 内容生成 | `generate-content` | `generate-content` |
| 制定发布计划 | `plan-publish` | `plan-publish` |
| 发布笔记 | `run-publish-plan` | `publish` |
| —（删） | `prepare-trends` / `publish-note` / `list-publish-candidates` / `login consumer` | 删除 |

**模块文件**：

| 旧 | 新 |
|---|---|
| `phase1.py` | `products/fetch.py` |
| `merchant.py`（抓图部分） | `products/page.py` |
| `image_pipeline.py` / `image_assets.py` | `products/images.py` |
| `phase2.py` | `content/generate.py` |
| `image_semantics.py` | `content/vision.py` |
| `content_gen.py` | `content/llm.py` |
| `image_allocation.py` | `content/images.py` |
| `phase3.py` | `publish/{plan,session,records}.py` |
| `merchant.py`（发布部分） | `publish/page.py` |
| `trend_signals/hot_notes/history_notes/image_facts/facts_builder/phase2_report/consumer` | 删除 |

**数据文件**：

| 旧 | 新 |
|---|---|
| `today-pool.json` | `products.json` |
| `phase1-state.json` | `products-state.json` |
| `contents.json` | `contents.json` |
| `publish-plan.json` | `publish-plan.json` |
| `phase3/<date>/publish-records.json` | `publish/<date>/records.json` |
| `artifacts/phase3/` | `publish/<date>/evidence/` |
| `image-semantic-facts.json` | `image-analysis.json` |
| `phase2-report.json` / `image-facts.json` / `product-facts.json` / `hot-notes-analysis.json` / `history-style-refs.json` / `trend-signals.json` | 删除 |

> 命令全采纳改名，**不保留旧别名**（调用方是 systemd，部署模板同步更新即可）。

---

## 7. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 大重命名引入断链 | 先做纯重命名里程碑，`compileall` + import 检查 + 冒烟逐步验证；一次一层 |
| 删文案模块影响输出 | 已核实核心是 vision+商品信息；动 prompt 前导出现状文案作对比基线（发现 E），重写后人工抽检 |
| 会话复用被风控识别（发现 C） | 单一长会话高频发布的指纹与每篇独立会话不同；`publish_session_recycle_every` 可调，上线按反馈折中 |
| 会话复用状态串扰 | 每篇独立 tab、发完即关；列表页每篇前校验就绪 |
| originality 闸门简化漏挡雷同 | 保留相似度查重 + 阈值；阈值可调，必要时人工抽检相似度日志 |
| 条件等待选择器失效 | 失败即打包 trace；选择器集中管理便于改 |
| 间隔设小被风控 | 间隔可配置 + 随机；默认偏保守 |
| 去 JSON 后信息缺失 | `--verbose` 提供全量细节；证据包覆盖事后分析 |
| trace 体积 | 默认仅失败留；`--verbose` 全留但按天目录便于轮转清理 |

---

## 8. 验收标准

- **性能**：单批 20 篇（含默认间隔）总时长较当前下降 ≥ 40%；去间隔后单篇有效操作 ≤ 90s。
  - **基线测量法（发现 D）**：现状基线用历史 `publish-records.json` 的 `attempted_at` 时间戳间隔估算，或重构后跑到"点发布前"为止计时；**不为测耗时真发笔记到线上店铺**。
- **可靠**：单篇失败不影响同批其余；进程中断后 `publish` 能续发。
- **可观测**：任意失败可在证据目录定位失败步骤（steps.jsonl）并回放（trace.zip）；`--verbose` 下 journal 有全程实时日志。
- **精简**：`xhs-poster --help` 仅列 4 阶段命令 + login/auth；代码中 `grep -r phase\|consumer\|SiteName` 无残留；文案链路删模块全部移除。
- **文案**：精简后 generate-content 产出可发布 contents.json，人工抽检质量不劣于现状。
- **输出**：命令无 JSON 输出；退出码语义正确（0/1/2）；systemd journal 可读。
- **质量**：`uv run python -m compileall src` 通过；ruff / basedpyright 按项目配置零新增告警。
- **运行化**：`deploy/` 模板可在 VPS 直接安装，两类 timer 各司其职，实跑一轮通过。

---

## 9. 里程碑计划（概览，详细实施计划另出）

"一个里程碑 = 一个可独立验收的最小功能单元"，顺序遵循"先理顺结构、再提速、再可观测、再上线"：

| M | 名称 | 验收要点 | 风险 |
|---|---|---|---|
| M0 | 基线固化 | 冒烟脚本；用历史 records 估算耗时基线（不真发，发现 D）；导出现状文案样本作对比基线（发现 E） | 低 |
| M1 | 文案链路精简 + originality 简化 | 删 6 个空转模块；prompt 重写；originality 改纯相似度查重（发现 A）；generate-content 仍产可发布 contents 且能挡雷同 | 中 |
| M2 | consumer/SiteName 移除 | 删 consumer 概念；行为不变，compileall 通过 | 低 |
| M3 | 术语统一 + 命名重构（products/content） | phase*→语义命名、命令改名、products/ 与 content/ 包结构落位；**publish 命名暂不动（留 M5，发现 F）**；冒烟全绿 | 中 |
| M4 | 输出契约改造 | 去 emit_json/JSON 壳，改日志 + 退出码（含 publish 部分失败语义，发现 B）；models 瘦身 | 中 |
| M5 | 发布会话复用 + publish 命名落位 | 重写 publish 时直接落位 `publish/` 新结构与命名（发现 F）；整批共享一个浏览器会话（`publish_session_recycle_every` 可调，发现 C）；性能首跳 | 高 |
| M6 | 条件等待改造 | 消除死 sleep，单篇有效操作 ≤ 90s | 高 |
| M7 | 反检测间隔 | 可配置随机 30–90s 接入 | 低 |
| M8 | 可观测性 | 默认失败证据 + `--verbose` 全程日志/trace | 中 |
| M9 | 健壮性 | 断点续传强化、登录失效整批退出、页面自愈 | 中 |
| M10 | systemd 运行化 | service/timer 模板 + 部署文档 + VPS 实跑验收（验证发 1–2 篇即可，避免污染，发现 D） | 中 |

> 顺序说明：M1（删空转模块 + 简化闸门）先于 M3（重命名），避免给将删的模块白做重命名；M3 只重命名 products/content，**publish 的命名/结构落位并入 M5 重写**（避免对将被重写的代码做无谓搬运，发现 F）；M2/M4 是低-中风险的结构/契约清理；M5/M6 是性能收益主体（需实跑验证）；M8 是可观测主体。每个里程碑产出可验证、可回滚。
```