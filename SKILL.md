---
name: xiaohongshu-product-poster
description: |
  小红书商家后台自动化工具：从商品管理拉取商品、生成笔记内容、发布笔记。
  三阶段独立执行（phase1 准备 → phase2 内容 → phase3 发布），通过 JSON 文件传递数据。
  使用 CLI `uv run xhs-poster` 执行。需商家端登录；phase2 依赖 LLM。
  使用场景：拉取商品主图、生成种草文案、按需发布单条笔记。
---

# 小红书商品笔记自动发布

## 何时使用本 skill

当用户表达「发小红书商品笔记」「拉商品并生成/发布笔记」「从商家后台拉商品写种草文案」等意图时，使用本 skill。入口为在技能目录下执行 `uv run xhs-poster` 子命令。

## 心智模型：三阶段工作流

本技能采用**三阶段独立执行**的设计，各阶段通过文件传递数据，支持独立调度。

```
┌─────────────────────────────────────────────────────────────────┐
│                         一天的工作流                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  阶段1: 准备          阶段2: 内容           阶段3: 发布           │
│  ━━━━━━━━━━          ━━━━━━━━━━          ━━━━━━━━━━             │
│                                                                  │
│  ┌─────────┐         ┌─────────┐         ┌─────────┐          │
│  │拉取商品 │   ───>  │主图分析 │   ───>  │单条发布 │          │
│  │下载主图 │         │LLM生成  │         │         │          │
│  │         │         │         │         │         │          │
│  └────┬────┘         └────┬────┘         └────┬────┘          │
│       │                   │                   │                 │
│       ▼                   ▼                   ▼                 │
│  today-pool.json     contents.json      publish-log.json        │
│  (商品+图片)         (标题+正文+标签)    (发布记录，仅追加)        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 阶段说明

| 阶段 | 职责 | 输入 | 输出 | 依赖 |
|------|------|------|------|------|
| **phase1** | 拉取商品和主图 | 商家后台 | `today-pool.json` + 图片 | 商家端登录 |
| **prepare-trends** | 生成趋势信号（可选） | `references/history-notes/*.yaml` | `trend-signals.json` | 无 |
| **phase2** | 生成笔记内容 | `today-pool.json` + 主图 + LLM | `contents.json` | phase1、LLM |
| **phase3** | 发布单条笔记 | `today-pool.json` + `contents.json` | 发布到小红书 + 追加日志 | phase1、phase2、商家端登录 |

---

## 快速开始

### 前置条件

- Python 3.13+，`uv` 包管理器
- 配置 LLM（`.env` 中 `MOONSHOT_API_KEY` 等，见 `.env.example`）
- 首次使用需先执行 `login merchant` 完成商家端登录

### 执行流程

```bash
# 进入技能目录
cd ~/.openclaw/workspace/skills/xiaohongshu-product-poster

# 1. 登录（首次或 session 过期时）
uv run xhs-poster login merchant

# 2. 准备商品和图片
uv run xhs-poster phase1 --limit 10 --images-per-product 3

# 3. 生成趋势信号（可选，不跑则 phase2 用本地兜底）
uv run xhs-poster prepare-trends --keyword 抓夹

# 4. 生成内容
uv run xhs-poster phase2 --keyword 抓夹 --contents-per-product 5

# 5. 发布单条笔记（支持多话题；不传 `--topic-keyword` 则使用草稿 tags 中全部 #话题）
uv run xhs-poster phase3 --angle 1
uv run xhs-poster phase3 --angle 2 --topic-keyword 抓夹 --topic-keyword 发饰
uv run xhs-poster phase3 --angle 3 --topic-keyword 韩系 --topic-keyword 复古
```

### 常用命令

```bash
# 探测登录态
uv run xhs-poster auth probe merchant

# 查看各命令帮助
uv run xhs-poster phase1 --help
uv run xhs-poster phase2 --help
uv run xhs-poster phase3 --help
```

### AI 调用时的参数与顺序

**执行顺序**：先 `auth probe merchant`；若未登录（退出码非 0），提示用户执行 `login merchant`（会打开浏览器，需人工完成）。登录就绪后：phase1 → 可选 prepare-trends → phase2 → 按需多次 phase3。

**keyword**：`--keyword` 不传时，prepare-trends 默认使用「发饰」；phase2 会从 `today-pool.json` 中商品名按固定词表（抓夹、发夹、鲨鱼夹、发饰、头饰）推断。建议：phase1 完成后读取 today-pool 商品名推断一个 keyword，对 prepare-trends 与 phase2 使用同一 keyword。

**输出与错误**：命令成功时 stdout 为 JSON（含 `status`、`data` 等），便于解析并决定下一步（如根据生成条数决定调用几次 phase3）。失败时退出码非 0，错误信息在 payload 或 stderr 中，可据此重试或报错。

---

## 数据文件

```
{project_root}/xiaohongshu-data/
├── today-pool.json          # 阶段1输出：商品池 + 图片路径
├── contents.json            # 阶段2输出：生成的内容
├── product-facts.json       # 阶段2中间：主图分析结果
├── trend-signals.json       # prepare-trends 输出
├── phase2-report.json       # 阶段2报告
├── publish-log.json         # 发布记录（仅追加，不自动编排）
├── images/                  # 商品主图
│   └── {商品ID}/
│       ├── 1.jpg
│       └── ...
└── profiles/merchant/       # 商家端登录态

{project_root}/references/
└── history-notes/           # 历史笔记 YAML（供 prepare-trends）
```

---

## 阶段3 行为说明

**phase3 只负责「发」，不负责编排**：

- 每次调用发布**一条**笔记
- 不传 `--product-id` 时取 today-pool 第一个商品
- 不传 `--angle` 时取该商品第一条草稿
- 正文只使用草稿 `content`，不会再把 `tags` 拼到正文末尾
- 话题通过 `add_topic()` 单独添加；不传 `--topic-keyword` 时，默认从草稿 `tags` 中提取全部 `#话题`
- 不判断哪些已发、哪些未发；`publish-log.json` 仅作记录，不参与编排
- 批量发布由**调用方**（脚本或 AI）多次调用 phase3 并传入 `--product-id` / `--angle`，本 skill 不实现编排逻辑

**批量发布示例**（需由调用方编排）：

```bash
# 手动发 3 条：商品1 的 angle 1/2/3
uv run xhs-poster phase3 --angle 1
uv run xhs-poster phase3 --angle 2 --topic-keyword 抓夹 --topic-keyword 发饰
uv run xhs-poster phase3 --angle 3 --topic-keyword 韩系 --topic-keyword 复古
```

---

## 内容生成约束（phase2）

**禁止内容**：
- ❌ 不提及价格（如"便宜"、"贵"、"性价比"、"值"等）
- ❌ 不胡乱猜测（如"一套4个"、"送闺蜜"等未确认的信息）
- ❌ 不做虚假宣传（如"明星同款"、"网红推荐"等无法验证的说法）

**必须遵循**：
- ✅ 基于图片事实：只描述图片中确认的特征（颜色、图案、材质）
- ✅ 基于商品名称：从名称中提取款式、风格等信息
- ✅ 个人体验角度：用"我觉得"、"我发现"等第一人称表达
- ✅ 通用场景描述：日常、通勤、约会等普适场景

---

## 环境配置

复制 `.env.example` 为 `.env` 并填写：

```bash
# LLM（Moonshot 示例）
MOONSHOT_API_KEY=sk-xxx
MOONSHOT_MODEL=moonshot-v1-8k
LLM_BASE_URL=https://api.moonshot.cn/v1
```

项目根目录默认 `{skill_dir}`，可通过 `XHS_POSTER_PROJECT_ROOT` 覆盖。

---

## 故障排除

### 阶段1失败
- 检查登录：`uv run xhs-poster auth probe merchant`（退出码 0 表示已登录）
- 未登录：`uv run xhs-poster login merchant`
- 网络错误（如 Connection reset）：重试或减小 `--limit`

### 阶段2失败
- 检查 phase1 是否完成：`today-pool.json` 存在
- 检查 LLM 配置：`.env` 中 API Key 正确
- 检查主图：`xiaohongshu-data/images/{商品ID}/` 下有图片

### 阶段3失败
- 检查 phase1 和 phase2 是否完成
- 检查登录态
- 检查 `contents.json` 中是否有对应商品和 angle 的草稿

---

## 更多文档

- [REFERENCE.md](REFERENCE.md) — 详细说明、数据格式、规划中的编排逻辑
- [QUICKREF.md](QUICKREF.md) — 快速参考
