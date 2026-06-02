# 小红书商品笔记自动发布

小红书商家后台自动化工具：从商品管理拉取商品图片、用 LLM 生成种草文案、编排并发布笔记。

CLI 流水线四阶段，逐个独立执行：

```
fetch-products → generate-content → plan-publish → publish
```

登录/鉴权（`login` / `auth`）是贯穿各阶段的前置能力，不算流水线阶段。各阶段通过 `xiaohongshu-data/` 下的 JSON 文件衔接；命令对外只输出**人读日志（stderr）+ 退出码**（`0` 成功 / `1` 失败 / `2` 登录态失效；`publish` 批量发布 ≥1 篇成功即 `0`），不再向 stdout 输出 JSON。

> **所有命令必须在仓库根目录执行**——`.env` 与数据目录按进程 cwd 解析。

## 前置条件

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）
- 商家端账号；内容生成需配置 LLM（如 Moonshot API Key）

最低 `.env`（放仓库根）：

```
MOONSHOT_API_KEY=<key>
```

LLM 相关变量支持多种别名（文案 `MOONSHOT_API_KEY` / `LLM_API_KEY` / `XHS_POSTER_LLM_API_KEY`，视觉 `VISION_LLM_API_KEY`，模型 `MOONSHOT_MODEL` / `VISION_LLM_MODEL`），详见 `src/xhs_poster/config.py`。

## 安装（使用 uv）

### 方式一：从 GitHub 直接安装为全局工具

```bash
uv tool install 'xiaohongshu-product-poster @ git+https://github.com/szlevinli/skill-xhs-poster.git'
```

安装后可直接在终端使用 `xhs-poster` 命令。

### 方式二：克隆仓库后在项目内使用（推荐开发）

```bash
git clone https://github.com/szlevinli/skill-xhs-poster.git
cd skill-xhs-poster
uv sync
```

之后在仓库根目录下通过 `uv run xhs-poster` 执行子命令。

### 方式三：安装到当前环境的 site-packages

```bash
uv pip install 'xiaohongshu-product-poster @ git+https://github.com/szlevinli/skill-xhs-poster.git'
```

## 快速开始

```bash
# 1. 登录商家端（首次或登录态失效时）
uv run xhs-poster login merchant

# 2. 可选：导出 auth-state，供云服务器导入复用
uv run xhs-poster auth export merchant --output ./merchant-state.json

# 3. 拉取商品与主图（断点续传；可轮询 products-state.json 查看进度）
uv run xhs-poster fetch-products --limit 10

# 4. 生成笔记文案（每个商品多角度草稿）
uv run xhs-poster generate-content --contents-per-product 5

# 5. 生成当天发布计划（不传 --count 默认选今天剩余全部可发候选）
uv run xhs-poster plan-publish

# 6. 执行发布（整批共享一个浏览器会话）
uv run xhs-poster publish --count 1
```

更多子命令与参数见 `uv run xhs-poster --help`。开发与贡献见 [AGENTS.md](AGENTS.md)，VPS 定时部署见 [deploy/README.md](deploy/README.md)。

## 数据产物（`xiaohongshu-data/`）

运行时产物（非源码），各阶段通过这些文件衔接：

| 文件 | 阶段 | 说明 |
|------|------|------|
| `products.json` | fetch-products 输出 | 当日商品池，带 `date` 字段 |
| `products-state.json` | fetch-products 检查点 | 断点续传状态，可轮询查看进度/失败商品 |
| `contents.json` | generate-content 输出 | 文案内容，带 `date` 字段 |
| `publish-plan.json` | plan-publish 输出 | 当日发布计划 |
| `publish/<date>/records.json` | publish 记录 | 当日发布账本（成功/失败） |
| `publish/<date>/evidence/<product>-<angle>-<HHMMSS>/` | publish 证据 | 按篇子目录：screenshot.png + page.html + steps.jsonl + trace.zip（默认仅失败保留 trace；`--verbose` 每篇全留） |
| `images/<product_id>/` | fetch-products 下载 | 商品主图与详情图 |
| `image-analysis.json` | 视觉分析缓存 | 长期缓存，避免重复调用视觉 LLM，不要随意清除 |

## 关键行为

- **`fetch-products --limit N`** 语义是"得到 N 个成功商品"，不是"只看前 N 个"：前面商品没可用图会继续向后补位，直到达标或候选耗尽；只有 0 张图的商品才排除。已成功且落盘的商品默认跳过，`--force-download` 才重抓。`products-state.json` 实时更新，适合云端/编排层轮询。
- **`plan-publish`** 不传 `--count` 时默认选今天剩余全部可发候选；支持 `--mode sequential|random`、`--dedupe-scope today|ever`。
- **`publish`** 整批共享一个浏览器会话提速，每篇之间插入随机反检测间隔；`publish_session_recycle_every`（config）控制每 N 篇重建会话（默认很大=整批一会话）。中途检测到掉登录会整批中止（退出码 2），已成功篇保持续传可恢复态。`--verbose` 全程实时打印每步并为每篇（含成功）保留 trace/steps 证据。
- **阶段完成判断**：对应文件存在 **且** `date == 今天` 且结构合法。
- 登录态运行时优先用 `auth-state`，不存在再回退本地 Playwright profile。

## 云服务器部署登录态

VPS 无头运行依赖 **auth-state**（不是本地 profile）。先在已登录的 macOS 导出，拷到 VPS 导入：

macOS：

```bash
uv run xhs-poster login merchant
uv run xhs-poster auth export merchant --output ./merchant-state.json
```

Linux 云服务器：

```bash
uv run xhs-poster auth import merchant --input ./merchant-state.json
uv run xhs-poster auth probe merchant            # 退出码 0=已登录可无头运行，2=需重新登录
uv run xhs-poster fetch-products --limit 10
```

说明：

- `auth import` 会自动把文件复制到默认 `xiaohongshu-data/auth/merchant-state.json`，无需手动放置。
- `auth-state` 会过期；过期后相关命令退出码 2，需回到 macOS 重新登录并重新导出。
- 整份 profile 从 macOS 到 Linux 复用不保证稳定，建议仅作兜底。

完整的 systemd 定时编排（准备批 + 发布批）见 [deploy/README.md](deploy/README.md)。

## 本地验证仅 auth-state 生效

验证"没有 profile 也能运行"——先备份原 profile，只保留 `auth-state`：

```bash
mv xiaohongshu-data/profiles/merchant xiaohongshu-data/profiles/merchant.bak
mkdir -p xiaohongshu-data/profiles/merchant
uv run xhs-poster auth import merchant --input ./merchant-state.json
uv run xhs-poster auth probe merchant
uv run xhs-poster fetch-products --limit 1
```

验证完成后恢复本地 profile：

```bash
rm -rf xiaohongshu-data/profiles/merchant
mv xiaohongshu-data/profiles/merchant.bak xiaohongshu-data/profiles/merchant
```
