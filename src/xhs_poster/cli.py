from __future__ import annotations

import warnings
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
from .logging import log_error, log_summary
from .models import Phase3DedupScope, Phase3PlanMode
from .products.fetch import run_fetch_products
from .content.generate import build_generate_content_outputs
from .phase3 import (
    build_phase3_plan,
    list_phase3_candidates,
    run_phase3,
    run_phase3_plan,
)

APP_HELP = """小红书商家端自动发帖工具。输出为人读日志（stderr）+ 退出码（0 成功 / 1 失败 / 2 登录态失效）。

流程：fetch-products（拉商品与主图，支持断点续传）→ generate-content（生成文案）→ plan-publish（生成当天发布计划）→ run-publish-plan（执行当天计划）。
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
    images_per_product: Annotated[int, typer.Option("--images-per-product", help="兼容废弃参数：保留旧脚本调用，但不再限制每个商品下载图片数量")] = 3,
    force_download: Annotated[bool, typer.Option("--force-download", help="强制重新下载图片，覆盖已有")] = False,
) -> None:
    cmd = "fetch-products"
    warnings.warn(
        "--images-per-product 已废弃；fetch-products 现在总是下载商品主图和详情页图片的全部去重原图。",
        UserWarning,
        stacklevel=2,
    )
    try:
        result = run_fetch_products(
            limit=limit,
            images_per_product=images_per_product,
            force_download=force_download,
        )
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    if result.success_count == 0:
        log_error(
            f"[{cmd}] 未成功准备任何商品（失败 {result.failed_count} / 跳过 {result.skipped_count}），详情见 {result.progress_ref}"
        )
        raise typer.Exit(code=1)
    log_summary(
        f"[{cmd}] 就绪 {result.success_count} / 失败 {result.failed_count} / 跳过 {result.skipped_count}，状态={result.run_status}"
    )
    raise typer.Exit(code=0)


@app.command("generate-content", help="基于 products 与商品图片生成待发布笔记内容，写出 contents.json，并为每条草稿绑定 selected_image_paths；依赖 LLM 配置。")
def generate_content_command(
    contents_per_product: Annotated[int, typer.Option("--contents-per-product", help="每个商品生成的文案条数")] = 5,
) -> None:
    cmd = "generate-content"
    try:
        result = build_generate_content_outputs(contents_per_product=contents_per_product)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    total_drafts = sum(len(drafts) for drafts in result.contents.values())
    log_summary(
        f"[{cmd}] {result.total_products} 个商品生成 {total_drafts} 条草稿 → {result.contents_path}"
    )
    raise typer.Exit(code=0)


@app.command("publish-note", help="直接发布单条笔记的底层调试命令；默认不作为 AI 发布入口。需已登录商家端。")
def publish_note_command(
    product_id: Annotated[str | None, typer.Option("--product-id", help="要发笔记的商品 ID，不传则取 products 第一个")] = None,
    angle: Annotated[int | None, typer.Option("--angle", help="使用 contents.json 中该商品的第几条草稿（1～N）")] = None,
    title: Annotated[str | None, typer.Option("--title", help="直接指定标题（与 --content 一起用时忽略 contents.json）")] = None,
    content: Annotated[str | None, typer.Option("--content", help="直接指定正文（与 --title 一起用时忽略 contents.json）")] = None,
    topic_keywords: Annotated[list[str] | None, typer.Option("--topic-keyword", help="指定话题关键词，可多次传入；不传则从草稿 tags 提取全部 #")] = None,
    image_paths: Annotated[list[str] | None, typer.Option("--image-path", help="指定图片路径，可多次传入；不传则优先用草稿绑定的 selected_image_paths，再回退到 products")] = None,
) -> None:
    cmd = "publish-note"
    try:
        result = run_phase3(
            product_id=product_id,
            angle=angle,
            title=title,
            content=content,
            topic_keywords=topic_keywords,
            image_paths=image_paths,
        )
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    if result.publish_result.get("success"):
        log_summary(f"[{cmd}] 已发布：{result.title}")
        raise typer.Exit(code=0)
    log_error(f"[{cmd}] 发布失败或成功信号不明确：{result.title}")
    raise typer.Exit(code=1)


@app.command("list-publish-candidates", help="列出 contents.json 中全部可发布候选，并结合当日/历史发布记录标记是否可发布；用于编排前查看候选池。")
def list_publish_candidates_command(
    date: Annotated[str | None, typer.Option("--date", help="按指定日期评估去重，默认今天")] = None,
    exclude_published: Annotated[
        Phase3DedupScope, typer.Option("--exclude-published", help="去重范围：today 或 ever")
    ] = "today",
) -> None:
    cmd = "list-publish-candidates"
    try:
        result = list_phase3_candidates(
            date=date,
            exclude_published=exclude_published,
        )
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    eligible = sum(1 for candidate in result.candidates if candidate.eligible)
    log_summary(
        f"[{cmd}] 候选 {len(result.candidates)} 条，可发 {eligible} 条（去重范围={result.exclude_published}）"
    )
    raise typer.Exit(code=0)


@app.command("plan-publish", help="按顺序或随机策略生成并保存待发布计划，但不执行发布；推荐作为 AI 发布前的编排步骤。")
def plan_publish_command(
    mode: Annotated[Phase3PlanMode, typer.Option("--mode", help="计划模式：sequential 或 random")] = "sequential",
    count: Annotated[int | None, typer.Option("--count", help="计划选择的候选数量；不传则默认选择今天剩余全部可发布候选")] = None,
    date: Annotated[str | None, typer.Option("--date", help="按指定日期评估去重，默认今天")] = None,
    dedupe_scope: Annotated[
        Phase3DedupScope, typer.Option("--dedupe-scope", help="去重范围：today 或 ever")
    ] = "today",
    seed: Annotated[int | None, typer.Option("--seed", help="随机模式的随机种子")] = None,
) -> None:
    cmd = "plan-publish"
    try:
        result = build_phase3_plan(
            mode=mode,
            count=count,
            date=date,
            dedupe_scope=dedupe_scope,
            seed=seed,
        )
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    log_summary(
        f"[{cmd}] 计划 {result.count_selected} 篇 → {result.plan_path or 'publish-plan.json'}"
    )
    raise typer.Exit(code=0)


@app.command("run-publish-plan", help="执行已保存的发布计划，并写入当日 publish-records.json；AI 使用前应先确保当天已执行 plan-publish。")
def run_publish_plan_command(
    mode: Annotated[Phase3PlanMode, typer.Option("--mode", help="执行模式：sequential 或 random")] = "sequential",
    count: Annotated[int, typer.Option("--count", help="本次尝试发布的数量")] = 1,
    date: Annotated[str | None, typer.Option("--date", help="按指定日期评估去重，默认今天")] = None,
    dedupe_scope: Annotated[
        Phase3DedupScope, typer.Option("--dedupe-scope", help="去重范围：today 或 ever")
    ] = "today",
    seed: Annotated[int | None, typer.Option("--seed", help="随机模式的随机种子")] = None,
) -> None:
    cmd = "run-publish-plan"
    try:
        result = run_phase3_plan(
            mode=mode,
            count=count,
            date=date,
            dedupe_scope=dedupe_scope,
            seed=seed,
        )
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        raise typer.Exit(code=1)
    log_summary(
        f"[{cmd}] {result.count_succeeded}/{result.count_attempted} 成功，{result.count_failed} 失败"
    )
    # 发现 B：≥1 篇成功即 0；无 pending（no-op）也按 0；尝试了但全失败才 1。
    if result.count_attempted == 0 or result.count_succeeded >= 1:
        raise typer.Exit(code=0)
    raise typer.Exit(code=1)


app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
