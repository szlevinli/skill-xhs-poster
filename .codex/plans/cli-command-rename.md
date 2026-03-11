# CLI 命令一次性重命名方案

## Summary
彻底移除 `phase1 / phase2 / phase3` 这类阶段编号式命名，统一改为“用户意图命名”。不保留旧命令，不做兼容跳转，不输出 deprecated 提示，一次切换完成。

目标：

- 用户只看命令名就能理解用途
- 文档、帮助、示例、技能说明全部统一
- 内部是否仍沿用 `phase*` 模块名不是重点，对外 CLI 必须完全语义化

## Final Command Set

### 主流程命令
- `phase1` -> `prepare-products`
  - 从商家后台拉商品、下载主图、生成 `today-pool.json`
- `prepare-trends` -> `prepare-trends`
  - 这个名字已经足够清晰，保留
- `phase2` -> `generate-content`
  - 基于商品、主图和趋势信号生成 `contents.json`
- `phase3` -> `publish-note`
  - 发布一条笔记

### 编排层命令
- `phase3-candidates` -> `list-publish-candidates`
- `phase3-plan` -> `plan-publish`
- `phase3-run-plan` -> `run-publish-plan`

## Final CLI Shape

```bash
uv run xhs-poster prepare-products --limit 10 --images-per-product 3
uv run xhs-poster prepare-trends --keyword 抓夹
uv run xhs-poster generate-content --keyword 抓夹 --contents-per-product 5
uv run xhs-poster publish-note --product-id XXX --angle 1

uv run xhs-poster list-publish-candidates
uv run xhs-poster plan-publish --mode sequential --count 3
uv run xhs-poster run-publish-plan --mode random --count 3 --seed 42
```

## Required Implementation Changes

### 1. CLI registration
修改 [src/xhs_poster/cli.py](/Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/src/xhs_poster/cli.py)：

- 删除命令注册：
  - `phase1`
  - `phase2`
  - `phase3`
  - `phase3-candidates`
  - `phase3-plan`
  - `phase3-run-plan`
- 新增命令注册：
  - `prepare-products`
  - `generate-content`
  - `publish-note`
  - `list-publish-candidates`
  - `plan-publish`
  - `run-publish-plan`

要求：
- handler 逻辑复用现有 `build_*_payload()`，不改 payload schema
- 参数名保持现状，不改 `--product-id` / `--angle` / `--topic-keyword`
- `APP_HELP` 改为基于新命令名描述完整流程

### 2. Help text rewrite
所有命令帮助统一改成“动作 + 结果”表达：

- `prepare-products`
  - “从商家后台同步商品与主图，写出 today-pool.json”
- `generate-content`
  - “基于商品和主图生成待发布笔记内容，写出 contents.json”
- `publish-note`
  - “发布一条笔记到商家后台”
- `list-publish-candidates`
  - “列出当前所有可发布候选并标记是否已发布”
- `plan-publish`
  - “生成发布计划但不执行”
- `run-publish-plan`
  - “按计划批量发布并自动去重”

### 3. Documentation rewrite
必须同步更新以下文件中所有命令示例与说明：

- [SKILL.md](/Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/SKILL.md)
- [QUICKREF.md](/Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/QUICKREF.md)
- [REFERENCE.md](/Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/REFERENCE.md)
- [AGENTS.md](/Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/AGENTS.md) 如果包含命令引用，也要同步

文档改动要求：
- 不再把 `phase1/2/3` 作为对外概念
- “三阶段工作流”可以保留为内部心智模型，但必须配套说明：
  - 对外命令名分别是 `prepare-products / generate-content / publish-note`
- 所有 shell 示例全部替换成新命令名
- 不提“旧命令”“兼容”“deprecated”

### 4. Reference consistency
需要全文检查并删除以下对外表述：

- “执行 `phase1`”
- “执行 `phase2`”
- “执行 `phase3`”
- “phase3-candidates / phase3-plan / phase3-run-plan”

统一替换成新命令名。

### 5. Optional internal naming
内部 Python 函数和模块名可暂时保留：

- `phase1.py`
- `phase2.py`
- `phase3.py`
- `build_phase3_payload()`

默认不做内部重命名，避免无必要风险。  
只要 CLI 和文档层已完全切换即可。

## Public API / Interface Changes

### CLI breaking changes
对外命令名直接变更为：

- `prepare-products`
- `prepare-trends`
- `generate-content`
- `publish-note`
- `list-publish-candidates`
- `plan-publish`
- `run-publish-plan`

### Not changing
以下内容保持不变：

- JSON payload 结构
- exit code 语义
- 参数名与参数行为
- 数据文件路径与格式
- 内部模块文件名

## Search/Replace Matrix

必须完成以下映射：

- `uv run xhs-poster phase1` -> `uv run xhs-poster prepare-products`
- `uv run xhs-poster phase2` -> `uv run xhs-poster generate-content`
- `uv run xhs-poster phase3` -> `uv run xhs-poster publish-note`
- `uv run xhs-poster phase3-candidates` -> `uv run xhs-poster list-publish-candidates`
- `uv run xhs-poster phase3-plan` -> `uv run xhs-poster plan-publish`
- `uv run xhs-poster phase3-run-plan` -> `uv run xhs-poster run-publish-plan`

## Test Cases And Scenarios

### CLI discovery
- `uv run xhs-poster --help` 只显示新命令名
- `uv run xhs-poster prepare-products --help` 正常
- `uv run xhs-poster generate-content --help` 正常
- `uv run xhs-poster publish-note --help` 正常
- `uv run xhs-poster list-publish-candidates --help` 正常
- `uv run xhs-poster plan-publish --help` 正常
- `uv run xhs-poster run-publish-plan --help` 正常

### Old command removal
- `uv run xhs-poster phase1` 应直接失败
- `uv run xhs-poster phase2` 应直接失败
- `uv run xhs-poster phase3` 应直接失败
- `uv run xhs-poster phase3-candidates` 应直接失败
- `uv run xhs-poster phase3-plan` 应直接失败
- `uv run xhs-poster phase3-run-plan` 应直接失败

### Functional parity
- `prepare-products` 输出与原 `phase1` 行为一致
- `generate-content` 输出与原 `phase2` 行为一致
- `publish-note` 输出与原 `phase3` 行为一致
- `list-publish-candidates` / `plan-publish` / `run-publish-plan` 行为与现有实现一致

### Documentation checks
- 文档中不再出现旧命令示例
- README/技能文档命令与 `--help` 输出一致
- 文档里的流程顺序与 CLI 顶层帮助一致

## Assumptions And Defaults
- 不考虑任何向后兼容
- 旧命令删除后，调用失败属于预期行为
- 内部模块名暂不重构
- `prepare-trends` 保持不变，因为它已经足够清晰
