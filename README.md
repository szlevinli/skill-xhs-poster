# 小红书商品笔记自动发布

小红书商家后台自动化工具：从商品管理拉取商品与主图、生成种草文案、发布笔记。CLI 三阶段独立执行（prepare-products → generate-content → publish-note），通过 JSON 文件传递数据。

## 前置条件

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）
- 商家端账号；内容生成需配置 LLM（如 Moonshot API Key）

## 安装（使用 uv）

上传到 GitHub 后，可用以下任一方式安装。

### 方式一：从 GitHub 直接安装为全局工具

安装后可直接在终端使用 `xhs-poster` 命令（请将 `OWNER/REPO` 替换为你的 GitHub 用户名/组织与仓库名）：

```bash
uv tool install 'xiaohongshu-product-poster @ git+https://github.com/OWNER/REPO.git'
```

### 方式二：克隆仓库后在项目内使用

```bash
git clone https://github.com/OWNER/REPO.git
cd REPO
uv sync
```

之后在项目目录下通过 `uv run xhs-poster` 执行子命令。

### 方式三：安装到当前环境的 site-packages

```bash
uv pip install 'xiaohongshu-product-poster @ git+https://github.com/OWNER/REPO.git'
```

安装完成后可直接运行 `xhs-poster`（若该环境在 PATH 中）。

## 快速开始

```bash
# 1. 登录商家端（首次或 session 过期时）
uv run xhs-poster login merchant

# 2. 可选：导出 auth-state，供云服务器导入复用
uv run xhs-poster auth export merchant --output ./merchant-state.json

# 3. 拉取商品与主图
uv run xhs-poster prepare-products --limit 10 --images-per-product 3

# 4. 生成趋势信号（可选）
uv run xhs-poster prepare-trends --keyword 抓夹

# 5. 生成笔记内容
uv run xhs-poster generate-content --keyword 抓夹 --contents-per-product 5

# 6. 发布一条笔记
uv run xhs-poster publish-note --angle 1
```

更多子命令与参数见 `uv run xhs-poster --help`。完整流程与 AI 调用说明见 [SKILL.md](SKILL.md)，开发与贡献见 [AGENTS.md](AGENTS.md)。

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
