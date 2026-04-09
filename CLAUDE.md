# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

小红书商品笔记自动发布工具。CLI 三阶段流程：
1. `prepare-products` — 用 Playwright 从商家后台抓取商品与主图
2. `generate-content` — 调 LLM 生成种草文案
3. `plan-publish` + `run-publish-plan` — 生成发布计划并执行发布

**所有命令必须在仓库根目录执行**（`SettingsConfigDict(env_file=".env")` 按进程 cwd 查找 `.env`）。

## Commands

```bash
uv sync                                      # 安装依赖
uv run xhs-poster --help                     # 查看全部子命令
uv run python -m compileall src              # 语法检查（无自动化测试套件）
```

常用流程命令：

```bash
uv run xhs-poster login merchant
uv run xhs-poster auth export merchant --output ./merchant-state.json
uv run xhs-poster auth import merchant --input ./merchant-state.json
uv run xhs-poster auth probe merchant
uv run xhs-poster prepare-products --limit 10 --images-per-product 3
uv run xhs-poster prepare-trends --keyword 抓夹   # 可选
uv run xhs-poster generate-content --contents-per-product 5
uv run xhs-poster list-publish-candidates
uv run xhs-poster plan-publish
uv run xhs-poster run-publish-plan --count 1
```

## Architecture

```
src/xhs_poster/
  cli.py            — Typer 入口，注册所有子命令，输出全部为 JSON
  config.py         — Settings（pydantic-settings），集中管理所有路径与 env 变量
  phase1.py         — prepare-products 实现（Playwright 爬取，断点续传，收敛执行）
  phase2.py         — generate-content 实现（LLM 文案生成）
  phase3.py         — plan-publish / run-publish-plan / publish-note 实现
  auth.py           — login / export / import / probe 实现
  browser.py        — Playwright 浏览器封装
  models.py         — 共享 Pydantic 模型
  content_gen.py    — LLM 调用封装（OpenAI 兼容接口）
  image_semantics.py — 视觉 LLM 图片语义分析，结果长期缓存
  trend_signals.py  — prepare-trends 实现
  facts_builder.py / image_facts.py / hot_notes.py / history_notes.py / merchant.py
                    — 各类辅助数据构建器
```

**数据目录** `xiaohongshu-data/`（运行时产物，不是源码）：

| 文件 | 阶段 | 说明 |
|------|------|------|
| `today-pool.json` | phase1 输出 | 当日商品池，带 `date` 字段 |
| `phase1-state.json` | phase1 检查点 | 断点续传状态，可轮询 |
| `contents.json` | phase2 输出 | 文案内容，带 `date` 字段 |
| `publish-plan.json` | phase3 计划 | 当日发布计划 |
| `phase3/YYYY-MM-DD/publish-records.json` | phase3 记录 | 当日发布账本 |
| `images/{product_id}/` | phase1 下载 | 商品主图 |
| `image-semantic-facts.json` | 视觉分析缓存 | 长期缓存，避免重复调用视觉 LLM |

## Configuration (`.env`)

最低配置：`MOONSHOT_API_KEY=<key>`

LLM 相关变量支持多种别名（见 `config.py`）：
- 文案 LLM：`MOONSHOT_API_KEY` / `LLM_API_KEY` / `XHS_POSTER_LLM_API_KEY`
- 视觉 LLM：`VISION_LLM_API_KEY`（未设则复用文案 LLM 的 key）
- 模型：`MOONSHOT_MODEL` / `VISION_LLM_MODEL`

## Coding Conventions

- Python 3.13+，`from __future__ import annotations`，4-space 缩进，类型注解
- 新文件按阶段命名，保持职责单一
- Pydantic 模型/Settings 类用 `PascalCase`，其余 `snake_case`
- CLI 子命令均通过 `emit_json()` 输出，exit code 0 = 成功，2 = 未登录/失败
- 提交信息使用中文短句，按阶段或模块范围描述（参照 git log 风格）

## Key Behaviors to Preserve

- `prepare-products --limit N` 语义是"得到 N 个成功商品"，不是"只看前 N 个"；只有 0 张图的商品才排除
- `plan-publish` 不传 `--count` 时，默认选今天剩余全部可发候选
- `publish-note` 是底层调试命令，AI 发布入口应走 `plan-publish` + `run-publish-plan`
- 阶段完成判断：文件存在 **且** `date == 今天`，结构合法
- `image-semantic-facts.json` 是长期缓存，不要随意清除
