# 实施计划（里程碑级）

> 配套方案：`docs/refactor-plan.md`（v4 定稿）。本文件是可执行的逐里程碑计划。
> 工作方式：**一个 session 只做一个里程碑**，做完 `/clear` 再开下一个。

---

## 给每个新 session 的使用说明（务必先读）

`/clear` 之后你没有上一轮对话记忆。按以下步骤冷启动，**不要依赖任何对话历史**：

1. 读本文件的「进度跟踪」表，找到第一个未完成（⬜）的里程碑 = 你这次要做的。
2. 读该里程碑的完整小节（目标 / 前置状态 / 涉及文件 / 改动点 / 验收 / 回滚 / 收尾清单）。
3. 读 `docs/refactor-plan.md` 对应章节作为设计依据；读项目记忆 `memory/refactor-v2.md`（含隐藏耦合陷阱）。
4. 核对「前置状态」与当前代码是否一致（前序里程碑应已落在 git 里）。不一致就停下来问，不要硬上。
5. 编码 → 跑「验收」里的命令 → 全绿后执行「收尾清单」。
6. 收尾必须做：① git 提交（中文信息）② 把进度表该行改 ✅ ③ 必要时更新 `memory/`。提示用户 `/clear` 后继续下一个。

**铁律**：每个里程碑结束，`uv run python -m compileall src` 与冒烟必须绿，系统不留半坏中间态。只改与当前里程碑相关的代码。

---

## 进度跟踪

| M | 名称 | 状态 | 提交 |
|---|---|---|---|
| M0 | 基线固化 | ✅ | fda7355 |
| M1 | 文案链路精简 + originality 简化 | ✅ | 8be265c |
| M2 | consumer/SiteName 移除 | ✅ | b86ae02 |
| M3 | 术语统一 + 命名重构（products/content） | ✅ | 9779964 |
| M4 | 输出契约改造 | ✅ | 8934e3b |
| M5 | 发布会话复用 + publish 命名落位 | ⬜ | |
| M6 | 条件等待改造 | ⬜ | |
| M7 | 反检测间隔 | ⬜ | |
| M8 | 可观测性（--verbose + 失败证据） | ⬜ | |
| M9 | 健壮性 | ⬜ | |
| M10 | systemd 运行化 | ⬜ | |

> M2–M10 当前只有 §9 概览（见 refactor-plan.md）。**做到哪个，就在它前一个里程碑收尾时把它展开成下面 M0/M1 这样的详细小节**，避免一次写一大份很快过时的细节。

---

## M0 — 基线固化

**目标**：建立后续里程碑可独立对照的基线（耗时 + 文案样本）+ 冒烟脚本，使后续验证不必真发笔记。纯新增，不碰 `src/`，零风险。

**前置状态**：refactor 分支干净，无任何里程碑改动。

**涉及文件**（全部新增）：
- `scripts/smoke.sh` — 冒烟脚本
- `docs/baseline/contents-before.json` — 现状文案样本（发现 E）
- `docs/baseline/perf-baseline.md` — 现状耗时估算（发现 D）

**改动点**：
1. **冒烟脚本** `scripts/smoke.sh`：
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   uv run python -m compileall src
   uv run xhs-poster --help >/dev/null && echo "help OK"
   ```
   赋可执行权限。后续每个里程碑收尾都跑它。
2. **文案基线**：把当前 `xiaohongshu-data/contents.json` 复制到 `docs/baseline/contents-before.json`（若不存在则记录"无现状样本"）。M1 重写 prompt 后用它做人工对比。
3. **耗时基线**：读历史 `xiaohongshu-data/phase3/<date>/publish-records.json` 的 `attempted_at` 时间戳，算相邻成功记录的间隔，估算"现状单篇耗时"，写进 `docs/baseline/perf-baseline.md`。**不真发笔记**。若历史记录不足，注明"基线不可得，以 M5/M6 实测为准"。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 全绿；
- `docs/baseline/` 下两个文件存在且有内容。

**回滚**：删 `scripts/smoke.sh` 与 `docs/baseline/`。纯新增，无副作用。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿
- [ ] git 提交：`基线固化：冒烟脚本 + 文案/耗时基线`
- [ ] 进度表 M0 → ✅
- [ ] 提示用户 `/clear` 继续 M1

---

## M1 — 文案链路精简 + originality 简化

**目标**：删掉空转的文案模块，把内容生成收敛为「商品信息 + 视觉LLM图片分析 → 笔记」；originality 从硬闸门改为纯相似度查重。`generate-content` 仍产出可发布的 `contents.json`，且能挡住雷同稿。

> ⚠️ **隐藏耦合（务必先懂，详见 refactor-plan §4.5 发现 A）**：`originality.py` 的 `assert_publishable_originality` 是发布前硬闸门，要求每个 draft 带合格 `OriginalityCheck`，而该字段是旧 content_gen prompt 专门生成的。若只重写 prompt 不改闸门 → 后续 publish 阶段每篇抛错、发布全挂。所以 **prompt 重写与 originality 简化必须在本里程碑一起做**。

**前置状态**：M0 已完成（`docs/baseline/` 存在）。代码仍是 phase* 命名（M1 不改名），consumer 仍在（留 M2）。

**涉及文件**：
- **删除**：`src/xhs_poster/trend_signals.py`、`hot_notes.py`、`history_notes.py`、`image_facts.py`、`facts_builder.py`、`phase2_report.py`
- **改写**：
  - `phase2.py` — 去掉对上述模块的 import 与调用，主流程收敛为：load today-pool → 逐商品 `analyze_product_image_semantics`（视觉LLM，保留）→ `generate_product_contents`（精简）→ `allocate_image_paths`（配图，保留）→ 写 `contents.json`。删除写 `image-facts/hot-notes/product-facts/phase2-report` 等产物的代码。
  - `content_gen.py` — prompt 重写为纯「商品信息 + semantic_facts」；删除 history_style_refs / trend_analysis 入参与 `OriginalityCheck` schema 生成；保留 LLM 调用、JSON 解析、模板兜底。
  - `originality.py` — 删 `build_default_originality_check` 的主观闸门（core_input_type / supporting_differences）与历史查重（`_check_history_template_reuse`）；保留 `similarity_ratio` + `_check_generated_draft_reuse`；`assert_publishable_originality` 改为只基于"与已发布/已生成草稿的相似度阈值"。
  - `models.py` — 删随模块移除而无用的模型：`HotNotesAnalysis`、`HistoryStyleReference`、`ProductImageFacts`、`ProductSemanticFacts` 中如有 facts 专用字段酌情保留（vision 仍需）、`ProductFactsSnapshot`、`OriginalityCheck` 的主观字段。**保留** vision 相关模型与 `ContentDraft`。
  - `config.py` — 删 `image_facts_path / hot_notes_analysis_path / raw_hot_notes_path / product_facts_path / phase2_report_path / history_style_refs_path / trend_signals_path` 等无用路径。
- **注意**：`infer_search_keyword` 原在 `hot_notes.py`，删该文件时若 prompt 仍需关键词，内联到 `content_gen.py` 作小函数；否则删。`consumer.py` 在删 `hot_notes.py` 后变成孤儿，**本里程碑不删它**（留 M2 统一处理 consumer 概念）。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 绿；
- `grep -rE "trend_signals|hot_notes|history_notes|image_facts|facts_builder|phase2_report" src/` 无残留引用；
- `uv run xhs-poster generate-content`（需 `xiaohongshu-data/today-pool.json` 存在 + `.env` 有 LLM key）产出 `contents.json`，结构含 `contents[product_id]` 草稿列表且每条有 `selected_image_paths`；
- 人工抽检：对比 `docs/baseline/contents-before.json`，文案质量不劣于现状；
- 相似度查重生效：可临时构造两条高度相似草稿，确认 `assert_publishable_originality` 能挡（或加一个最小 pytest）。

**回滚**：`git revert` 本里程碑提交（删除的模块随之恢复）。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿 + `generate-content` 实跑通过
- [ ] grep 无残留
- [ ] git 提交：`文案链路精简：删空转模块 + originality 改相似度查重`
- [ ] 进度表 M1 → ✅；**展开 M2 的详细小节**（参照本节格式）
- [ ] 若发现新的隐藏耦合，更新 `memory/refactor-v2.md`
- [ ] 提示用户 `/clear` 继续 M2

---

## M2 — consumer/SiteName 移除

**目标**：彻底删除 consumer 概念与 `SiteName` 类型。所有鉴权函数固化为商家端单站点，CLI 删去 `login consumer` 与 auth 命令的 `site` 参数。行为与现有商家端流程完全不变，`compileall` + 冒烟通过。

**前置状态**：M1 已完成（删 6 个空转模块、originality 改相似度查重）。`consumer.py` 仍存在，SiteName、consumer_* 散落各处。

**涉及文件**：
- **删除**：`src/xhs_poster/consumer.py`
- **改写**：
  - `models.py` — 删 `SiteName = Literal["merchant", "consumer"]`；`SessionInfo.site` 改 `Literal["merchant"]`；`SkillError.site` 字段删（或改 `str | None`，M4 会统一删 SkillError，先删字段）。
  - `config.py` — 删 `consumer_home_url`、`consumer_auth_state_path_override`、`consumer_profile_dir`、`consumer_auth_state_path`；`ensure_directories()` 去掉 consumer_* 两行。
  - `browser.py` — 删 `SiteName` import；`site_profile_dir / site_auth_state_path / profile_has_state / available_auth_sources / launch_site_persistent_context / launch_site_runtime_context` 全去掉 `site` 参数，固化为商家端；删 `launch_consumer_context` 与 `consumer_context`；`launch_merchant_context` 直接内联调用（可保留函数名，删 site 参数的内部版本）。
  - `auth.py` — 删 `SiteName` import；删 `_has_consumer_auth_cookies / _consumer_has_logged_in_markers / _site_home_url / _site_profile_dir / _site_auth_state_path` 等 consumer 辅助函数（或简化为直接用 settings 属性）；`_is_authenticated_page / _probe_context / login_site` 中删除 `if site == "consumer"` 分支；所有公开函数删 `site: SiteName` 参数（固化为 merchant）；`_build_session_info` 中 `site` 参数改为 `Literal["merchant"]` 或直接硬编码。
  - `cli.py` — 删 `login consumer` 命令与 `login_consumer` 函数；删 auth_probe / auth_export / auth_import 的 `site` Argument；删 `_run_login` 的 `site` 参数；更新对 auth 模块的调用签名（不再传 site）；删 `SiteName` import。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 绿；
- `grep -rn "consumer\|SiteName" src/` 无残留（`consumer.py` 已删，无需排除）；
- `uv run xhs-poster --help` 输出无 `consumer`、无 `site` 参数；
- `uv run xhs-poster auth probe` 能运行（不传 site，直接探测商家端）；
- `uv run xhs-poster login --help` 仅有 `merchant` 子命令（或直接取消子命令层级，视改动量决定）。

**回滚**：`git revert` 本里程碑提交。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿
- [ ] `grep -rn "consumer\|SiteName" src/` 无残留
- [ ] git 提交：`M2：删 consumer/SiteName，auth 固化商家端单站点`
- [ ] 进度表 M2 → ✅；展开 M3 的详细小节
- [ ] 提示用户 `/clear` 继续 M3

---

## M3 — 术语统一 + 命名重构（products / content）

**目标**：消除 products 与 content 两条链路上的 `phase*` 命名，按业务语义重组为 `products/` 与 `content/` 两个子包；CLI `prepare-products` 改名 `fetch-products`；三个产品/内容数据文件改语义名。**publish 链路（`phase3.py` / `merchant.py` / `image_pipeline.py` / `Phase3*` / `run-publish-plan` / `publish-note` / `list-publish-candidates`）整体不动，留 M5**（发现 F：不给将被重写的代码白搬）。行为不变，冒烟全绿。

**前置状态**：M2 已完成（consumer/SiteName 已删）。`src/xhs_poster/` 仍是扁平布局：`phase1/phase2/phase3 + merchant + image_* + content_gen + originality` 并列；`__init__.py` 为空（`__all__: list[str] = []`）。

**关键耦合（务必先懂）**：
- `merchant.py`（1199 行）含三类：`ProductDetailPage`（抓图）、`ProductListPage`（**fetch 与 publish 共用**，`phase1` 与 `phase3` 都 import）、`PublishPage`（发布）。因 `ProductListPage` 横跨两端，**M3 不拆 `merchant.py`**——整体留顶层，products 侧继续 `from ..merchant import ProductListPage`，待 M5 重写 publish 时再拆 `publish/page.py`。
- `image_pipeline.py` **只被 `merchant.py` import**（不被 phase1 用），属页面抽取/发布侧共享底层。**故不并入 `products/images.py`**（这点与 refactor-plan §6 映射表不同，是基于实际依赖图的修正）；它随 `merchant.py` 留顶层，M5 再归位。M3 只把 products-only 的 `image_assets.py` 折进 `products/images.py`。
- `phase3.py` 不在 M3 改名范围，但它读 `settings.today_pool_path`（4 处）。该 config 属性随数据文件改名后，`phase3.py` 的读取处必须同步改，否则 publish 读不到商品池。

**涉及文件**：

新增（空 `__init__.py`，与根同风格，不写 `__all__`）：
- `src/xhs_poster/products/__init__.py`
- `src/xhs_poster/content/__init__.py`

模块移动 / 改名（`git mv`）：

| 旧 | 新 |
|---|---|
| `phase1.py` | `products/fetch.py` |
| `image_assets.py` | `products/images.py` |
| `phase2.py` | `content/generate.py` |
| `content_gen.py` | `content/llm.py` |
| `image_semantics.py` | `content/vision.py` |
| `image_allocation.py` | `content/images.py` |
| `merchant.py` / `phase3.py` / `originality.py` / `image_pipeline.py` | **不动（顶层保留，留 M5）** |

**改动点**：

1. **`git mv` 移动 6 个模块**到上表新位置。

2. **重写被移动模块的内部相对导入**（顶层 `.x` → 子包 `..x`；同包内引用改 `.新名`）：
   - `products/fetch.py`：`.auth`→`..auth`、`.browser`→`..browser`、`.config`→`..config`、`.models`→`..models`、`.merchant`→`..merchant`、`.image_assets import build_local_assets`→`.images import build_local_assets`。
   - `content/generate.py`：`.config`→`..config`、`.models`→`..models`、`.originality`→`..originality`、`.content_gen`→`.llm`、`.image_allocation`→`.images`、`.image_semantics`→`.vision`（被导入的符号名 `analyze_product_image_semantics` / `load_image_semantic_facts` / `save_image_semantic_facts` / `allocate_image_paths` / `generate_product_contents` / `check_draft_similarity` 保持不变，只改模块路径）。
   - `content/llm.py`、`content/vision.py`、`products/images.py`：`.config`→`..config`、`.models`→`..models`。

3. **`cli.py`**：
   - `from .phase1 import build_phase1_payload` → `from .products.fetch import build_fetch_products_payload`
   - `from .phase2 import build_phase2_payload` → `from .content.generate import build_generate_content_payload`
   - 命令 `@app.command("prepare-products")` → `@app.command("fetch-products")`（函数体不变）；更新其 help 文本里的 `phase1-state.json`/`today-pool.json` → `products-state.json`/`products.json`。
   - `generate-content` / `plan-publish` / `publish-note` help 文本中的 `today-pool` 措辞 → `products`。
   - **不改** `run-publish-plan` / `publish-note` / `list-publish-candidates` 命令名（留 M5）。

4. **`config.py`** 路径属性 + 文件名改语义名（仅 products/content 三项；`phase3_*` 全留 M5）：
   - `today_pool_path` → `products_path`，`"today-pool.json"` → `"products.json"`
   - `phase1_state_path` → `products_state_path`，`"phase1-state.json"` → `"products-state.json"`
   - `image_semantic_facts_path` → `image_analysis_path`，`"image-semantic-facts.json"` → `"image-analysis.json"`
   - `contents_path` / `publish_plan_path` / `phase3_*` 不变。

5. **改名所有对上述 config 属性的引用**（`grep -rn "today_pool_path\|phase1_state_path\|image_semantic_facts_path" src/`）：`products/fetch.py`、`content/generate.py`、`content/vision.py`，以及 **`phase3.py`（4 处读 `today_pool_path` + RuntimeError 文案中的 `today-pool.json`）**。

6. **`models.py` 内 phase1/phase2 命名的符号改名**（`models.py` 不移动；`Phase3*` 留 M5）：
   - 存活的领域模型 `Phase1State`（断点续传检查点，products-state.json 内容）→ `ProductsState`。
   - JSON 响应壳 `Phase1Success` → `FetchProductsResult`、`Phase2Success` → `GenerateContentResult`（M4 会删除它们；M3 仍改名，以保证 products/ 与 content/ 模块零 `phase1/phase2` token，且让 M4 只需"删除+换日志"而非"先改名再删"）。
   - 对应 `build_phase1_payload`→`build_fetch_products_payload`、`build_phase2_payload`→`build_generate_content_payload`（函数体不动，M4 重写契约）。
   - 模型内 `today_pool_path: str` / `image_semantic_facts_path` 等**数据名字段**不含 `phase`，不阻塞 M3，留 M4 随壳删除（不无谓改动）。
   - **`models.py` 顶层仍保留 `Phase3*` 等 publish 模型**，故 M3 后 `models.py` 仍有 `phase3` token —— 正常，M5 处理。

7. **数据文件不自动迁移**：旧 `today-pool.json` / `phase1-state.json` / `image-semantic-facts.json` 为运行时产物，改名后重跑 `fetch-products` / `generate-content` 重新生成；视觉缓存若想保留，手工 `mv image-semantic-facts.json image-analysis.json`，否则触发一次视觉 LLM 重算（可接受）。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 绿；
- `grep -rnE "phase1|phase2|image_pipeline|image_assets|content_gen|image_semantics|image_allocation" src/xhs_poster/products src/xhs_poster/content` 无残留（两包已完全去旧名）；
- `phase3.py` / `merchant.py` 仍能 `from .merchant import ProductListPage` 编译通过（publish 链路未破）；
- `uv run xhs-poster --help` 出现 `fetch-products`、不再有 `prepare-products`；`run-publish-plan` / `publish-note` / `list-publish-candidates` 仍在；
- `uv run xhs-poster generate-content`（需 `products.json` 存在 + `.env` 有 LLM key）跑通，产出结构不变的 `contents.json`；
- 可选实跑：`uv run xhs-poster fetch-products --limit 1`，确认写出 `products.json` / `products-state.json`。

**回滚**：`git revert` 本里程碑提交（`git mv` 与改名随之回退）。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿 + `generate-content` 实跑通过
- [ ] products/ 与 content/ grep 无旧名残留；publish 链路（phase3/merchant/image_pipeline）编译通过
- [ ] git 提交：`M3：术语统一+命名重构，phase1/2 → products/content 包`
- [ ] 进度表 M3 → ✅；**展开 M4 的详细小节**（参照本节格式）
- [ ] 若发现新的隐藏耦合，更新 `memory/refactor-v2.md`
- [ ] 提示用户 `/clear` 继续 M4

---

## M4 — 输出契约改造（日志 + 退出码）

**目标**：删除 `emit_json` 与全部 JSON 响应壳模型，命令改为「人读日志（stderr）+ 退出码」。退出码语义统一为 `0` 成功 / `1` 其他失败 / `2` 登录态失效；**publish 批量按发现 B 改为「≥1 篇成功即 0」**（当前 `run-publish-plan` 是"有任一失败即 1"，是 bug，必须修）。`models.py` 删壳瘦身。**publish 系列命令最小改动**（去 JSON + 修退出码 + 一行汇总），不重构 `phase3.py` 结构/日志（留 M5 重写、M8 丰富证据）。

**前置状态**：M3 已完成。命令分两种输出模式：① auth/login（`probe/login/export/import`）直接 `emit_json(SessionInfo)`，退出码 0/2；② 流水线命令调 `build_*_payload()` 拿 `(dict, exit_code)` 再 `emit_json`。所有 JSON 壳与 `SkillError` **仅** 被 `cli.py` 与各 `build_*_payload` 使用（已核实无其他消费方）。

**关键事实（务必先懂）**：
- `cli.py:40 emit_json` + 13 处调用；`cli.py` 仅为 `emit_json` 而 `import json`。
- **壳模型（删）**：`SkillError`、`FetchProductsResult`、`GenerateContentResult`、`Phase3Success`、`Phase3CandidatesSuccess`、`Phase3PlanSuccess`、`Phase3RunPlanSuccess`。
- **领域模型（留）**：`SessionInfo`（auth 状态，含 `.authenticated/.status/.message/.site`）、`FetchProductsExecutionResult`、`GenerateContentExecutionResult`、`Phase3ExecutionResult`、`Phase3CandidatesResult`、`Phase3PlanResult`、`Phase3RunPlanResult` 及全部数据模型。`Phase3DedupScope/Phase3PlanMode`（CLI 选项 Literal）留 M5。
- 各命令的**核心函数**（壳删掉后 cli 直接调）：`run_fetch_products`（→`FetchProductsExecutionResult`，字段 `success_count/failed_count/skipped_count/run_status/progress_ref/total_products`）、`build_generate_content_outputs`、`run_phase3`（publish-note，结果 `.publish_result["success"]`）、`build_phase3_candidates`、`build_phase3_plan`、`run_phase3_plan`（→`Phase3RunPlanResult`，字段 `count_selected/count_attempted/count_succeeded/count_failed`）。
- `products/fetch.py`、`content/generate.py`、`phase3.py` 底部各有 `main()/__main__`（print json + SystemExit）——死入口，删。

**涉及文件**：

新增：
- `src/xhs_poster/logging.py` — 极简日志 seam（M8 再扩 --verbose/分级/trace）：
  ```python
  from __future__ import annotations
  import sys

  def log_summary(message: str) -> None:
      """面向人的单行结果/进度，输出到 stderr（systemd journal 可读）。"""
      print(message, file=sys.stderr, flush=True)

  def log_error(message: str) -> None:
      print(message, file=sys.stderr, flush=True)
  ```

改写：
- `cli.py`：
  - 删 `emit_json` 与 `import json`；加 `from .logging import log_summary, log_error`。
  - 删 `build_*_payload` 的 import，改为 import 上述**核心函数**（`from .products.fetch import run_fetch_products`、`from .content.generate import build_generate_content_outputs`、`from .phase3 import run_phase3, build_phase3_candidates, build_phase3_plan, run_phase3_plan`）。
  - **退出码 + 汇总策略移进各命令体**（这是 CLI 策略，不是领域逻辑）。统一形态：
    ```python
    try:
        result = <核心函数>(...)
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    log_summary(f"[{cmd}] <人读汇总>")
    raise typer.Exit(code=<0/1>)
    ```
  - 各命令退出码：
    - `fetch-products`：`success_count==0 → 1`，否则 `0`；登录失效 `2`。`--images-per-product` 的 `warnings.warn` 从删掉的 build 包装挪进本命令体。
    - `generate-content`：成功 `0`，异常 `1`（无登录路径）。
    - `publish-note`（`run_phase3`）：`publish_result["success"] → 0`，否则 `1`；登录 `2`。
    - `list-publish-candidates` / `plan-publish`：成功 `0`，异常 `1`。
    - **`run-publish-plan`（发现 B）**：`count_succeeded >= 1 → 0`；`count_attempted == 0 → 0`（无 pending，no-op，照常打汇总）；`count_attempted > 0 且 count_succeeded == 0 → 1`；登录失效 `2`；其他异常 `1`。
  - 汇总文案示例（按领域模型实际字段写）：`[fetch-products] 就绪 N / 失败 M / 跳过 K，状态=complete`；`[run-publish-plan] {count_succeeded}/{count_attempted} 成功，{count_failed} 失败`；`[plan-publish] 计划 {count_selected} 篇 → publish-plan.json`。
  - `APP_HELP` 去掉"输出为 JSON，便于脚本或下游消费"，改"输出为人读日志（stderr）+ 退出码（0 成功 / 1 失败 / 2 登录态失效）"。
- `products/fetch.py`：删 `build_fetch_products_payload` 与 `main()/__main__`；删 `FetchProductsResult/SkillError` 的 import；保留 `run_fetch_products`。顺带清理 `FetchProductsExecutionResult` 里 M3 遗留的 `today_pool_path` 字段（与新文件名不符）：日志用得到就改名 `products_path`，用不到就删。
- `content/generate.py`：删 `build_generate_content_payload` 与 `main()/__main__`；删 `GenerateContentResult/SkillError` import；保留 `build_generate_content_outputs`。同样处理 `image_semantic_facts_path` 遗留字段。
- `phase3.py`（**最小改动**）：删 4 个 `build_phase3_*_payload` 与 `main()/__main__`；删 `*Success/SkillError` import；**核心函数 `run_phase3/build_phase3_candidates/build_phase3_plan/run_phase3_plan` 保持不动**（M5 重写）。
- `models.py`：删上述 7 个壳模型。确认删后无残留 import。
- `CLAUDE.md`：架构块加 `logging.py` 一行；`cli.py` 注释"输出全部为 JSON（待 M4 改造）"→"人读日志 + 退出码"；Coding Conventions 第 87 行"CLI 子命令均通过 emit_json() 输出，exit code 0=成功，2=未登录/失败"→"输出人读日志到 stderr + 退出码（0 成功 / 1 失败 / 2 登录态失效；publish 批量 ≥1 成功即 0）"。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 绿；`uv run ruff check src/xhs_poster` 过；
- `grep -rnE "emit_json|SkillError|Phase3Success|Phase3CandidatesSuccess|Phase3PlanSuccess|Phase3RunPlanSuccess|FetchProductsResult|GenerateContentResult" src/xhs_poster` 无残留；
- `uv run xhs-poster generate-content >/tmp/o 2>/tmp/e; echo $?` —— 缺 `products.json` 时退出码 `1`、**stdout 无 JSON**、stderr 有一行人读错误；
- `uv run xhs-poster fetch-products --help` 等 `--help` 正常；
- run-publish-plan 退出码逻辑：因不真发，验收 = 审 cli 里 `count_succeeded/count_attempted` 的映射 + 一个最小 pytest（构造 `Phase3RunPlanResult` 三种计数组合断言退出码），确认"≥1 成功→0、全失败→1、无 pending→0"。

**回滚**：`git revert` 本里程碑两笔提交。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿 + `generate-content` 缺数据时退出码/无 JSON 验证通过
- [ ] grep 无 emit_json/壳模型残留
- [ ] git 提交（代码）：`M4：输出契约改日志+退出码，删 emit_json/JSON 壳，修 publish 部分失败退出码（发现 B）`
- [ ] 进度表提交：`进度表：M4 ✅`（记录代码提交 hash）
- [ ] **展开 M5 的详细小节**（这是重写支点：先实地勘察 `phase3.py`/`merchant.py`/`PublishPage` 再展开）
- [ ] 若发现新隐藏耦合，更新 `memory/refactor-v2.md`
- [ ] 提示用户 `/clear` 继续 M5

---

## M5 — 发布会话复用 + publish 命名落位

**目标**：把"每篇发布冷启动一次浏览器"改为"整批共享一个浏览器会话"，拿到性能首跳；同时把 publish 链路从 `phase3.py` / `merchant.py`(发布部分) 重写落位到 `publish/` 子包并完成语义命名（发现 F：留到重写时一起做，不给将被重写的代码白搬）。**本里程碑只做会话复用 + 命名/结构落位，不动死 sleep（留 M6）、不加反检测间隔（留 M7）、不丰富证据/trace（留 M8）。** 行为对外等价（仍能发布、仍写计划/账本、仍能续传），冒烟全绿。

> ⚠️ **会话复用 vs 风控权衡（发现 C）**：单一长会话高频发 20 篇的指纹 ≠ 每篇独立冷启动。M5 预留 `publish_session_recycle_every` 配置（每发 N 篇重建一次会话；很大=整批一会话，1=退化回每篇一会话），默认值上线后按风控反馈调。这是旋钮不是非此即彼。

**前置状态**：M4 已完成（8934e3b）。当前发布链路实测形态（务必先懂，已勘察）：
- `cli.py` 命令 `publish-note`→`run_phase3`、`list-publish-candidates`→`list_phase3_candidates`、`plan-publish`→`build_phase3_plan`、`run-publish-plan`→`run_phase3_plan`，全部 import 自顶层 `phase3.py`。
- **冷启动根因**：`run_phase3_plan`（`phase3.py:544`）循环 `pending_items`，每篇调 `run_phase3`（`phase3.py:448`），后者每篇 `with merchant_context(...)`（`:483`）重启 Chromium + `open_product_list_page`（`:486`）重进列表页。20 篇 = 20 次冷启动。
- **续传已有雏形（保留强化）**：`run_phase3_plan` 每篇 `save_publish_plan` + 失败 `append_phase3_record`；批前 `reconcile_publish_plan_with_records`（`phase3.py:417`）。
- **页面对象边界**：`merchant.py`（1199 行）含 `ProductDetailPage`(抓图)、`ProductListPage`(**fetch+publish 共用**，`:422`)、`PublishPage`(发布，`:528`)。`list_page.open_publish_page(product_id)`(`:501`) 返回 `PublishPage`。`ProductListPage` 仍被 `products/fetch.py` import，**不能整体搬走**——M5 把 `PublishPage` 抽到 `publish/page.py`，`ProductListPage`/`ProductDetailPage` 留 `merchant.py`（或后续里程碑再议）。
- **publish 链路死 sleep 仍在 `PublishPage`**（`merchant.py` 内 `wait_for_timeout`），M5 搬运时**原样保留**，M6 再改条件等待。

**涉及文件**：

新增子包（空 `__init__.py`，不写 `__all__`）：
- `src/xhs_poster/publish/__init__.py`
- `src/xhs_poster/publish/plan.py` — 候选/计划/续传：搬 `load_contents_bundle`/`list_phase3_candidates`/`build_phase3_plan`/`load_publish_plan`/`save_publish_plan`/`reconcile_publish_plan_with_records` 及计划相关 helper。
- `src/xhs_poster/publish/records.py` — 账本：搬 `load_phase3_daily_records`/`save_phase3_daily_records`/`append_phase3_record`/`save_phase3_artifacts`、`_save_json_atomic`。
- `src/xhs_poster/publish/session.py` — **会话复用核心（新写）**：`PublishSession` 负责 `merchant_context` + `open_product_list_page` + `ProductListPage` **只初始化一次**；提供 `publish_one(item)`（每篇独立 tab 开 PublishPage→上传→填写→话题→绑品→发布→校验→失败打证据→关 tab）；`run_publish_plan(...)` 改为：建会话 → for pending → publish_one → 即时写 plan/records → （M7 才加 sleep）→ `recycle_every` 到点重建会话 → 关浏览器 → 汇总。
- `src/xhs_poster/publish/page.py` — 从 `merchant.py` 抽出 `PublishPage`（连同其 `wait_for_timeout` 原样）。`merchant.py` 改为 `from .publish.page import PublishPage` 或反向，**注意 `ProductListPage.open_publish_page` 与 `PublishPage` 的循环依赖**：建议 `open_publish_page` 改为返回 page 句柄、由 session 侧构造 `PublishPage`，解开环。

改写：
- `cli.py`：命令重命名 `run-publish-plan`→`publish`、删 `publish-note` 与 `list-publish-candidates`（信息并入 `plan-publish` 输出/日志，见 refactor §4.7）；import 改自 `publish/`。
- `config.py`：`phase3_records_dir/path`→`publish_records_dir/path`，路径 `phase3/<date>/publish-records.json`→`publish/<date>/records.json`；`phase3_artifacts_dir`→证据目录 `publish/<date>/evidence/`（结构细化留 M8，M5 先改路径名）；`phase3_published_path` 评估是否更名；加 `publish_session_recycle_every`（int，默认很大=整批一会话）。
- `models.py`：`Phase3*` 系列（`Phase3Candidate`/`Phase3PlanItem`/`Phase3PlanResult`/`Phase3PublishRecord`/`Phase3DailyRecords`/`Phase3RunPlan*`/`Phase3ExecutionResult`/`Phase3DedupScope`/`Phase3PlanMode` 等）改 publish 语义名；`grep -rn "phase3\|Phase3" src/` 清零。
- `originality.py`：保持，仅更新 import 名（若引用 Phase3* 模型）。
- `deploy/`（若已存在）/`CLAUDE.md`：命令名 `run-publish-plan`→`publish`、数据路径同步。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 绿；`uv run ruff check src/xhs_poster` 过；`uv run pyright src/xhs_poster` 零新增；
- `grep -rn "phase3\|Phase3" src/xhs_poster` 无残留；`merchant.py` 不再含 `PublishPage`；
- `uv run xhs-poster --help` 出现 `publish`、无 `run-publish-plan`/`publish-note`/`list-publish-candidates`；
- **会话复用证据**：`publish --count 2`（或对着测试桩/跑到"点发布前"为止，发现 D：不为测耗时真发线上）确认整批只 `sync_playwright()`/`open_product_list_page` 一次（日志或断点验证），单篇失败隔离、即时写 plan/records、可续传；
- `fetch-products` 仍能 `from ..merchant import ProductListPage` 跑通（products 侧未被打断）。

**回滚**：`git revert` 本里程碑提交。

**收尾清单**：
- [ ] smoke + ruff + pyright 绿；`phase3/Phase3` grep 清零
- [ ] 会话复用与续传实测/桩验通过（不真发污染线上）
- [ ] git 提交：`M5：发布链路重写落位 publish/ + 整批共享浏览器会话（发现 C 预留 recycle 旋钮）`
- [ ] 进度表 M5 → ✅；**展开 M6 的详细小节**（M6 死 sleep 改条件等待，长在 M5 的 publish/page.py 上）
- [ ] 若发现新隐藏耦合（尤其 PublishPage↔ProductListPage 循环依赖的解法），更新 `memory/refactor-v2.md`
- [ ] 提示用户 `/clear` 继续 M6

---

## M6–M10

概览见 `docs/refactor-plan.md` §9。每个里程碑在其**前一个里程碑收尾时**展开为上面的详细格式。

> **为何仍不一次展开 M5–M10**：M5 是发布链路的重写支点，其细节依赖对 `phase3.py`/`merchant.py`(`PublishPage`/`ProductListPage`)/选择器的实地勘察（勘察本身是 M5 工作的一部分）；M6（条件等待）、M8（证据/trace）、M9（断点续传/自愈）又全部长在 M5 重写出的 `publish/session.py` 结构上。在 M5 代码尚不存在时细化它们，等于写一批很快返工的规格。M10（systemd）结构相对稳定，可在 M5 后按需先做"设计意图"级预写。
