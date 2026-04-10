# 小红书商品笔记自动发布 - 参考文档

本文档提供数据格式、内容策略及当前已实现的编排/账本逻辑，供实现或扩展时参考。

---

## 数据格式

### phase1-state.json

`prepare-products` 的实时状态与断点续传检查点：

```json
{
  "date": "2026-03-12",
  "run_status": "partial",
  "started_at": "2026-03-12T09:20:00+08:00",
  "updated_at": "2026-03-12T09:23:10+08:00",
  "completed_at": "2026-03-12T09:23:10+08:00",
  "target_total": 10,
  "processed_count": 10,
  "success_count": 8,
  "failed_count": 2,
  "skipped_count": 5,
  "products": {
    "商品ID": {
      "product_id": "商品ID",
      "product_name": "商品名",
      "list_discovered": true,
      "fetch_status": "complete",
      "attempt_count": 1,
      "last_error": null,
      "updated_at": "2026-03-12T09:22:31+08:00",
      "artifacts": {
        "images": {
          "status": "complete",
          "paths": ["/path/to/1.jpg", "/path/to/2.jpg", "/path/to/3.jpg"],
          "count": 3,
          "source": "existing_files"
        }
      }
    }
  }
}
```

说明：

- `target_total` 表示本次希望收敛到的成功商品数
- `success_count` 表示当前已成功进入商品池的商品数
- phase1 会继续尝试后续候选商品补位，直到 `success_count >= target_total` 或当前候选耗尽

### today-pool.json

```json
{
  "date": "2026-03-10",
  "status": "partial",
  "generated_at": "2026-03-10T09:23:10+08:00",
  "products": [
    {
      "id": "商品ID",
      "name": "商品名称"
    }
  ],
  "images": {
    "商品ID": ["/path/to/1.jpg", "/path/to/2.jpg", "/path/to/3.jpg"]
  },
  "failed_products": [
    {
      "product_id": "失败商品ID",
      "product_name": "失败商品名称",
      "reason": "失败原因"
    }
  ]
}
```

说明：

- `today-pool.json.products` 只包含当前已成功准备的商品
- `prepare-products --limit N` 的语义是“尽量得到 N 个成功商品”
- 若前面的商品没有可用图片，phase1 会继续尝试后续商品补位
- 每个商品会下载商品主图全部图片 + 详情页图片全部图片，并优先原图、自动去重后进入 `today-pool.json`
- 只有 0 张图片的商品才会被排除在 phase2/phase3 之外
- `status: "partial"` 表示阶段1部分成功，phase2/phase3 仍可消费成功商品
- `phase1-state.json` 是 AI/云端编排层的实时进度来源

### contents.json

```json
{
  "date": "2026-03-10",
  "total_products": 10,
  "contents_per_product": 5,
  "contents": {
    "商品ID": [
      {
        "angle": 1,
        "angle_name": "颜色颜值",
        "title": "标题",
        "content": "正文",
        "tags": "#抓夹 #韩系 #复古",
        "reference_notes": []
      }
    ]
  }
}
```

### publish-plan.json

phase3 当前发布计划：

```json
{
  "date": "2026-03-12",
  "mode": "sequential",
  "dedupe_scope": "today",
  "count_requested": 5,
  "count_selected": 5,
  "items": [
    {
      "sequence": 1,
      "product_id": "691e83b0b4ade0001551defc",
      "product_name": "商品名",
      "angle": 1,
      "angle_name": "颜色颜值",
      "title": "标题",
      "topic_keywords": ["抓夹", "复古"],
      "selection_reason": "sequential",
      "status": "pending",
      "published_at": null,
      "error": null
    }
  ]
}
```

### phase3/YYYY-MM-DD/publish-records.json

phase3 当日发布记录，用于去重、统计当天已发数量和排查失败原因：

```json
{
  "date": "2026-03-12",
  "records": [
    {
      "attempted_at": "2026-03-12T18:20:31+08:00",
      "product_id": "691e83b0b4ade0001551defc",
      "product_name": "商品名",
      "angle": 1,
      "angle_name": "颜色颜值",
      "title": "标题",
      "topic_keywords": ["抓夹", "复古"],
      "status": "success",
      "dedupe_key": "2026-03-12:691e83b0b4ade0001551defc:1",
      "error": null,
      "publish_result": {},
      "artifacts": null
    }
  ]
}
```

---

## 内容角度（generate-content 每商品 5 篇）

| angle | angle_name | 编写思路 |
|-------|------------|----------|
| 1 | 颜色颜值 | 描述商品图片色彩/图案，营造视觉吸引力 |
| 2 | 材质质感 | 从图片质感推测材质，强调品质感 |
| 3 | 搭配场景 | 结合款式给出发型/穿搭建议 |
| 4 | 风格情感 | 赋予商品情感价值，引发共鸣 |
| 5 | 使用体验 | 模拟真实使用感受，增加可信度 |

---

## publish-note 参数说明

| 参数 | 说明 | 默认 |
|------|------|------|
| `--product-id` | 要发笔记的商品 ID | today-pool 第一个 |
| `--angle` | 使用该商品第几条草稿（1～N） | 第一条 |
| `--title` | 直接指定标题（与 `--content` 一起用时忽略 contents.json） | - |
| `--content` | 直接指定正文 | - |
| `--topic-keyword` | 话题关键词，可多次传入；不传则从草稿 tags 提取全部 # | - |
| `--image-path` | 指定图片路径，可多次传入 | 显式图片 > draft `selected_image_paths` > today-pool |

### phase1 图片规则

- 每个商品会下载商品主图全部图片与详情页图片全部图片
- 下载时优先原图，不消费缩略图参数版本
- phase1 会先按 URL 归一化去重，再按下载后内容 hash 精确去重
- 若商品 0 张图片，则视为该商品不完整，不进入 `today-pool.json.products`
- `--images-per-product` 已废弃，仅保留兼容，不再限制下载数量

### 话题与正文中的标签（publish-note）

- **话题**（多个）：通过 `add_topic(topic_keyword)` 逐个在编辑器中输入 `#关键词` 并点选平台下拉，与后台话题数据关联（带浏览数等），**有单独的数据交互**。
- **正文**：publish-note 只填充草稿 `content`，不再把 `draft.tags` 追加到正文末尾。
- **默认行为**：若未显式传入 `--topic-keyword`，会从草稿 `tags`（如 `#抓夹 #韩系 #复古`）中提取全部 `#标签`，并逐个作为平台话题添加。
- 效果：正文与平台话题完全分离；`#抓夹 #韩系 #复古` 这类标签只通过 `add_topic` 添加，不在正文中重复出现。

---

## generate-content 流程（实际实现）

1. 读取 `today-pool.json` 和商品去重图片集
2. 先对商品图片做本地视觉特征提取，再做图片语义分析（`image-facts.json`、`image-semantic-facts.json`、`product-facts.json`）
3. 加载趋势信号：优先 `trend-signals.json`，否则从 `references/history-notes/*.yaml` 或本地兜底
4. 调用 LLM 为每个商品生成 N 篇内容（`--contents-per-product`）
5. 为每条草稿分配 `selected_image_paths`，优先文案间不重复，建议 3-5 张，最多 9 张
6. 写出 `contents.json`、`phase2-report.json`

**不依赖**：xiaohongshu-mcp、用户端浏览器抓热门笔记。

---

## 已实现的编排逻辑

### 新命令

- `list-publish-candidates`
  - 列出 `contents.json` 中全部 `(product_id, angle)` 候选
  - 标记 `published_today` / `published_ever` / `eligible`
- `plan-publish`
  - 按 `--mode sequential|random` 生成待发布清单并写入 `publish-plan.json`
  - 支持 `--count`、`--date`、`--dedupe-scope today|ever`、`--seed`
- `run-publish-plan`
  - 消费已保存的 `publish-plan.json`
  - 每次发布后立即写入当日 `publish-records.json`
  - 单条失败不会回滚已成功项
  - AI 使用时应先确保当天计划已生成，不应把它当成隐式补计划入口

### 去重规则

- 默认去重范围：`today`
- 去重键：`(date, product_id, angle)`
- `ever` 模式下，任何历史成功发布过的 `(product_id, angle)` 都会被排除

### 兼容性

- 旧版 `publish-log.json` / `phase3-published.json` 不再作为 phase3 主数据源
- 新版 phase3 以 `publish-plan.json` 和按日 `publish-records.json` 为准

---

## 测试记录摘要

- **2026-03-05**：phase3 发布流程已跑通（手动创作 → 上传图文 → 填写标题/正文 → 添加话题 → 发布）
- **2026-03-10**：阶段2 已改为「主图分析 + LLM + 可选趋势」，不再依赖 xiaohongshu-mcp 抓热门笔记
- **已知**：`run-publish-plan` 会真实发布，测试时应优先使用 `list-publish-candidates` 或 `plan-publish`
