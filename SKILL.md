---
name: xiaohongshu-product-poster
description: |
  小红书商家后台自动化工具：从商品管理拉取商品、生成笔记内容、发布笔记。
  三阶段独立执行（prepare-products 准备 → generate-content 内容 → phase3 编排/发布），通过 JSON 文件传递数据。
  prepare-products 支持断点续传与收敛执行，会实时写出 phase1-state.json 并增量更新 today-pool.json。
  使用 CLI `uv run xhs-poster` 执行。需商家端登录；generate-content 依赖 LLM。
  使用场景：拉取商品主图、生成种草文案、按需查看候选、生成发布计划、执行单篇或批量发布。
  支持 macOS 登录后导出 auth-state，并在云服务器导入后无头运行。
---

# 小红书商品笔记自动发布

## 何时使用

当用户要做以下事情时，使用本 skill：

- 从小红书商家后台抓商品和主图
- 生成商品笔记内容
- 发布 1 篇或多篇笔记
- 查看还能发布哪些候选
- 在 macOS 和云服务器之间迁移商家端登录态

入口命令为 `uv run xhs-poster ...`。

## 核心规则

AI 使用本 skill 时，默认原则是：

1. 先检查已有产物
2. 只补跑缺失阶段
3. phase3 必须先确保当天已有发布计划，再执行发布
4. 不要因为用户说“发布”就默认重跑 phase1 或 phase2

除非用户明确要求“重新抓商品”“重新下载图片”“重新生成文案”“从头重跑”，否则优先复用 `xiaohongshu-data/` 下已有结果。

### 执行方式

- 在云服务器或其他容易断开交互会话的环境中，长时任务默认使用 `systemd-run --user` 启动，不要把长任务直接挂在当前 openclaw / SSH 前台会话里
- 适合用 transient service 的命令：`prepare-products`、`generate-content`、`plan-publish`、`run-publish-plan`
- 不适合用 transient service 的命令：`login merchant` 这类需要人工交互、扫码或浏览器前台操作的命令；这类命令默认前台执行
- 若用户没有明确要求前台执行，AI 在执行长时任务时应优先选择 `systemd-run --user --same-dir --collect`
- AI 执行 transient service 后，应告知用户如何用 `systemctl --user status <unit>` 和 `journalctl --user -u <unit>` 查看状态与日志
- 若目标机器没有 `systemd --user`、没有 user bus、或 `systemd-run --user` 不可用，再退回 `tmux` / `screen` / `nohup`

建议 unit 命名：

- `xhs-prepare-products`
- `xhs-generate-content`
- `xhs-plan-publish`
- `xhs-run-publish-plan`

示例：

```bash
systemd-run --user --unit=xhs-prepare-products --same-dir --collect \
  uv run xhs-poster prepare-products --limit 10 --images-per-product 3

systemctl --user status xhs-prepare-products
journalctl --user -u xhs-prepare-products -f
```

### 阶段判断

- phase1 已完成：`xiaohongshu-data/today-pool.json` 存在，且 `today-pool.json.date == 今天`，并满足目标商品数量与图片完整性要求
- phase2 已完成：`xiaohongshu-data/contents.json` 存在，且 `contents.json.date == 今天`，并满足目标商品数量与草稿数量要求
- phase1 进度与恢复检查点：`xiaohongshu-data/phase1-state.json`
- phase3 发布计划：`xiaohongshu-data/publish-plan.json`
- phase3 当日发布记录：`xiaohongshu-data/phase3/YYYY-MM-DD/publish-records.json`

### 补跑规则

- 缺 `today-pool.json`，或 `today-pool.json.date != 今天`：执行 `prepare-products`
- 有今日 `today-pool.json`，但缺 `contents.json`，或 `contents.json.date != 今天`：执行 `generate-content`
- `today-pool.json` 与 `contents.json` 都可用，且两者日期均为今天：直接进入发布相关命令
- 只有用户明确要求刷新商品池或重下图片时，才重跑 `prepare-products`
- 只有用户明确要求重写文案时，才重跑 `generate-content`
- `prepare-products --limit 10` 的语义是“尽量得到 10 个成功商品”，不是“只检查前 10 个商品后就停止”

日期判断原则：

- 这里的“今天”指执行命令当日，本地读取 JSON 中的 `date` 字段进行判断
- 不要仅凭文件存在就认定 phase1 或 phase2 已完成
- 若 `today-pool.json` 或 `contents.json` 缺少 `date` 字段、结构损坏、或日期不是今天，都应视为对应阶段未完成
- phase2 除了要求 `contents.json.date == 今天`，还要求它覆盖当前今日商品池；若商品集合明显不一致，也应视为 phase2 未完成并重新生成

### 数量完整性规则

默认目标：

- phase1 每天准备 `10` 个商品
- 每个商品最多准备 `3` 张主图
- phase2 为每个商品生成 `5` 篇草稿

默认判断标准：

- phase1 达标：`today-pool.json.date == 今天`，`today-pool.json.status == "complete"`，且有 `10` 个成功商品；每个商品至少有 `1` 张可用图片，最多保留 `3` 张
- phase2 达标：`contents.json.date == 今天`，`contents.json` 覆盖这批商品，且每个商品至少有 `5` 篇草稿
- 理想总量：`10` 个商品，`50` 篇内容

降级规则：

- 若当天后台实际可成功准备的商品少于 `10` 个，则以 phase1 实际成功商品数作为 phase2 的生成基数
- phase1 在当前商品列表中应继续补位，遇到 `0` 张主图商品时跳过，并继续尝试后续商品，直到成功商品达到目标数量或候选耗尽
- 若 phase1 最终成功商品为 `M` 个，则 phase2 的完整目标变为 `M * 5` 篇
- AI 不应在 phase1 已经部分成功时，因为未达到理想 `10` 个商品而盲目重跑整个流程；应优先查看 `phase1-state.json` 判断是继续收敛还是接受当天实际数量

### 主图数量不足时的处理

默认最多下载每个商品前 `3` 张主图，但需要按下面规则处理：

- 若商品主图多于 `3` 张，只下载前 `3` 张
- 若商品主图是 `1` 或 `2` 张，则按实际数量下载，并仍视为 phase1 成功商品
- 只有当商品 `0` 张主图时，才视为该商品不完整
- `0` 张主图的商品应在 `phase1-state.json` 中保留失败或不完整状态，并记录原因
- phase2 和 phase3 只排除 `0` 张主图的商品，不排除只有 `1` 或 `2` 张主图的商品

AI 的判断原则：

- 不把“只有 1 张或 2 张主图”的商品误判为失败商品
- 只把“0 张主图”的商品排除在成功商品集合之外
- 不为了补齐单个异常商品而默认推翻当天整个 phase1 结果
- 若多数商品已成功，则继续基于成功商品推进 phase2 和 phase3
- 只有当成功商品数过低、无法满足用户当前发布目标，或用户明确要求补齐时，才建议继续执行 `prepare-products`

建议响应方式：

- 若 phase1 成功商品数达到当天可用目标，则继续后续阶段
- 若有少量商品因 `0` 张主图被跳过，应告知用户“部分商品因没有可用主图未纳入今日商品池”
- 若成功商品数不足以支撑当前发布需求，再告知用户当前可用商品数，并说明是否需要继续补抓或重新生成

## 发布规则

phase3 在概念上分成两步：

1. 编排：先确定今天要发哪些内容、按什么顺序发
2. 发布：再执行编排结果

对 AI 来说，phase3 必须严格分成两步：

- `plan-publish` 生成并保存当天 `publish-plan.json`
- `run-publish-plan` 只负责执行当天已存在的发布计划
- `publish-note` 保留为底层调试命令，不作为 AI 的默认发布入口

说明：

- 即使底层实现对“无计划时自动补计划”保留兼容行为，AI 也不得依赖这个隐式行为
- AI 必须像处理 phase1 / phase2 一样，把“当天是否已有 `publish-plan.json`”视为单独的阶段检查

### 三个编排相关命令的职责

- `list-publish-candidates`
  - 只查看候选
  - 不生成计划
  - 不执行发布
- `plan-publish`
  - 生成待发布计划
  - 默认写入 `publish-plan.json`
  - 不传 `--count` 时，默认选择当天剩余全部可发布候选
  - 只做选择，不执行发布
- `run-publish-plan`
  - 执行已保存的发布计划
  - 会真实发布，并写入当日 `publish-records.json`

默认关系：

- 想知道“有哪些可以发”：用 `list-publish-candidates`
- 想显式查看并保存一份计划：用 `plan-publish`
- 想真正开始发：先确保当天已有计划，再用 `run-publish-plan`
- AI 执行 `plan-publish` 时，除非用户明确指定数量，否则不需要自己计算 `count`

### 发布 1 篇

当用户说“发布 1 篇笔记”时：

1. 检查 `today-pool.json`
2. 检查 `contents.json`
3. 检查两者 `date` 是否为今天；若不是今天，先补跑缺失阶段
4. 检查 `contents.json` 是否覆盖当前今日商品池；若不一致，先执行 `generate-content`
5. 检查 `publish-plan.json` 是否存在且是今天的计划；若不是，先通过 `systemd-run --user` 执行 `uv run xhs-poster plan-publish --mode sequential`
6. 如需先确认候选，执行 `uv run xhs-poster list-publish-candidates`
7. 再通过 `systemd-run --user` 执行 `uv run xhs-poster run-publish-plan --mode sequential --count 1`
8. 不要默认执行 `prepare-products`
9. 不要默认执行 `generate-content`
10. 不要默认直接调用 `publish-note`

### 发布 N 篇

当用户说“发布 N 篇笔记”且 `N > 1` 时：

1. 检查 `today-pool.json`
2. 检查 `contents.json`
3. 检查两者 `date` 是否为今天；若不是今天，先补跑缺失阶段
4. 检查 `contents.json` 是否覆盖当前今日商品池；若不一致，先执行 `generate-content`
5. 检查 `publish-plan.json` 是否存在且是今天的计划；若不是，先通过 `systemd-run --user` 执行 `uv run xhs-poster plan-publish --mode sequential`
6. 如需先确认候选，执行 `uv run xhs-poster list-publish-candidates`
7. 再通过 `systemd-run --user` 执行 `uv run xhs-poster run-publish-plan --mode sequential --count N`
8. 不要无必要地手工循环多次 `publish-note`

### 仅生成计划

当用户说“生成今天的发布计划”“先排一下今天剩余可发内容”时：

1. 检查 `today-pool.json`
2. 检查 `contents.json`
3. 检查两者 `date` 是否为今天；若不是今天，先补跑缺失阶段
4. 检查 `contents.json` 是否覆盖当前今日商品池；若不一致，先执行 `generate-content`
5. 默认通过 `systemd-run --user` 执行 `uv run xhs-poster plan-publish --mode sequential`
6. 不要默认自己先计算 `count`
7. 只有用户明确说“生成 5 条计划”这类数量要求时，才传 `--count N`

### 去重与上限

- 默认去重范围：`today`
- 默认不重复发布同一天已成功发布过的 `(product_id, angle)`
- 以 `phase3/YYYY-MM-DD/publish-records.json` 中 `status == "success"` 的记录作为去重依据
- 当天成功发布数达到 50 后，不再执行任何发布命令，只告知用户“今日发布已达 50 篇上限”
- 若用户要求发布 `N` 篇，但当前 eligible 候选少于 `N`，默认发布可用数量，并明确告知实际发布数量
- 若当前没有可发候选，直接告知用户，不要默认回头重跑 phase1 或 phase2

## 用户意图映射

| 用户表达             | 默认动作                                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------------------------- |
| “发布 1 篇笔记”      | 先确保当天已有 `publish-plan.json`；若没有则先用 `systemd-run --user` 执行 `plan-publish`，再用 `systemd-run --user` 执行 `run-publish-plan --count 1`    |
| “发布 5 篇笔记”      | 先确保当天已有 `publish-plan.json`；若没有则先用 `systemd-run --user` 执行 `plan-publish`，再用 `systemd-run --user` 执行 `run-publish-plan --count 5`    |
| “继续发布几篇”       | 基于现有候选和发布账本继续增量发布                                                                        |
| “看看有哪些可以发”   | 执行 `list-publish-candidates`                                                                            |
| “先生成一个发布计划” | 用 `systemd-run --user` 执行 `plan-publish`；默认覆盖当天剩余全部可发布候选                              |
| “重新抓商品”         | 用 `systemd-run --user` 执行 `prepare-products`，必要时用 `--force-download`                              |
| “重新生成文案”       | 用 `systemd-run --user` 执行 `generate-content`                                                           |
| “重新下载图片”       | 用 `systemd-run --user` 执行 `prepare-products --force-download`                                          |
| “从头跑一遍”         | 才允许按阶段全量重跑                                                                                      |

解释：

- “发布”默认指先检查当天发布计划；若当天没有计划，先执行 `plan-publish` 再发布
- “继续发布”默认表示基于现有产物做增量操作
- “查看”“看看”“列一下”默认是只读，不直接发布
- `publish-note` 不是 AI 默认入口，而是底层调试命令
- 长时写操作默认通过 `systemd-run --user` 启动，不依赖当前交互会话持续存活

## 常用命令

```bash
uv run xhs-poster auth probe merchant
systemd-run --user --unit=xhs-prepare-products --same-dir --collect uv run xhs-poster prepare-products --limit 10 --images-per-product 3
systemd-run --user --unit=xhs-generate-content --same-dir --collect uv run xhs-poster generate-content --keyword 抓夹 --contents-per-product 5
uv run xhs-poster publish-note --angle 1
uv run xhs-poster list-publish-candidates
systemd-run --user --unit=xhs-plan-publish --same-dir --collect uv run xhs-poster plan-publish --mode sequential
systemd-run --user --unit=xhs-plan-publish --same-dir --collect uv run xhs-poster plan-publish --mode sequential --count 5
systemd-run --user --unit=xhs-run-publish-plan --same-dir --collect uv run xhs-poster run-publish-plan --mode sequential --count 5
```

## 只在需要时阅读

- 详细使用说明：`README.md`
- 快速命令参考：`QUICKREF.md`
- 数据格式、账本、编排细节：`REFERENCE.md`
- 仓库约束与开发规范：`AGENTS.md`

仅当当前任务需要更多细节时，再读取这些文件；默认不要把所有文档都当作执行前提。
