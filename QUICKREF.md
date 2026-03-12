# 小红书商品笔记自动发布 - 快速参考

## 工作模式

```
阶段1（准备）      阶段2（内容）        阶段3（发布）
    │                │                  │
    ▼                ▼                  ▼
拉取商品      →  主图分析+LLM生成  →  单条发布 / 编排批量发布
下载主图           contents.json      （phase3 + plan/candidates）
phase1-state.json
```

## CLI 命令

```bash
# 入口
uv run xhs-poster

# 登录（首次或过期时）
uv run xhs-poster login merchant

# 探测登录态
uv run xhs-poster auth probe merchant

# 准备商品和图片（支持断点续传；`--limit` 表示目标成功商品数）
uv run xhs-poster prepare-products --limit 10 --images-per-product 3

# 内容前置（可选）：生成趋势信号
uv run xhs-poster prepare-trends --keyword 抓夹

# 生成内容
uv run xhs-poster generate-content --keyword 抓夹 --contents-per-product 5

# 发布单条（每次一条，支持多话题；不传则默认使用草稿 tags 中全部 #话题）
uv run xhs-poster publish-note --angle 1
uv run xhs-poster publish-note --angle 2 --topic-keyword 抓夹 --topic-keyword 发饰
uv run xhs-poster publish-note --product-id XXX --angle 3 --topic-keyword 韩系 --topic-keyword 复古

# 发布编排：查看候选 / 生成计划 / 批量执行
uv run xhs-poster list-publish-candidates
uv run xhs-poster plan-publish --mode sequential --count 3
uv run xhs-poster run-publish-plan --mode random --count 3 --seed 42
```

## 数据文件

```
xiaohongshu-data/
├── today-pool.json      # prepare-products 输出
├── phase1-state.json    # prepare-products 实时进度 / 断点续传检查点
├── contents.json        # generate-content 输出
├── trend-signals.json   # prepare-trends 输出（可选）
├── publish-log.json     # 发布日志（仅追加）
├── phase3-published.json # phase3 成功账本（编排去重）
└── images/{商品ID}/     # 商品主图
```

## Phase1 语义

- `prepare-products --limit 10` 表示尽量收敛到 10 个成功商品
- 遇到 0 张主图商品会跳过，并继续尝试后续商品补位
- 每个商品最多保留前 3 张主图
- 商品只有 1 或 2 张主图时仍算成功，可继续进入阶段2和阶段3

## 环境配置

复制 `.env.example` 为 `.env`，填写 LLM 配置（如 Moonshot）：

```
MOONSHOT_API_KEY=sk-xxx
MOONSHOT_MODEL=moonshot-v1-8k
LLM_BASE_URL=https://api.moonshot.cn/v1
```

## 故障排除

| 问题 | 检查 |
|------|------|
| prepare-products 失败 | `auth probe merchant`，未登录则 `login merchant`；查看 `phase1-state.json` 的失败详情 |
| generate-content 失败 | today-pool.json 存在、LLM 配置正确、主图存在 |
| publish-note 失败 | prepare-products / generate-content 完成、登录态、contents.json 有对应草稿 |

## 更多文档

- `SKILL.md` — 完整技能文档
- `REFERENCE.md` — 数据格式、编排与账本逻辑
