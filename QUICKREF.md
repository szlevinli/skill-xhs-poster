# 小红书商品笔记自动发布 - 快速参考

## 三阶段工作模式

```
阶段1（准备）      阶段2（内容）        阶段3（发布）
    │                │                  │
    ▼                ▼                  ▼
拉取商品      →  主图分析+LLM生成  →  单条发布
下载主图           contents.json      （每次一条，无编排）
```

## CLI 命令

```bash
# 入口
uv run xhs-poster

# 登录（首次或过期时）
uv run xhs-poster login merchant

# 探测登录态
uv run xhs-poster auth probe merchant

# 阶段1：准备商品和图片
uv run xhs-poster phase1 --limit 10 --images-per-product 3

# 阶段2 前置（可选）：生成趋势信号
uv run xhs-poster prepare-trends --keyword 抓夹

# 阶段2：生成内容
uv run xhs-poster phase2 --keyword 抓夹 --contents-per-product 5

# 阶段3：发布单条（每次一条，支持多话题；不传则默认使用草稿 tags 中全部 #话题）
uv run xhs-poster phase3 --angle 1
uv run xhs-poster phase3 --angle 2 --topic-keyword 抓夹 --topic-keyword 发饰
uv run xhs-poster phase3 --product-id XXX --angle 3 --topic-keyword 韩系 --topic-keyword 复古
```

## 数据文件

```
xiaohongshu-data/
├── today-pool.json      # 阶段1输出
├── contents.json        # 阶段2输出
├── trend-signals.json   # prepare-trends 输出（可选）
├── publish-log.json     # 发布记录（仅追加，不参与编排）
└── images/{商品ID}/     # 商品主图
```

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
| phase1 失败 | `auth probe merchant`，未登录则 `login merchant` |
| phase2 失败 | today-pool.json 存在、LLM 配置正确、主图存在 |
| phase3 失败 | phase1/2 完成、登录态、contents.json 有对应草稿 |

## 更多文档

- `SKILL.md` — 完整技能文档
- `REFERENCE.md` — 数据格式、规划中的编排逻辑
