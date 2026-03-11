# 小红书商品笔记自动发布 - 参考文档

本文档提供数据格式、内容策略及当前已实现的编排/账本逻辑，供实现或扩展时参考。

---

## 数据格式

### today-pool.json

```json
{
  "date": "2026-03-10",
  "products": [
    {
      "id": "商品ID",
      "name": "商品名称",
      "create_time": "上架时间"
    }
  ],
  "images": {
    "商品ID": ["/path/to/1.jpg", "/path/to/2.jpg", "/path/to/3.jpg"]
  }
}
```

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

### publish-log.json

每次 `publish-note` 发布成功或失败后追加一条记录，结构由 `Phase3ExecutionResult` 决定。该文件用于追踪发布过程，不承担去重职责。

### phase3-published.json

`publish-note` 成功账本，供 `list-publish-candidates` / `plan-publish` / `run-publish-plan` 去重使用：

```json
{
  "records": [
    {
      "date": "2026-03-11",
      "published_at": "2026-03-11T18:20:31+08:00",
      "product_id": "691e83b0b4ade0001551defc",
      "product_name": "商品名",
      "angle": 1,
      "angle_name": "颜色颜值",
      "title": "标题",
      "topic_keywords": ["抓夹", "复古"],
      "status": "success",
      "publish_log_path": "xiaohongshu-data/publish-log.json",
      "dedupe_key": "2026-03-11:691e83b0b4ade0001551defc:1"
    }
  ]
}
```

---

## 内容角度（generate-content 每商品 5 篇）

| angle | angle_name | 编写思路 |
|-------|------------|----------|
| 1 | 颜色颜值 | 描述主图色彩/图案，营造视觉吸引力 |
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
| `--image-path` | 指定图片路径，可多次传入 | today-pool 主图 |

### 话题与正文中的标签（publish-note）

- **话题**（多个）：通过 `add_topic(topic_keyword)` 逐个在编辑器中输入 `#关键词` 并点选平台下拉，与后台话题数据关联（带浏览数等），**有单独的数据交互**。
- **正文**：publish-note 只填充草稿 `content`，不再把 `draft.tags` 追加到正文末尾。
- **默认行为**：若未显式传入 `--topic-keyword`，会从草稿 `tags`（如 `#抓夹 #韩系 #复古`）中提取全部 `#标签`，并逐个作为平台话题添加。
- 效果：正文与平台话题完全分离；`#抓夹 #韩系 #复古` 这类标签只通过 `add_topic` 添加，不在正文中重复出现。

---

## generate-content 流程（实际实现）

1. 读取 `today-pool.json` 和商品主图
2. 分析主图提取视觉特征（`image_facts`、`product-facts.json`）
3. 加载趋势信号：优先 `trend-signals.json`，否则从 `references/history-notes/*.yaml` 或本地兜底
4. 调用 LLM 为每个商品生成 N 篇内容（`--contents-per-product`）
5. 写出 `contents.json`、`phase2-report.json`

**不依赖**：xiaohongshu-mcp、用户端浏览器抓热门笔记。

---

## 已实现的编排逻辑

### 新命令

- `list-publish-candidates`
  - 列出 `contents.json` 中全部 `(product_id, angle)` 候选
  - 标记 `published_today` / `published_ever` / `eligible`
- `plan-publish`
  - 按 `--mode sequential|random` 生成待发布清单
  - 支持 `--count`、`--date`、`--dedupe-scope today|ever`、`--seed`
- `run-publish-plan`
  - 先生成计划，再逐条调用 `publish-note`
  - 成功后立即写入 `phase3-published.json`
  - 单条失败不会回滚已成功项

### 去重规则

- 默认去重范围：`today`
- 去重键：`(date, product_id, angle)`
- `ever` 模式下，任何历史成功发布过的 `(product_id, angle)` 都会被排除

### 兼容性

- 旧版 `phase3-published.json` 若使用 `entries` 结构，当前代码会自动兼容读取
- 新写入统一使用 `records` 结构

---

## 测试记录摘要

- **2026-03-05**：phase3 发布流程已跑通（手动创作 → 上传图文 → 填写标题/正文 → 添加话题 → 发布）
- **2026-03-10**：阶段2 已改为「主图分析 + LLM + 可选趋势」，不再依赖 xiaohongshu-mcp 抓热门笔记
- **已知**：`run-publish-plan` 会真实发布，测试时应优先使用 `list-publish-candidates` 或 `plan-publish`
