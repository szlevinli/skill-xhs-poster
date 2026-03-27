---
name: xhs-poster
description: |
  小红书商家后台自动化工具：准备商品、生成笔记内容、查看候选、生成发布计划、执行发布。
  使用 CLI `uv run xhs-poster ...` 执行。prepare-products 支持断点续传与收敛执行；phase3 使用发布计划与发布记录。
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

## 工作目录

- 执行本 skill 的任何命令前，必须先把当前工作目录切换到本 skill 仓库根目录
- 本 skill 仓库根目录就是 `SKILL.md` 所在目录
- 不要把 Openclaw workspace 根目录默认当作本 skill 的工作目录
- 执行前必须先自检：当前目录下应存在 `SKILL.md`、`pyproject.toml`、`src/xhs_poster/`
- 所有相对路径都相对于本 skill 仓库根目录解析，包括 `.env`、`xiaohongshu-data/`、`references/`

## 执行原则

- 先检查已有产物，再决定是否补跑阶段
- 只补跑缺失阶段；不要因为用户说“发布”就默认重跑 phase1 或 phase2
- 默认直接执行 `uv run xhs-poster ...`
- 不要因为命令耗时较长，就默认再套一层 `systemd-run`
- 只有运行环境会在命令尚未结束时强制回收子进程时，才考虑 `systemd-run`、`tmux`、`screen` 或 `nohup`
- `login merchant` 这类需要人工交互、扫码或浏览器前台操作的命令，始终前台执行
- LLM 相关环境变量统一使用仓库根目录 `.env`；默认只要求配置 `MOONSHOT_API_KEY`
- `SettingsConfigDict(env_file=".env")` 会按进程当前工作目录查找 `.env`；因此所有命令都必须在本仓库根目录执行

## 示例与参数

- 本文出现的命令示例仅用于说明命令形态，不代表实际执行时必须使用这些参数
- AI 不得机械照抄示例参数
- 执行前必须先结合用户要求、当前产物状态、当天日期、候选数量与发布目标，决定实际命令和参数
- 只有用户明确给出的参数，才能直接带入命令
- 用户未明确给出的参数，先按当前产物状态与本 skill 规则推导；只有在安全且无歧义时，才可省略并使用 CLI 默认值

参数来源优先级：

1. 用户当前消息中明确给出的参数
2. 用户在当前会话前文中仍然有效的明确要求
3. 当前产物可客观推导出的参数
4. CLI 默认值

禁止用更低优先级来源覆盖更高优先级来源。

## 阶段产物

- phase1: `xiaohongshu-data/today-pool.json`
- phase1 恢复检查点: `xiaohongshu-data/phase1-state.json`
- phase2: `xiaohongshu-data/contents.json`
- phase3 计划: `xiaohongshu-data/publish-plan.json`
- phase3 记录: `xiaohongshu-data/phase3/YYYY-MM-DD/publish-records.json`

## 阶段判断

- phase1 已完成：`today-pool.json` 存在，`date == 今天`，并且商品池可用于后续阶段
- phase2 已完成：`contents.json` 存在，`date == 今天`，并覆盖当前今日商品池
- phase3 可发布：当天已有可用的 `publish-plan.json`
- 不要仅凭文件存在就认定阶段完成
- 若 `today-pool.json` 或 `contents.json` 缺少 `date` 字段、结构损坏、或日期不是今天，都视为对应阶段未完成

补跑规则：

- 缺 `today-pool.json`，或 `today-pool.json.date != 今天`：执行 `prepare-products`
- 有今日 `today-pool.json`，但缺 `contents.json`，或 `contents.json.date != 今天`：执行 `generate-content`
- `today-pool.json` 与 `contents.json` 都可用时：直接进入发布相关命令
- 只有用户明确要求刷新商品池或重下图片时，才重跑 `prepare-products`
- 只有用户明确要求重写文案时，才重跑 `generate-content`

## 数量与完整性

- `prepare-products --limit 10` 的语义是“尽量得到 10 个成功商品”，不是“只检查前 10 个商品”
- 每个商品最多保留 3 张主图
- 只有 0 张主图的商品才视为不完整
- 只有 0 张主图的商品会被排除在 phase2 和 phase3 之外
- 若商品只有 1 张或 2 张主图，仍视为 phase1 成功商品
- 若 phase1 当天最终成功商品为 `M` 个，则 phase2 的完整目标为 `M * contents_per_product`
- 若多数商品已成功，不要为了少量异常商品默认推翻当天整个 phase1 结果

## 自然语言参数映射

当用户用自然语言描述数量、范围、关键词或目标时，AI 必须优先映射到对应 CLI 参数。

默认映射：

- “准备/抓取/获取/拉取 20 个商品” -> `prepare-products --limit 20`
- “每个商品下载/保留 2 张图” -> `prepare-products --images-per-product 2`
- “重新下载图片” -> `prepare-products --force-download`
- “每个商品生成 2 篇笔记/2 条内容” -> `generate-content --contents-per-product 2`
- “关键词用 抓夹” -> `generate-content --keyword 抓夹`
- “发布 2 篇” -> `run-publish-plan --count 2`

禁止误映射：

- 不得把“20 个商品”误映射成 `--contents-per-product 20`
- 不得把“每个商品 2 篇笔记”误映射成 `run-publish-plan --count 2`
- 不得把与发布数量相关的话误加到 `plan-publish`

## Phase3 规则

- phase3 必须严格分成两步：先 `plan-publish`，再 `run-publish-plan`
- `publish-note` 仅作为底层调试命令，不作为 AI 默认发布入口
- 即使底层实现保留“无计划时自动补计划”的兼容行为，AI 也不得依赖这个隐式行为
- `list-publish-candidates` 只查看候选，不生成计划，也不执行发布

`plan-publish` 规则：

- 默认不主动添加任何参数
- 当用户只是说“生成发布计划”“先排一下今天剩余可发内容”时，直接执行 `uv run xhs-poster plan-publish`
- 不要因为示例命令、习惯参数或 AI 自行估算，就主动添加 `--count`
- 只有用户明确指定计划数量、计划模式、日期或去重范围时，才添加对应参数

`run-publish-plan` 规则：

- 发布 1 篇：执行 `run-publish-plan --count 1`
- 发布 N 篇：执行 `run-publish-plan --count N`
- 若用户要求发布 `N` 篇，但当前 eligible 候选少于 `N`，默认发布可用数量，并明确告知实际发布数量
- 若当前没有可发候选，直接告知用户，不要默认回头重跑 phase1 或 phase2
- 默认去重范围为 `today`
- 当天成功发布数达到 50 后，不再执行任何发布命令

## 用户意图默认动作

- “发布 1 篇笔记”：先确保当天已有 `publish-plan.json`；若没有则先执行 `plan-publish`，再执行 `run-publish-plan --count 1`
- “发布 5 篇笔记”：先确保当天已有 `publish-plan.json`；若没有则先执行 `plan-publish`，再执行 `run-publish-plan --count 5`
- “继续发布几篇”：基于现有候选和发布账本继续增量发布
- “看看有哪些可以发”：执行 `list-publish-candidates`
- “先生成一个发布计划”：直接执行不带额外参数的 `plan-publish`
- “重新抓商品”：执行 `prepare-products`
- “重新下载图片”：执行 `prepare-products --force-download`
- “重新生成文案”：执行 `generate-content`
- “从头跑一遍”：才允许按阶段全量重跑

## 常用命令形态

以下命令仅为命令形态参考，不是固定参数模板。

```bash
uv run xhs-poster auth probe merchant
uv run xhs-poster prepare-products --limit N --images-per-product M
uv run xhs-poster generate-content --contents-per-product K
uv run xhs-poster list-publish-candidates
uv run xhs-poster plan-publish
uv run xhs-poster plan-publish --count N
uv run xhs-poster run-publish-plan --count N
```

## 只在需要时阅读

- 详细使用说明：`README.md`
- 快速命令参考：`QUICKREF.md`
- 数据格式、账本、编排细节：`REFERENCE.md`
- 仓库约束与开发规范：`AGENTS.md`

仅当当前任务需要更多细节时，再读取这些文件；默认不要把所有文档都当作执行前提。
