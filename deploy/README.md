# VPS 部署（用户级 systemd timer）

把 xhs-poster 跑成 VPS 上自动定时的两条流水线：

| Unit | 频次 | 做什么 |
|------|------|--------|
| `xhs-prepare`（service + timer） | 每天 1 次（非黄金时段） | `fetch-products` → `generate-content` → `plan-publish` 顺序执行，任一步失败即整链停 |
| `xhs-publish`（service + timer） | 每天 2 次（黄金时段） | `publish --count 20`，headless 发布当日剩余可发笔记 |

运行方式 = `uv run`；systemd 层级 = **用户级**（`systemctl --user` + linger），无需 root 即可常驻。

---

## 0. 前置：登录态（必须先做）

VPS 无头运行依赖 **auth-state**（不是本地 profile）。在你**已登录商家后台的 macOS** 上导出，拷到 VPS 导入：

```bash
# macOS（仓库根目录执行）
uv run xhs-poster login merchant            # 若尚未登录
uv run xhs-poster auth export merchant --output ./merchant-state.json
scp ./merchant-state.json <user>@<vps>:~/xhs-poster/

# VPS（仓库根目录执行）
uv run xhs-poster auth import merchant --input ./merchant-state.json
uv run xhs-poster auth probe merchant       # 应输出登录有效、可无头运行
```

> auth-state 会过期。过期后 `publish` 退出码 2，timer 不会自动重试——需回 macOS 重新 export 再 import。

## 1. VPS 环境

```bash
# 1.1 装 uv（若没有），确认绝对路径
curl -LsSf https://astral.sh/uv/install.sh | sh
which uv                                     # 记下绝对路径，多半是 ~/.local/bin/uv

# 1.2 拉代码到 home 下（unit 默认假设 ~/xhs-poster；放别处则改 unit 里的路径）
git clone <repo-url> ~/xhs-poster
cd ~/xhs-poster
uv sync

# 1.3 装 Playwright 浏览器（与项目同一 venv）；系统依赖那行需要一次 sudo
uv run playwright install chromium
sudo $(which uv) run playwright install-deps chromium   # 或参照官方装齐系统库
```

浏览器装到本用户 `~/.cache/ms-playwright`，会被 `configure_playwright_browser_path` 自动发现，无需额外配置。

## 2. `.env`

放在**仓库根**（`~/xhs-poster/.env`），最低配置：

```
MOONSHOT_API_KEY=<key>
```

> `EnvironmentFile=` 要求**纯 `KEY=value`**：不要 `export`、不要 shell 引号/续行。应用本身也会按 WorkingDirectory 读同一个 `.env`，两条路径一致即可。

## 3. 安装 unit

```bash
mkdir -p ~/.config/systemd/user
cp deploy/xhs-*.service deploy/xhs-*.timer ~/.config/systemd/user/
```

**编辑 4 个文件，替换占位符**（`~/.config/systemd/user/` 下）：

- `xhs-prepare.service` / `xhs-publish.service`：
  - `%h/xhs-poster` → 仓库实际路径（在 home 下保持默认即可；否则写绝对路径，两处 `WorkingDirectory` 与 `EnvironmentFile`）
  - `%h/.local/bin/uv` → 第 1 步 `which uv` 的实际路径（所有 `ExecStart` 行）
- `xhs-prepare.timer`：`<HH:MM>` → 准备批钟点，如 `03:00`
- `xhs-publish.timer`：两行 `<HH:MM>` → 两个黄金时段，如 `12:30` 和 `20:30`

> `OnCalendar` 还支持星期/日期等更复杂表达，详见 `man systemd.time`；`systemd-analyze calendar "12:30"` 可预览下次触发时间。

## 4. 启用

```bash
# 让用户级 unit 在未登录时也能跑（关键，否则关掉 SSH 后 timer 不触发）
loginctl enable-linger <user>

systemctl --user daemon-reload
systemctl --user enable --now xhs-prepare.timer xhs-publish.timer
systemctl --user list-timers                 # 确认 NEXT 时间正确
```

## 5. 验收（少量真跑，避免污染线上）

```bash
# 5.1 准备批跑通（产出 products.json / contents.json / publish-plan.json）
systemctl --user start xhs-prepare.service
journalctl --user -u xhs-prepare.service -n 50 --no-pager

# 5.2 发布批：先手动小批真发 1~2 篇确认链路（不要直接 start 那条 --count 20 的 service）
cd ~/xhs-poster
uv run xhs-poster publish --count 1          # 看 stderr 全程日志 + 退出码
ls xiaohongshu-data/publish/$(date +%F)/     # records.json + 失败时 evidence/

# 5.3 定时器最终态
systemctl --user list-timers xhs-prepare.timer xhs-publish.timer
```

退出码语义（journal 里可见，timer 不因失败重试）：
- `0` = 准备批全步成功 / 发布批 ≥1 篇成功（含无 pending 的 no-op）
- `1` = 发布批尝试了但全失败
- `2` = 掉登录 → 需重新导入 auth-state（见第 0 步）

## 6. 日常运维

```bash
journalctl --user -u xhs-publish.service -f          # 跟踪发布日志
systemctl --user start xhs-publish.service           # 手动触发一次发布批（--count 20）
systemctl --user disable --now xhs-prepare.timer xhs-publish.timer   # 停用
```

排错全程实时日志：临时 `uv run xhs-poster publish --count 1 --verbose`，每篇含成功都落 `trace.zip` + `steps.jsonl` 到 `xiaohongshu-data/publish/<date>/evidence/`。
