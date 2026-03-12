# Phase3 按日分块与计划驱动改造方案

## 目标结构

保留阶段1/2的当前快照，重构阶段3为“计划文件 + 按日记录文件”。

### 当前快照

- `xiaohongshu-data/today-pool.json`
- `xiaohongshu-data/contents.json`
- `xiaohongshu-data/phase1-state.json`

### 发布计划

- `xiaohongshu-data/publish-plan.json`

职责：

- 表示当前这轮/当天准备发什么
- 只负责计划，不记录真实发布过程

建议字段：

- `date`
- `mode`
- `dedupe_scope`
- `count_planned`
- `items`
- 每个 `item`：
  - `sequence`
  - `product_id`
  - `product_name`
  - `angle`
  - `angle_name`
  - `title`
  - `topic_keywords`
  - `status`: `pending | published | failed | skipped`
  - `published_at`
  - `error`

### 发布记录

合并原来的两个日志文件，改成按日分块：

- `xiaohongshu-data/phase3/2026-03-12/publish-records.json`

职责：

- 记录当天所有发布尝试
- 成功和失败都记录
- 作为当天去重与日上限判断依据

建议字段：

- `date`
- `records`
- 每条 `record`：
  - `attempted_at`
  - `product_id`
  - `product_name`
  - `angle`
  - `angle_name`
  - `title`
  - `topic_keywords`
  - `status`: `success | failed | skipped`
  - `error`
  - `publish_result`
  - `artifacts`
  - `dedupe_key`

## 行为规则

### 编排

- `plan-publish` 生成并写入 `publish-plan.json`
- 默认当天只编排一次
- 若已有计划，默认不覆盖
- 用户明确要求时，才允许重排

### 发布

- `run-publish-plan --count N` 只消费 `publish-plan.json` 中前 `N` 条 `pending` 项
- 每发布一条：
  - 计划项状态更新
  - 当日 `publish-records.json` 追加一条记录

### 去重

- 当天去重：读取当天 `publish-records.json` 中 `status == "success"` 的记录
- 历史去重：扫描历史日目录中的 `publish-records.json`，筛 `status == "success"`

### 日上限

- 今日是否达到 50 篇：直接数当天 `publish-records.json` 中 `status == "success"` 的记录数

## 为什么这样比现在更好

- 不再有 `publish-log.json` 和 `phase3-published.json` 的双写一致性问题
- 发布“计划”和“实际记录”职责分离，比“日志 + 账本”更清晰
- 按日分块后，当天最多 50 条，文件体量很小，AI 和脚本处理都简单
- “今天准备发什么”和“今天已经发了什么”两个问题有清晰的数据来源

## 建议的改造范围

- `config.py`
  - 新增 `publish_plan_path`
  - 新增 `phase3_records_dir` / 当日记录路径辅助函数
- `models.py`
  - 新增发布计划模型
  - 新增按日发布记录模型
- `phase3.py`
  - 删除对 `publish-log.json` / `phase3-published.json` 的依赖
  - 改成读写 `publish-plan.json` 和当日 `publish-records.json`
- `cli.py`
  - 更新 help 文案
- `SKILL.md` / `README.md` / `REFERENCE.md`
  - 改成“计划文件 + 当日记录文件”语义
