# Phase2 图片语义分析接入与缓存实施方案

## Summary

在 `generate-content` 流程中新增“图片语义分析”子阶段，先使用 Kimi 视觉模型对商品主图做结构化语义提取，再把语义结果与现有的本地图像事实、趋势分析、历史风格参考一起送入文案生成提示词，提升标题/正文与主图的一致性。

已核实的前提：

- 当前代码只做了浅层图片事实提取，主要是颜色、明暗、尺寸以及从商品名硬匹配出的元素词，不能支撑可靠的图文对齐。
- 实测 `moonshot-v1-8k-vision-preview` 可正常接收图片并返回结构化分析结果。
- 当前账号可见的模型列表包含视觉模型；`moonshot-v1-8k` 不支持图片输入。
- 已确认的默认决策：
  - 缓存键使用图片内容 `SHA256`
  - 默认视觉模型使用 `moonshot-v1-8k-vision-preview`
  - 图片语义分析失败时，phase2 降级继续，不阻断产出

## Implementation Changes

### 1. 新增图片语义分析缓存层

新增一个独立模块，负责：

- 读取单张图片并计算 `sha256`
- 根据 `sha256` 命中本地缓存，避免重复请求
- 未命中时调用 Kimi 视觉模型生成语义结果
- 将结果以原子写入方式持久化到新的缓存文件，例如 `xiaohongshu-data/image-semantic-facts.json`

缓存结构按“图片”而不是按“商品”组织，单条记录至少包含：

- `image_sha256`
- `path`
- `width`
- `height`
- `model`
- `analyzed_at`
- `status`
- `summary`
- `category`
- `colors`
- `material_guess`
- `visible_elements`
- `product_elements`
- `background_elements`
- `style_mood`
- `scene_guess`
- `confidence_notes`
- `raw_text` 或 `raw_payload` 的最小保留字段

默认只缓存成功结果；失败结果也记录简要错误和时间戳，便于当天避免无限重试。

### 2. 扩展 phase2 的事实构建

在现有 `ProductImageFacts` / `ProductFactsSnapshot` 基础上增加“语义事实”输入：

- phase2 读取每个商品的 1-3 张图
- 逐张生成或读取缓存的语义分析结果
- 聚合成商品级语义摘要，再写入 `product-facts.json`
- 原有 `image-facts.json` 继续保留，作为低成本兜底数据源

商品级聚合规则在方案中固定，避免实现时再做决策：

- `colors`：按图片语义颜色去重汇总，保留前 5 个
- `product_elements`：汇总所有图片中明确属于商品本体的元素
- `background_elements`：单独保留，不进入商品卖点字段
- `material_guess`：按出现频次取主值，冲突时保留最多 2 个
- `style_mood` / `scene_guess`：按频次去重，保留前 3 个
- `summary`：生成一个商品级短摘要，明确“图中商品是什么、主要视觉特征是什么”

### 3. 更新文案生成提示词

修改 `content_gen` 的 prompt payload，让 LLM 明确以图片语义为主、商品名为辅：

- 在 `product` 段新增图片语义字段
- 在 `rules` 中加入约束：
  - 标题和正文必须优先依据图片语义事实
  - 不得把 `background_elements` 写成商品属性
  - 不得根据背景道具虚构使用场景或功能
  - 当图片语义与商品名冲突时，优先保守描述，不编造细节
- 保留现有 `history_style_refs` 和 `hot_notes_analysis`，但它们只能修饰表达方式，不能覆盖图片事实

新增的 prompt 输入应至少包括：

- `semantic_summary`
- `semantic_category`
- `semantic_colors`
- `semantic_materials`
- `semantic_product_elements`
- `semantic_style_moods`
- `semantic_scene_guesses`
- `semantic_confidence_notes`

### 4. 增加独立视觉模型配置

在配置层新增视觉分析专用配置，不复用当前文本模型配置：

- `XHS_POSTER_VISION_LLM_API_KEY`
- `XHS_POSTER_VISION_LLM_BASE_URL`
- `XHS_POSTER_VISION_LLM_MODEL`
- 默认值回退到：
  - API key 未单独设置时，复用 `LLM_API_KEY`
  - base URL 未单独设置时，复用 `LLM_BASE_URL`
  - model 默认 `moonshot-v1-8k-vision-preview`

这样文本生成仍可继续使用现有 `moonshot-v1-8k`，图片理解走视觉模型，不互相干扰。

### 5. 失败与降级行为

图片语义分析失败时按商品级降级，不中断当天 phase2：

- 某张图失败：
  - 记录 warning 和缓存失败记录
  - 其它图继续分析
- 某商品所有图都失败：
  - 该商品继续走现有本地图像事实 + 文本生成逻辑
  - `phase2_report.json` 和 `contents.json.warnings` 中记录“semantic_analysis_unavailable”
- 只有当视觉接口配置错误导致所有商品都无法分析时，也不让整个 phase2 报错；仍按当前旧逻辑产出，并在总报告中标记全局 warning

## Public Interfaces / Data Changes

需要明确调整的接口与数据产物：

- `config.py`
  - 新增视觉模型配置项
  - 新增 `image_semantic_facts_path`
- `models.py`
  - 新增单图语义分析模型
  - 新增商品级语义聚合模型
  - 扩展 `ProductFactsSnapshot`，加入图片语义字段
- `phase2.py`
  - 在图片事实提取后、文案生成前插入语义分析步骤
  - 把语义缓存路径和 warning 信息写入 phase2 输出
- `content_gen.py`
  - 扩展 prompt payload
  - 不改变现有 CLI 参数接口
- 新增数据文件
  - `xiaohongshu-data/image-semantic-facts.json`

CLI 行为不新增必选参数；`uv run xhs-poster generate-content ...` 仍是同一个入口，语义分析作为默认内置能力。

## Test Plan

### 1. 能力验证

- 用单张已有商品图调用视觉模型，确认：
  - 请求成功
  - 返回可解析 JSON
  - 字段完整
- 验证默认视觉模型为 `moonshot-v1-8k-vision-preview`

### 2. 缓存命中与去重

- 首次运行 `generate-content` 时，对未分析图片发起请求并写入 `image-semantic-facts.json`
- 第二次运行同一批图片时，不再重复请求视觉接口
- 把一张图片复制到新路径但内容不变，验证仍能命中同一 `sha256`
- 替换图片内容后，验证会触发重新分析

### 3. 降级路径

- 人为配置错误的视觉模型名，验证 phase2 仍能产出 `contents.json`
- 单个商品图片分析失败时，其它商品仍正常生成
- `phase2_report.json` 中出现可定位的 warning

### 4. 提示词效果

- 抽样对比修改前后同一商品的文案：
  - 标题是否提到主图实际可见颜色/元素
  - 正文是否避免把背景物体误写成商品卖点
  - 商品名与图片视觉语义冲突时是否保持保守表述

### 5. 兼容性

- 未配置视觉专用环境变量时，复用现有 `MOONSHOT_API_KEY` 仍可运行
- 未配置任何 API key 时，仍保留当前模板/降级逻辑，不引入新的硬失败

## Acceptance Criteria

满足以下条件即视为完成：

- `generate-content` 默认会先尝试图片语义分析
- 已分析过的图片不会重复消耗 token
- 视觉分析失败不会阻断 phase2
- 文案生成 prompt 明确使用图片语义结果
- 新生成的标题/正文与商品主图的颜色、元素、风格描述明显更贴合
- 不改变现有 CLI 使用方式，不破坏 phase1/phase3 数据流

## Assumptions

- 当前视觉接口继续使用 Moonshot 的 OpenAI 兼容 `chat/completions`
- 请求图片采用 `image_url` + base64 data URL 方式
- 视觉模型返回允许是自然语言包裹 JSON；实现时应做 JSON 提取和容错解析
- 本次实施不新增单独的 CLI 子命令，语义分析集成在 phase2 内部
- 本次实施不引入数据库，缓存仅落地到 `xiaohongshu-data/` JSON 文件
