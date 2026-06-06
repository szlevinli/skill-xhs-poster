from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .auth import (
    LoginRequiredError,
    export_site_auth_state,
    import_site_auth_state,
    login_site,
    probe_site_session,
)
from .config import Settings
from .logging import log_error, log_summary
from .models import PublishDedupScope, PublishPlanMode
from .notify import (
    build_notifier,
    error_event,
    publish_summary_event,
    stage_done_event,
)
from .products.fetch import run_fetch_products
from .content.generate import build_generate_content_outputs
from .publish.plan import build_publish_plan
from .publish.session import run_publish_plan

APP_HELP = """小红书商家端自动发帖工具。输出为人读日志（stderr）+ 退出码（0 成功 / 1 失败 / 2 登录态失效）。

流程：fetch-products（拉商品与主图，支持断点续传）→ generate-content（生成文案）→ plan-publish（生成当天发布计划）→ publish（执行当天计划，整批共享一个浏览器会话）。
首次使用需先执行 login merchant 完成本机登录；云服务器部署推荐使用 auth export / auth import 迁移登录态。"""
auth_app = typer.Typer(add_completion=False, no_args_is_help=True, help="探测商家端是否已登录。")
login_app = typer.Typer(add_completion=False, no_args_is_help=True, help="拉起浏览器，等待人工完成扫码登录。")

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=APP_HELP,
)


@auth_app.command("probe", help="探测商家端是否已有有效登录态；退出码 0 表示已登录，2 表示未登录或超时。")
def auth_probe(
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="请求超时毫秒数")] = 8_000,
) -> None:
    payload = probe_site_session(timeout_ms=timeout_ms)
    if payload.authenticated:
        log_summary(f"[auth probe] 已登录（{payload.site}）")
        raise typer.Exit(code=0)
    log_error(f"[auth probe] 未登录：{payload.message}")
    raise typer.Exit(code=2)


@auth_app.command("export", help="从本地已登录 profile 导出 auth-state JSON，便于上传到云服务器。")
def auth_export(
    output: Annotated[Path | None, typer.Option("--output", help="导出的 auth-state 文件路径")] = None,
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="登录态校验毫秒数")] = 8_000,
) -> None:
    try:
        payload = export_site_auth_state(output_path=output, timeout_ms=timeout_ms)
    except LoginRequiredError as exc:
        log_error(f"[auth export] 登录态失效：{exc.session.message}")
        raise typer.Exit(code=2)
    log_summary(f"[auth export] {payload.message}")
    raise typer.Exit(code=0)


@auth_app.command("import", help="导入 auth-state JSON 到本机/服务器默认路径，并立即做无头校验。")
def auth_import(
    input_path: Annotated[Path | None, typer.Option("--input", help="待导入的 auth-state 文件路径")] = None,
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="导入后校验毫秒数")] = 8_000,
) -> None:
    try:
        payload = import_site_auth_state(input_path=input_path, timeout_ms=timeout_ms)
    except LoginRequiredError as exc:
        log_error(f"[auth import] 登录态失效：{exc.session.message}")
        raise typer.Exit(code=2)
    log_summary(f"[auth import] {payload.message}")
    raise typer.Exit(code=0)


@login_app.command("merchant", help="打开商家端登录页，等待扫码；成功后退出码 0，未完成则 2。成功后会写入本地 profile，可继续执行 auth export 导出云端复用的 auth-state。")
def login_merchant(
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="等待登录的毫秒数，0 表示一直等")] = 0,
    debug_auth: Annotated[bool, typer.Option("--debug-auth", help="登录成功/失败时写出截图、HTML 与 cookie 摘要")] = False,
) -> None:
    try:
        payload = login_site(timeout_ms=timeout_ms, debug_auth=debug_auth)
    except LoginRequiredError as exc:
        log_error(f"[login] 未完成登录：{exc.session.message}")
        raise typer.Exit(code=2)
    log_summary(f"[login] {payload.message}")
    raise typer.Exit(code=0)


@app.command("fetch-products", help="从商家后台同步商品图片，下载商品主图全部图片与详情页图片全部图片，实时写出 products-state.json，并收敛更新 products.json；支持断点续传，需已登录商家端。")
def fetch_products_command(
    limit: Annotated[int, typer.Option("--limit", help="目标成功商品数量；会在当前列表中继续补位，直到凑够或候选耗尽")] = 10,
    force_download: Annotated[bool, typer.Option("--force-download", help="强制重新下载图片，覆盖已有")] = False,
) -> None:
    cmd = "fetch-products"
    notifier = build_notifier(Settings())
    try:
        result = run_fetch_products(
            limit=limit,
            force_download=force_download,
        )
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        notifier.send(error_event(cmd, exc.session.message, exit_code=2))
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        notifier.send(error_event(cmd, str(exc), exit_code=1))
        raise typer.Exit(code=1)
    if result.success_count == 0:
        log_error(
            f"[{cmd}] 未成功准备任何商品（失败 {result.failed_count} / 跳过 {result.skipped_count}），详情见 {result.progress_ref}"
        )
        notifier.send(
            error_event(
                cmd,
                f"未成功准备任何商品（失败 {result.failed_count} / 跳过 {result.skipped_count}）",
                exit_code=1,
            )
        )
        raise typer.Exit(code=1)
    log_summary(
        f"[{cmd}] 就绪 {result.success_count} / 失败 {result.failed_count} / 跳过 {result.skipped_count}，状态={result.run_status}"
    )
    notifier.send(
        stage_done_event(
            cmd,
            "商品信息获取完成",
            [
                ("就绪", str(result.success_count)),
                ("失败", str(result.failed_count)),
                ("跳过", str(result.skipped_count)),
            ],
        )
    )
    raise typer.Exit(code=0)


@app.command("generate-content", help="基于 products 与商品图片生成待发布笔记内容，写出 contents.json，并为每条草稿绑定 selected_image_paths；依赖 LLM 配置。")
def generate_content_command(
    contents_per_product: Annotated[int, typer.Option("--contents-per-product", help="每个商品生成的文案条数")] = 5,
) -> None:
    cmd = "generate-content"
    notifier = build_notifier(Settings())
    try:
        result = build_generate_content_outputs(contents_per_product=contents_per_product)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        notifier.send(error_event(cmd, str(exc), exit_code=1))
        raise typer.Exit(code=1)
    total_drafts = sum(len(drafts) for drafts in result.contents.values())
    log_summary(
        f"[{cmd}] {result.total_products} 个商品生成 {total_drafts} 条草稿 → {result.contents_path}"
    )
    notifier.send(
        stage_done_event(
            cmd,
            "内容生成完成",
            [
                ("商品", str(result.total_products)),
                ("草稿", str(total_drafts)),
            ],
        )
    )
    raise typer.Exit(code=0)


@app.command("plan-publish", help="按顺序或随机策略生成并保存待发布计划，但不执行发布；推荐作为 AI 发布前的编排步骤。")
def plan_publish_command(
    mode: Annotated[PublishPlanMode, typer.Option("--mode", help="计划模式：sequential 或 random")] = "sequential",
    count: Annotated[int | None, typer.Option("--count", help="计划选择的候选数量；不传则默认选择今天剩余全部可发布候选")] = None,
    date: Annotated[str | None, typer.Option("--date", help="按指定日期评估去重，默认今天")] = None,
    dedupe_scope: Annotated[
        PublishDedupScope, typer.Option("--dedupe-scope", help="去重范围：today 或 ever")
    ] = "today",
    seed: Annotated[int | None, typer.Option("--seed", help="随机模式的随机种子")] = None,
) -> None:
    cmd = "plan-publish"
    notifier = build_notifier(Settings())
    try:
        result = build_publish_plan(
            mode=mode,
            count=count,
            date=date,
            dedupe_scope=dedupe_scope,
            seed=seed,
        )
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        notifier.send(error_event(cmd, str(exc), exit_code=1))
        raise typer.Exit(code=1)
    log_summary(
        f"[{cmd}] 计划 {result.count_selected} 篇 → {result.plan_path or 'publish-plan.json'}"
    )
    notifier.send(
        stage_done_event(
            cmd,
            "发布计划就绪",
            [("计划", f"{result.count_selected} 篇")],
        )
    )
    raise typer.Exit(code=0)


@app.command("publish", help="执行已保存的发布计划，整批共享一个浏览器会话，并写入当日 publish/<date>/records.json；AI 使用前应先确保当天已执行 plan-publish。")
def publish_command(
    mode: Annotated[PublishPlanMode, typer.Option("--mode", help="执行模式：sequential 或 random")] = "sequential",
    count: Annotated[int, typer.Option("--count", help="本次尝试发布的数量")] = 1,
    date: Annotated[str | None, typer.Option("--date", help="按指定日期评估去重，默认今天")] = None,
    dedupe_scope: Annotated[
        PublishDedupScope, typer.Option("--dedupe-scope", help="去重范围：today 或 ever")
    ] = "today",
    seed: Annotated[int | None, typer.Option("--seed", help="随机模式的随机种子")] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="全程实时打印每步进度，并为每篇（含成功）保留 trace/steps 证据")
    ] = False,
) -> None:
    cmd = "publish"
    notifier = build_notifier(Settings())
    try:
        result = run_publish_plan(
            mode=mode,
            count=count,
            date=date,
            dedupe_scope=dedupe_scope,
            seed=seed,
            verbose=verbose,
        )
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        notifier.send(error_event(cmd, exc.session.message, exit_code=2))
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        notifier.send(error_event(cmd, str(exc), exit_code=1))
        raise typer.Exit(code=1)
    log_summary(
        f"[{cmd}] {result.count_succeeded}/{result.count_attempted} 成功，{result.count_failed} 失败"
    )
    # 批次摘要总发（含部分失败 / 全失败），卡片级别由 publish_summary_event 按成败判定。
    notifier.send(publish_summary_event(result))
    # 发现 B：≥1 篇成功即 0；无 pending（no-op）也按 0；尝试了但全失败才 1。
    if result.count_attempted == 0 or result.count_succeeded >= 1:
        raise typer.Exit(code=0)
    raise typer.Exit(code=1)


@app.command(
    "notify-failure",
    hidden=True,
    help="内部命令：供 systemd OnFailure 调用，发送一条飞书告警，覆盖进程被超时/OOM/信号杀掉、Python 自身来不及发通知的盲区。",
)
def notify_failure_command(
    unit: Annotated[str, typer.Option("--unit", help="触发告警的 systemd unit 名")] = "unknown",
    result: Annotated[
        str | None, typer.Option("--result", help="systemd $SERVICE_RESULT，如 timeout / signal / oom-kill")
    ] = None,
) -> None:
    notifier = build_notifier(Settings())
    reason = f"systemd 判定异常退出（result={result or '未知'}）" if result else "systemd 判定异常退出"
    message = (
        f"{reason}；通常是超时被 RuntimeMaxSec 杀、被 OOM 杀或崩溃。"
        f"请检查 journalctl --user -u {unit}"
    )
    notifier.send(error_event(unit, message, exit_code=-1))
    # 通知器本身永不抛（失败隔离）；此命令始终 0 退出，避免拖垮 OnFailure 单元。
    raise typer.Exit(code=0)


app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
