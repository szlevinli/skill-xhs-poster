# Gemini Project Context: 小红书商品笔记自动发布 (xhs-poster)

## 项目概览
`xiaohongshu-product-poster`（CLI 名称为 `xhs-poster`）是一个专为小红书商家后台设计的自动化工具。它能够自动从商家后台拉取商品信息及其主图，利用大语言模型（如 Moonshot AI）生成种草文案，并自动化执行笔记发布流程。

### 核心技术栈
- **语言**: Python 3.13+
- **包管理**: [uv](https://docs.astral.sh/uv/)
- **自动化**: Playwright (用于模拟浏览器操作商家后台)
- **数据建模**: Pydantic (用于配置、状态和产物的结构化管理)
- **内容生成**: OpenAI 兼容接口 (默认推荐 Moonshot AI)
- **CLI 框架**: Typer & Rich

### 核心工作流 (三阶段执行)
1. **Phase 1: `prepare-products`** - 从商家后台同步商品和主图。支持断点续传，产物为 `today-pool.json`。
2. **Phase 2: `generate-content`** - 对商品图进行语义分析，结合趋势信号生成多条待发布文案。产物为 `contents.json`。
3. **Phase 3: `plan-publish` & `run-publish-plan`** - 编排发布计划并执行。支持去重校验和每日 50 条发布上限控制。

---

## 构建与运行

### 环境准备
- 安装 Python 3.13+
- 安装 `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- 安装依赖: `uv sync`
- 浏览器驱动安装: `uv run playwright install chromium`

### 配置文件
项目根目录需存在 `.env` 文件，主要配置项包括：
- `XHS_POSTER_LLM_API_KEY`: LLM API 密钥（如 Moonshot）
- `XHS_POSTER_LLM_BASE_URL`: API 接口地址

### 常用命令
- **登录商家端**: `uv run xhs-poster login merchant` (需人工扫码)
- **拉取商品**: `uv run xhs-poster prepare-products --limit 10`
- **生成文案**: `uv run xhs-poster generate-content`
- **生成发布计划**: `uv run xhs-poster plan-publish`
- **执行发布**: `uv run xhs-poster run-publish-plan --count 1`
- **导出登录态**: `uv run xhs-poster auth export merchant --output ./merchant-state.json` (用于云服务器迁移)

---

## 开发与协作规范

### 核心目录结构
- `src/xhs_poster/`: 核心源代码。
    - `cli.py`: CLI 入口和子命令定义。
    - `phase*.py`: 各阶段核心逻辑实现。
    - `models.py`: 全局 Pydantic 数据模型定义。
    - `config.py`: 配置管理。
- `xiaohongshu-data/`: 运行时数据、状态文件及图片存储。
- `references/`: 文案风格参考及趋势分析原始数据。

### 重要文档
- **`SKILL.md` (关键)**: **AI Agent 执行的首要指南**。包含了阶段判断逻辑、数量控制、补跑规则等核心操作 SOP。
- **`AGENTS.md`**: 开发规范、代码结构、Prompt 技巧等。
- **`QUICKREF.md`**: 常用子命令速查表。

### 编码约定
- **类型提示**: 强制使用类型注解，采用 `from __future__ import annotations`。
- **数据结构**: 所有的 JSON 产物必须通过 `models.py` 中定义的 Pydantic 模型进行序列化和反序列化。
- **原子性操作**: 状态更新（如 `phase1-state.json`）应采用原子写入模式（写临时文件后重命名）。
- **无头浏览器**: 默认优先尝试无头模式，只有 `login` 相关操作或明确要求时才开启头模式。
