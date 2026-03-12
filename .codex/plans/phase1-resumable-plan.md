# Phase1 收敛式执行与 Skill 文档同步改造计划

## Summary
将 `prepare-products` 从“全量一次性成功才落盘”的批处理，改成“可恢复、可收敛、可观测”的阶段任务，并把这些行为同步更新到仓库内的 skill 文档与使用说明中，避免代码行为和 skill 描述脱节。

默认方案：
- 以状态文件作为实时进度通道，不默认改 stdout 为事件流。
- 新增 Phase1 检查点文件，支持断点续传、增量收敛和未来扩展更多商品信息字段。
- `today-pool.json` 支持 `partial/complete` 状态，允许部分成功产出。
- 每次执行优先复用已完成成果，只处理缺失或失败项。
- 同步更新 `AGENTS.md` / 仓库说明 / 相关 skill 描述，确保 CLI 行为、产物、恢复语义一致。

## Key Changes
### 1. 阶段产物改为“状态驱动”
新增 `xiaohongshu-data/phase1-state.json`，作为 Phase1 的唯一执行检查点与进度来源。

状态文件至少包含：
- `date`
- `run_status`: `running | partial | complete | failed`
- `started_at`, `updated_at`, `completed_at`
- `target_total`, `processed_count`, `success_count`, `failed_count`, `skipped_count`
- `products`: 以 `product_id` 为 key 的商品状态记录

每个商品状态记录至少包含：
- `product_id`, `product_name`
- `list_discovered`
- `fetch_status`: `pending | in_progress | complete | failed`
- `attempt_count`
- `last_error`
- `updated_at`
- `artifacts.images.status`: `missing | partial | complete`
- `artifacts.images.paths`
- `artifacts.images.count`
- `artifacts.images.source`: `downloaded | existing_files`

`today-pool.json` 继续保留给 phase2/phase3 消费，但增加：
- `status`: `partial | complete`
- `generated_at`
- `failed_products`

默认行为：
- 每处理完一个商品就更新 `phase1-state.json`
- 每出现新的成功商品就重写 `today-pool.json`
- 全部完成后将两者状态收敛为 `complete`

### 2. 执行逻辑改为“发现-收敛-补齐”
`prepare-products` 的执行顺序调整为：

1. 校验登录态并进入商品列表页。
2. 获取本次列表页商品集合。
3. 读取已有 `phase1-state.json` 与 `today-pool.json`。
4. 按“当天商品集合”与历史状态做归并。
5. 对每个商品按收敛规则决定跳过、回填、重试或重新抓取。
6. 每个商品处理结束后立即落盘状态与 today-pool。
7. 最终输出整批汇总结果。

收敛规则：
- 当天列表里存在且 artifact 已完成的商品，直接跳过。
- 状态缺失但本地图片完整的商品，直接回填为完成态。
- 只有 artifact 不完整、历史失败、或 `--force-download` 时才进入详情页。
- 单商品失败只记录状态，不阻断整批。

### 3. 断点续传按 artifact 完成度设计
Phase1 建模为“商品 artifact 采集任务”，不是“单纯下图”。

v1 artifact：
- `images`

未来可扩展：
- `title`
- `price`
- `category`
- `inventory`
- 其他商品信息字段

规则：
- 商品是否已完成，由所需 artifact 集合决定。
- 当前命令的完成条件是 `images.count >= images_per_product`。
- 未来扩展字段时，通过 artifact 级完成度补抓，不推翻断点续传模型。

### 4. 页面抓取策略改为“业务就绪”
将商品详情页等待策略从 `networkidle` 改成业务元素就绪。

建议：
- `goto(..., wait_until="domcontentloaded")`
- 再等待“图文信息”tab 或关键页面标记
- 图片提取前做短超时等待和必要延迟
- 对单商品超时、下载失败、图片不足都记录 `last_error`

对于已有完整图片的商品：
- 默认不再进入详情页
- 仅在状态不完整或 `--force-download` 时进入详情页

### 5. CLI 与返回约定
保留 `prepare-products` 入口和当前最终 JSON 输出模式。

新增/调整：
- stdout 仍只输出最终汇总 JSON
- 新增字段：
  - `status`: `ok | partial | error`
  - `run_status`: `complete | partial | failed`
  - `progress_ref`
  - `success_count`, `failed_count`, `skipped_count`
  - `failed_products`
- `today-pool.json` 可在部分成功时生成，且 `status=partial`
- phase2/phase3 默认接受 `partial` 的 `today-pool.json`，只消费成功商品

### 6. Skill 与说明文档同步更新
需要把这次改造同步到仓库内与 skill 使用相关的文档，至少覆盖以下内容：

更新内容：
- `prepare-products` 不再是“全成功才产出”，而是支持部分成功和断点续传
- 新增 `phase1-state.json` 的用途、位置、字段语义
- `today-pool.json` 现在可能为 `partial`
- 已完成商品默认跳过，`--force-download` 才强制重抓
- 云端/AI 场景下推荐通过状态文件轮询执行进度，而不是依赖 stdout 流事件

文档范围：
- 仓库根的 `AGENTS.md`
- 项目对外说明文档（若存在 README 或同类入口文档）
- 仓库内任何直接描述本 skill 使用方式、执行产物、阶段语义的说明文件

文档要求：
- 说明 CLI 行为变化和数据文件变化
- 说明“收敛式执行”的预期行为
- 说明恢复/重跑语义
- 保证文档描述与实际返回字段、文件路径、状态值完全一致

## Test Plan
1. 全新运行，无历史状态
- 全部成功
- 生成 `phase1-state.json` 和 `today-pool.json`
- 两者状态为 `complete`

2. 中途失败后重跑
- 前几个商品已成功并落盘
- 某商品详情页超时
- 再次运行时已完成商品被跳过，仅处理失败/未完成商品

3. 本地已有图片但无状态文件
- 能识别完整图片并回填状态
- 不进入详情页
- 正常生成 `today-pool.json`

4. `--force-download`
- 即使已有完整状态也重新抓取
- 覆盖图片并刷新 attempt/time/status

5. 部分成功
- `today-pool.json.status = partial`
- CLI 输出 `partial`
- phase2 可继续消费成功商品

6. 扩展兼容性
- 当前只要求 `images` 时逻辑成立
- 保留未来新增 artifact 的兼容能力

7. 文档一致性
- 文档中所有文件名、字段名、状态值、命令行为与实现一致
- 文档明确说明 AI/云端应轮询 `phase1-state.json`

## Assumptions
- 进度观测默认通过 `phase1-state.json` 轮询，不默认切换为 stdout 事件流。
- “已完成的不再操作”以“当天列表页仍存在该商品”为前提。
- `today-pool.json` 允许为 `partial`，下游按成功商品消费。
- 当前 v1 的完成条件仍是“每商品至少有 `images_per_product` 张主图”。
- skill 文档同步范围以仓库内现有说明文件为准；若存在多个入口文档，以 `AGENTS.md` 和主说明文档为优先同步对象。
