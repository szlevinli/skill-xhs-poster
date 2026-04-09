# OPENAI Skill Execution Plan

## Goal

将当前仓库从“面向人工/Agent 的多命令自动化脚本”迭代为“可被支持 Skill 的 Agent 稳定调用、可由 cron 定时执行、可替换大模型后端的标准化 Skill”。

## Working Assumptions

- 目标运行环境包含支持 Skill 的 Agent，优先兼容 OpenAI 风格 Skill 调用方式。
- 需要兼容 OpenAI-compatible LLM 接口，并支持接入 Openclaw + kimi2.5 这类后端。
- 需要支持 cron 周期执行，无人工值守时不得依赖当前 shell 的工作目录、交互状态或隐式上下文。
- `login merchant` 仍保留为人工前台动作，不纳入 cron 自动化链路。

## Phase 0: Guardrails

### Task 0.1

梳理并冻结当前外部契约：

- 列出当前对 Agent 暴露的命令、参数、退出码、JSON 输出结构。
- 标记哪些参数已实现，哪些参数仍是“预留未使用”。
- 标记哪些命令适合 cron，哪些命令只适合人工前台执行。

### Deliverables

- 新增 `docs/skill-contract.md`
- 新增错误码表与命令能力矩阵

### Acceptance

- Agent 不再依赖阅读多份文档推断命令能力边界。
- 所有 CLI 参数都被明确归类为“已实现 / 待实现 / 废弃”。

## Phase 1: Introduce A Single Orchestrator Entry

### Task 1.1

新增统一编排入口，例如：

- `uv run xhs-poster run-daily`

### Required Behavior

- 自动检查 `today-pool.json` 是否为今日可用产物。
- 自动检查 `contents.json` 是否为今日可用产物。
- 按规则只补跑缺失阶段，不重复重跑已完成阶段。
- 若没有当天发布计划，自动生成计划。
- 按输入参数决定发布数量。
- 支持仅编排不发布的 `--dry-run`。
- 输出单一 JSON 结果，包含实际执行的阶段列表、跳过原因、结果摘要。

### Suggested Parameters

- `--limit`
- `--images-per-product`
- `--contents-per-product`
- `--publish-count`
- `--keyword`
- `--force-refresh-products`
- `--force-regenerate-content`
- `--dry-run`

### Deliverables

- CLI 新增 `run-daily`
- 新增 orchestrator 模块，例如 `src/xhs_poster/orchestrator.py`
- 为 phase1/phase2/phase3 建立统一的可复用状态检查函数

### Acceptance

- cron 只需要调用一个命令即可完成日常流程。
- Agent 不再需要从 `SKILL.md` 中手动推理补跑顺序。

## Phase 2: Make Cron Execution Safe

### Task 2.1

消除对当前工作目录的强依赖。

### Required Behavior

- 支持通过参数或环境变量显式指定 `project_root`、`data_dir`、`env_file`。
- 所有入口命令在非仓库根目录执行时仍能稳定工作。

### Task 2.2

加入并发保护。

### Required Behavior

- 运行中的作业持有文件锁。
- 新作业发现锁时返回结构化错误，避免两个 cron 同时运行。

### Task 2.3

加入作业级审计字段。

### Required Behavior

- 每次执行生成 `run_id`。
- 记录 `trigger_source`，例如 `agent`、`cron`、`manual`。
- 所有产物写入 `run_id`、开始时间、结束时间、实际执行阶段。

### Deliverables

- `Settings` 支持 `project_root` / `data_dir` / `env_file`
- 新增运行锁模块，例如 `src/xhs_poster/runlock.py`
- 统一作业元信息模型

### Acceptance

- cron 环境无需依赖 `cd repo && ...` 才能工作。
- 重复触发不会破坏状态文件。

## Phase 3: Extract Provider-Agnostic LLM Layer

### Task 3.1

抽离统一 LLM Client。

### Required Behavior

- 文本生成与视觉分析共用一套 provider 抽象。
- provider、model、base_url、api_key 不再在业务模块里各自拼接。
- `provider` 元信息不得继续硬编码为 `moonshot`。

### Task 3.2

适配 OpenAI-compatible providers。

### Required Behavior

- 至少支持一个默认 provider 抽象名，如 `openai_compatible`。
- 明确支持接入 Openclaw + kimi2.5 的配置方式。
- 对 429、超时、结构化 JSON 失败统一做重试和错误归一化。

### Suggested Module Layout

- `src/xhs_poster/llm/client.py`
- `src/xhs_poster/llm/types.py`
- `src/xhs_poster/llm/providers/openai_compatible.py`

### Deliverables

- `content_gen.py` 改为调用统一文本生成 client
- `image_semantics.py` 改为调用统一视觉 client
- 新增 provider 配置文档

### Acceptance

- 切换模型供应商不需要修改业务代码。
- 输出元信息中的 `provider` 与实际配置一致。

## Phase 4: Move Business Rules From Docs Into Code

### Task 4.1

把关键约束做成硬规则，而不是仅写在文档里。

### Required Behavior

- phase3 每日发布上限在代码中真实生效。
- `run-publish-plan` 的自动补计划行为要么显式保留并写入输出，要么改为严格要求已有计划。
- plan / run / dedupe 的规则由代码主导，文档只解释。

### Task 4.2

收敛文档与实现偏差。

### Required Behavior

- README、SKILL、REFERENCE 中的规则与代码逐项对齐。
- 删除或修正文档中已失效、仅靠 Agent 自觉遵守的部分。

### Deliverables

- phase3 规则校验模块
- 文档对齐修订

### Acceptance

- 业务约束不再只存在于 `SKILL.md` 的自然语言描述中。
- 文档和代码对同一规则没有冲突。

## Phase 5: Tighten Agent-Facing CLI Contract

### Task 5.1

清理未实现参数。

### Required Behavior

- `generate-content` 中未生效的参数要么补实现，要么删除。
- 帮助文本不得再暴露“预留未使用”参数。

### Task 5.2

标准化 JSON 响应。

### Required Behavior

- 所有命令统一包含 `status`、`error`、`message`、`data`、`run_id`。
- 所有错误可被 Agent 直接分类为：登录问题、配置问题、数据问题、平台问题、限流问题。

### Deliverables

- 响应封装工具
- 错误类型枚举与统一映射

### Acceptance

- Agent 无需针对每个命令单独写异常分支解析。

## Phase 6: Add OpenAI-Oriented Skill Packaging

### Task 6.1

补充面向支持 Skill 的 Agent 的 manifest 和运行说明。

### Required Behavior

- 明确哪些命令允许隐式调用。
- 明确哪些命令必须人工确认。
- 明确哪些命令可被定时调度。

### Task 6.2

补充 cron 集成样例。

### Required Behavior

- 提供示例 cron 配置。
- 提供失败重试建议。
- 提供产物轮询与日志读取说明。

### Deliverables

- 更新 `agents/openai.yaml`
- 新增 `docs/cron-deployment.md`
- 新增 `.env.example` 中的 provider 配置样例

### Acceptance

- 新 Agent 接入时，不需要阅读源码即可知道如何调用和部署。

## Recommended Execution Order

1. 完成 Phase 0，冻结当前契约。
2. 完成 Phase 1，新增 `run-daily`。
3. 完成 Phase 2，补齐 cron 安全能力。
4. 完成 Phase 3，抽离通用 LLM/provider 层。
5. 完成 Phase 4，收敛“文档规则”到“代码规则”。
6. 完成 Phase 5，统一 Agent-facing 响应契约。
7. 完成 Phase 6，补齐 OpenAI/Skill/cron 包装。

## Immediate Next Actions For An AI Coding Agent

1. 创建 `orchestrator.py`，实现“检查 phase1/phase2/phase3 状态”的纯函数。
2. 在 `cli.py` 中新增 `run-daily` 命令，并先接通现有 phase1/phase2/phase3。
3. 在 `config.py` 中新增显式 `env_file`、`project_root`、`data_dir` 支持。
4. 引入运行锁，保证 `run-daily` 不会并发执行。
5. 抽离 `llm_client`，把 `content_gen.py` 和 `image_semantics.py` 的 HTTP 调用迁移过去。
6. 清理 `generate-content` 的未生效参数或补实现。
7. 校验 README / SKILL / REFERENCE 与最终行为一致。

## Definition Of Done

- Agent 可通过单一入口完成日常流程，不依赖文档推理阶段顺序。
- cron 可稳定执行，不依赖当前 shell 工作目录，不会并发踩状态。
- 更换为 OpenAI-compatible 新模型后端时，无需修改业务流程代码。
- 关键业务规则在代码中可验证、可测试、可审计。
- Skill 的 manifest、CLI、文档、错误码、JSON 响应保持一致。
