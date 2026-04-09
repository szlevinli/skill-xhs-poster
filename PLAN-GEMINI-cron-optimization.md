# GEMINI AI Agent Skill 优化与迭代计划

本计划旨在将 `xhs-poster` 从一个半自动的 CLI 工具提升为具备**高可靠性、自愈能力和闭环反馈**的生产级 AI Agent Skill。特别针对 Openclaw + Kimi 2.5 的 Cron 定时执行环境进行优化。

---

## 核心目标
1. **提升稳定性**: 解决 Cron 环境下的并发、登录态失效及环境隔离问题。
2. **增强可观测性**: 降低 Agent 理解项目状态的 Token 消耗，提供结构化反馈。
3. **建立闭环**: 引入发布后的数据回流（阅读/点赞），实现策略自动迭代。

---

## 实施路线图

### 第一阶段：健壮性与可观测性（快速见效）
**目标**：确保 Cron 任务不会因为并发或状态不明而崩溃。

- [ ] **任务 1.1: 实现 `status` 汇总子命令**
    - **内容**: 增加 `uv run xhs-poster status`，输出简化的 JSON 摘要：今日进度、剩余候选数、登录态寿命、最近一次失败原因。
    - **价值**: 显著降低 Agent 初始研究阶段的读取开销。
- [ ] **任务 1.2: 引入文件锁机制**
    - **内容**: 在写入 `today-pool.json`、`contents.json` 和 `publish-records.json` 时使用 `portalocker`。
    - **价值**: 防止 Cron 并发冲突导致的数据损坏。
- [ ] **任务 1.3: 支持结构化 JSON 日志**
    - **内容**: 增加 `--log-json` 全局开关，将运行时步骤（Step）、结果（Result）和耗时（Latency）以 JSON 格式流式输出到 stderr。

### 第二阶段：Agent 协同与自愈能力
**目标**：提升 Agent 在异常处理时的智能化程度。

- [ ] **任务 2.1: 登录态监控与告警 (`auth monitor`)**
    - **内容**: 增加子命令检查 Session 有效期。若低于阈值，通过配置的 Webhook（钉钉/企微）发送扫码请求。
- [ ] **任务 2.2: 基于 Vision LLM 的发布失败分析**
    - **内容**: 在 Phase 3 失败后，若配置了视觉模型，自动调用 Kimi 2.5 对 `artifacts` 中的错误截图进行 OCR 和语义分析。
    - **价值**: 将“图片级错误”转化为“文字级指令”，反馈给 Agent 执行自愈动作。

### 第三阶段：智能闭环与反馈回流
**目标**：让 Agent 具备“运营思维”，根据效果调整策略。

- [ ] **任务 3.1: 开发 Phase 4 `fetch-metrics` 模块**
    - **内容**: 增加子命令，根据 `publish-records.json` 中的笔记链接，回抓阅读数、点赞、收藏等数据。
- [ ] **任务 3.2: 实时趋势注入增强**
    - **内容**: 优化 `prepare-trends`，除了抓取历史，增加对小红书当前实时搜索热词的采集，并将其注入 Phase 2 的 Prompt 参数。
- [ ] **任务 3.3: 文案 A/B 测试支持**
    - **内容**: 在 `ContentsBundle` 中支持标记不同的生成策略（如“专业风”vs“生活化”），结合 Metrics 数据辅助 Agent 决策。

### 第四阶段：环境标准化
**目标**：简化部署，确保跨环境一致性。

- [ ] **任务 4.1: 提供 Dockerfile 与 Docker Compose**
    - **内容**: 构建集成 Playwright 依赖、Python 环境及常用 Cron 工具的镜像。
- [ ] **任务 4.2: 提供标准 Cron 配置模版**
    - **内容**: 在仓库中提供 `crontab.example`，定义推荐的任务触发频率（如：早上抓取、下午生成、傍晚发布）。

---

## 验证标准
1. **无人值守运行**: 连续 72 小时 Cron 执行无死锁、无状态文件损坏。
2. **决策效率**: Agent 通过 `status` 命令即可在 1 次对话内完成后续任务编排。
3. **数据闭环**: `publish-records.json` 中包含至少 3 个维度的互动数据。

---

## Agent 执行提示 (Prompts)
> "请参考 `.codex/plans/GEMINI-skill-optimization-plan.md`，从第一阶段的任务 1.1 开始，逐步实现 `xhs-poster status` 子命令。"
