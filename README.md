# 小红书商品笔记自动发布

小红书商家后台自动化工具：从商品管理拉取商品与主图、生成种草文案、编排并发布笔记。CLI 三阶段独立执行（prepare-products → generate-content → phase3 plan/run），通过 JSON 文件传递数据。`prepare-products` 现支持断点续传、收敛执行，并实时写出 `phase1-state.json`。`generate-content` 会优先对商品主图做语义分析，再结合趋势与风格参考生成文案。

## 前置条件

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）
- 商家端账号；内容生成需配置 LLM（如 Moonshot API Key）

## 安装（使用 uv）

上传到 GitHub 后，可用以下任一方式安装。

### 方式一：从 GitHub 直接安装为全局工具

安装后可直接在终端使用 `xhs-poster` 命令（请将 `OWNER/REPO` 替换为你的 GitHub 用户名/组织与仓库名）：

```bash
uv tool install 'xiaohongshu-product-poster @ git+https://github.com/szlevinli/skill-xhs-poster.git'
```

### 方式二：克隆仓库后在项目内使用

```bash
git clone https://github.com/szlevinli/skill-xhs-poster.git
cd REPO
uv sync
```

之后在项目目录下通过 `uv run xhs-poster` 执行子命令。

### 方式三：安装到当前环境的 site-packages

```bash
uv pip install 'xiaohongshu-product-poster @ git+https://github.com/szlevinli/skill-xhs-poster.git'
```

安装完成后可直接运行 `xhs-poster`（若该环境在 PATH 中）。

## 快速开始

```bash
# 1. 登录商家端（首次或 session 过期时）
uv run xhs-poster login merchant

# 2. 可选：导出 auth-state，供云服务器导入复用
uv run xhs-poster auth export merchant --output ./merchant-state.json

# 3. 拉取商品与主图（支持断点续传；可轮询 phase1-state.json 查看进度）
uv run xhs-poster prepare-products --limit 10 --images-per-product 3

# 4. 生成趋势信号（可选）
uv run xhs-poster prepare-trends --keyword 抓夹

# 5. 生成笔记内容
uv run xhs-poster generate-content --contents-per-product 5

# 6. 发布前先生成当天计划
uv run xhs-poster plan-publish --mode sequential --count 5

# 7. 真正执行发布
uv run xhs-poster run-publish-plan --count 1
```

更多子命令与参数见 `uv run xhs-poster --help`。完整流程与 AI 调用说明见 [SKILL.md](SKILL.md)，开发与贡献见 [AGENTS.md](AGENTS.md)。

## Phase3 行为说明

phase3 现在默认使用“计划文件 + 当日记录文件”：

- `plan-publish` 生成并保存 `xiaohongshu-data/publish-plan.json`
- `run-publish-plan` 只执行当天计划中的 `pending` 项
- 每次真实发布都会写入 `xiaohongshu-data/phase3/YYYY-MM-DD/publish-records.json`
- 当日去重和“是否达到 50 条上限”都基于当天记录文件中的成功记录判断
- AI 使用时，先检查当天是否已有 `publish-plan.json`；若没有，应先执行 `plan-publish`

## Phase1 行为说明

`prepare-products` 现在是收敛式执行：

- 每处理完一个商品就更新 `xiaohongshu-data/phase1-state.json`
- 只要已有成功商品，就会同步刷新 `xiaohongshu-data/today-pool.json`
- `--limit 10` 的语义是“尽量得到 10 个成功商品”，不是“只检查前 10 个商品”
- 若列表前面的商品没有可用主图，会继续尝试后续商品补位，直到成功商品达到目标数量或当前候选耗尽
- 已完成且图片齐全的商品，默认跳过，不会重复进入详情页或重新下载
- 只有缺失、失败或显式传 `--force-download` 时，才会重新抓取
- 每个商品最多保留前 3 张主图；若只有 1 或 2 张主图，也仍视为成功商品
- 只有 0 张主图的商品才会被排除在 phase2 和 phase3 之外
- 若本次只成功了一部分商品，`today-pool.json` 会带 `status: "partial"`，但仍可供后续阶段消费已成功商品

`phase1-state.json` 适合云服务器或 AI 编排层轮询，不建议依赖 stdout 流式事件。

## 云服务器部署登录态

推荐做法是先在 macOS 上登录，再把 auth-state JSON 上传到 Linux 云服务器。

macOS:

```bash
uv run xhs-poster login merchant
uv run xhs-poster auth export merchant --output ./merchant-state.json
```

Linux 云服务器:

```bash
uv run xhs-poster auth import merchant --input ./merchant-state.json
uv run xhs-poster auth probe merchant
uv run xhs-poster prepare-products --limit 10 --images-per-product 3
```

说明：

- 运行时会优先使用 `auth-state`，若不存在再回退到本地 Playwright profile。
- `auth import` 会自动把文件复制到默认 `xiaohongshu-data/auth/merchant-state.json`，无需手动放到 `auth/` 目录。
- `auth-state` 过期后，请回到 macOS 重新登录并重新导出。
- 兼容方案仍可上传 `xiaohongshu-data/profiles/merchant/`，但 macOS 到 Linux 的整份 profile 复用不保证稳定，建议仅作兜底。
- 云端执行 `prepare-products` 时，可轮询 `xiaohongshu-data/phase1-state.json` 判断当前处理进度和失败商品。

## 本地验证仅 auth-state 生效

如果要在本地验证“没有 profile 也能运行”，建议先备份原 profile，再只保留 `auth-state`：

```bash
mv xiaohongshu-data/profiles/merchant xiaohongshu-data/profiles/merchant.bak
mkdir -p xiaohongshu-data/profiles/merchant
uv run xhs-poster auth import merchant --input ./merchant-state.json
uv run xhs-poster auth probe merchant
uv run xhs-poster prepare-products --limit 1 --images-per-product 1
```

验证完成后，如需恢复本地 profile：

```bash
rm -rf xiaohongshu-data/profiles/merchant
mv xiaohongshu-data/profiles/merchant.bak xiaohongshu-data/profiles/merchant
```
