# 云服务器认证迁移方案

## Summary
将当前“本机 Playwright persistent profile 登录”的认证方式扩展为“双轨制”：

- 推荐路径：在 macOS 上完成 `merchant` 登录后，导出单文件 `auth-state.json`，上传到 Linux 云服务器；服务器无头运行时优先使用该文件完成自动认证。
- 兼容路径：仍保留现有 `xiaohongshu-data/profiles/merchant` profile 目录方案，必要时可压缩上传作为兜底。
- 运行时策略固定为：`auth-state` 优先，`profile` 兜底；本次仅覆盖 `merchant` 站点。

## Key Changes
### 认证模型与配置
- 在 `Settings` 中新增 merchant auth-state 路径配置，默认落到 `xiaohongshu-data/auth/merchant-state.json`。
- 在 `SessionInfo` 中补充认证来源字段，区分 `auth_state` / `profile` / `missing`，让 `auth probe` 和失败提示能明确当前使用哪种登录态。
- 保持现有 `merchant_profile_dir` 不变，避免破坏已有本地流程。

### 浏览器上下文创建
- 重构 `browser.py` / `auth.py` 的上下文创建逻辑，拆成两种模式：
  - 登录模式：继续使用 `launch_persistent_context(user_data_dir=...)`，仅用于 macOS 人工扫码登录。
  - 运行模式：优先使用 `browser.new_context(storage_state=...)` 加载 auth-state；若文件不存在或验证失败，再回退到现有 persistent profile。
- `probe_site_session()`、`require_authenticated_session()`、`merchant_context()` 统一走新的认证解析入口，避免 phase1 / phase3 各自分叉。
- 明确约束：服务器 headless 执行不依赖 profile 必须可跨平台复用；profile 只作为兼容兜底，不承诺 macOS 到 Linux 一定可用。

### CLI 能力
- 新增 `auth export merchant`
  - 从当前本地 `merchant` profile 启动有状态浏览器上下文。
  - 校验已登录后导出 `storage_state` 到 auth-state JSON。
  - 输出 JSON 结果，包含导出路径、站点、认证状态。
- 新增 `auth import merchant`
  - 从指定文件或默认路径读取 auth-state JSON。
  - 复制到项目约定目录，并立即做一次 headless probe 验证。
  - 输出 JSON 结果，失败时返回明确错误码。
- 保留现有 `login merchant` 不变，作为 auth-state 生成前提。
- 保留现有 `auth probe merchant`，但输出中增加“当前认证来源”和“建议动作”。
- README 补充部署流程：
  - macOS 登录
  - 导出 auth-state
  - 上传到服务器
  - 服务器执行 `auth import merchant`
  - 服务器执行 `auth probe merchant`
  - 再运行 `prepare-products` / `publish-note`

### 错误处理与提示
- auth-state 文件缺失、结构损坏、内容过期、导入后 probe 失败时，统一返回 `LOGIN_REQUIRED` 或专用认证错误，并提示“回到 macOS 重新登录并重新导出”。
- 当运行时走到 profile 兜底路径时，在输出里明确标记，避免用户误以为当前是标准跨平台方案。

## Public Interfaces
- 新增 CLI：
  - `uv run xhs-poster auth export merchant [--output PATH]`
  - `uv run xhs-poster auth import merchant [--input PATH]`
- 扩展 `auth probe merchant` 输出字段：
  - `auth_source`
  - `auth_state_path`（如适用）
- 配置新增：
  - `XHS_POSTER_MERCHANT_AUTH_STATE_PATH` 或等价 `Settings` 字段

## Test Plan
- 本地 macOS：
  - 执行 `uv run xhs-poster login merchant` 后，执行 `uv run xhs-poster auth export merchant`
  - 删除或忽略运行时 profile，仅保留 auth-state，执行 `uv run xhs-poster auth probe merchant`
- Linux 云服务器：
  - 上传导出的 JSON，执行 `uv run xhs-poster auth import merchant`
  - 执行 `uv run xhs-poster auth probe merchant`，确认 headless 通过
  - 执行 `uv run xhs-poster prepare-products --limit 1 --images-per-product 1`
- 回退验证：
  - 移除 auth-state，仅保留 profile，确认仍可按旧逻辑运行
- 失效验证：
  - 提供损坏 JSON 或过期登录态，确认返回可读错误，不进入假成功
- 基础检查：
  - `uv run python -m compileall src`

## Assumptions
- 云服务器目标是 Linux，无头运行，不在服务器侧做人机登录。
- 当前业务主流程只依赖 `merchant` 站点，`consumer` 暂不纳入实现。
- 用户接受“auth-state 为推荐标准方案，profile 为兼容兜底”的行为定义。
- 登录态续期策略不做自动刷新；一旦失效，流程固定为“macOS 重新登录 -> 重新导出 -> 上传服务器”。
