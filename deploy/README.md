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

**编辑 5 个文件，替换占位符**（`~/.config/systemd/user/` 下）：

- `xhs-prepare.service` / `xhs-publish.service` / `xhs-notify-failure@.service`：
  - `%h/xhs-poster` → 仓库实际路径（在 home 下保持默认即可；否则写绝对路径，两处 `WorkingDirectory` 与 `EnvironmentFile`）
  - `%h/.local/bin/uv` → 第 1 步 `which uv` 的实际路径（所有 `ExecStart` 行）
- `xhs-prepare.timer`：`<HH:MM>` → 准备批钟点，如 `03:00`
- `xhs-publish.timer`：两行 `<HH:MM>` → 两个黄金时段，如 `12:30` 和 `20:30`

> `xhs-notify-failure@.service` 是模板单元（带 `@`），无需 enable、无 timer——由两个主单元的 `OnFailure=` 在它们异常退出时自动实例化拉起，只需替换上面两个占位符即可。

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

## 7. 飞书通知（可选）

无人值守时，让每个命令收尾主动推一条飞书群消息：准备各阶段完成（绿卡）、发布批次摘要（成功/失败计数）、任意命令异常退出（红卡，含掉登录 exit 2）。**不配置则零行为**——`FEISHU_WEBHOOK_URL` 未设时全程静默，不发任何请求。

设计与协议细节见 `docs/feishu-notify-design.md`。

**启用步骤**：

1. 飞书群 → 群设置 → 群机器人 → 添加「自定义机器人」，复制 webhook URL。建议同时开启「签名校验」拿到 secret（比仅靠 URL 保密更稳）。
2. 在仓库根 `~/xhs-poster/.env` 追加（被现有 `EnvironmentFile=` 自动加载，**无需改任何 service unit**）：

   ```
   FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
   # 机器人开了签名校验再加：
   FEISHU_WEBHOOK_SECRET=xxxxxxxx
   # 多套部署时区分来源：
   FEISHU_NOTIFY_LABEL=xhs-prod
   ```

   可选降噪：`FEISHU_NOTIFY_EVENTS=publish_summary,error`（只留发布摘要与错误，准备阶段成功静默）。可选超时：`FEISHU_NOTIFY_TIMEOUT_SECONDS=5`。

3. VPS 需能出网到 `open.feishu.cn:443`；内网受限环境要放行。

> **安全**：webhook URL 即密钥，谁拿到谁能往群里发消息。`.env` 已在 `.gitignore`，勿提交、勿外泄。
>
> **失败隔离**：通知是副信道，发送失败（网络不通 / 4xx / 5xx / 超时）只在 stderr（journal）留一行日志，**绝不改变命令退出码或发布结果**。

**systemd `OnFailure=` 兜底进程暴毙（已内置）**

应用层通知靠 Python 在进程内发卡，抓不到硬崩溃（OOM、SIGKILL、被 `TimeoutStartSec` 超时杀、解释器在发卡前就挂）。这类「进程暴毙」由 systemd 层兜底：两个主单元都带 `OnFailure=xhs-notify-failure@%N.service`（`%N`＝不含 `.service` 后缀的单元名），进 failed 态时自动拉起 `xhs-notify-failure@.service` 发一条飞书 error 卡。两层互补——应用层报业务结果，systemd 层报进程暴毙。

配套的进程保护（两个主单元均已设）：

- `TimeoutStartSec=`（publish 1h / prepare 2h）：整批硬墙。**注意必须用 `TimeoutStartSec` 而非 `RuntimeMaxSec`**——`Type=oneshot` 默认无启动超时（这正是当初能挂 11 小时的底层原因），而 `RuntimeMaxSec` 对 oneshot 直接被忽略（systemd 会打印 `MaxRuntimeSec= has no effect ... Ignoring`）。超时 systemd 强杀，根治「publish 卡死把第二个黄金时段也堵掉」。publish 内还有**单篇看门狗**（`PUBLISH_ITEM_TIMEOUT_SECONDS`，默认 240s）：单篇超时即弃该篇、重建会话继续，正常整批根本碰不到这道墙。
- `SuccessExitStatus=1 2`（publish）/ `SuccessExitStatus=1`（prepare）：业务失败（exit 1/2，已自行发飞书摘要）不算 systemd 失败，避免与应用层通知重复、避免误触发 `OnFailure`。只有被杀/超时/崩溃才进 failed 态、才补发暴毙告警。
- `TimeoutStopSec=30` + `KillMode=control-group`：强杀时连同 chromium 子进程树一起收尾，30s 内不退再 SIGKILL，不留僵尸浏览器。
