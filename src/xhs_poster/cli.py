from __future__ import annotations

import json
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
from .models import Phase3DedupScope, Phase3PlanMode, SiteName
from .phase1 import build_phase1_payload
from .phase2 import build_phase2_payload
from .phase3 import (
    build_phase3_candidates_payload,
    build_phase3_payload,
    build_phase3_plan_payload,
    build_phase3_run_plan_payload,
)
from .trend_signals import build_trend_signals_payload


APP_HELP = """小红书商家端自动发帖工具。输出为 JSON，便于脚本或下游消费。

流程：prepare-products（拉商品与主图，支持 phase1-state 断点续传）→ prepare-trends（可选，生成趋势信号）→ generate-content（生成文案）→ plan-publish（生成当天发布计划）→ run-publish-plan（执行当天计划）。
首次使用需先执行 login merchant 完成本机登录；云服务器部署推荐使用 auth export / auth import 迁移登录态。"""
auth_app = typer.Typer(add_completion=False, no_args_is_help=True, help="探测商家端/用户端是否已登录。")
login_app = typer.Typer(add_completion=False, no_args_is_help=True, help="拉起浏览器，等待人工完成扫码登录。")

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=APP_HELP,
)


def emit_json(payload: dict) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@auth_app.command("probe", help="探测指定站点是否已有有效登录态；退出码 0 表示已登录，2 表示未登录或超时。")
def auth_probe(
    site: Annotated[SiteName, typer.Argument(help="站点：merchant（商家端）或 consumer（用户端）")],
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="请求超时毫秒数")] = 8_000,
) -> None:
    payload = probe_site_session(site, timeout_ms=timeout_ms)
    emit_json(payload.model_dump(mode="json"))
    raise typer.Exit(code=0 if payload.authenticated else 2)


@auth_app.command("export", help="从本地已登录 profile 导出 auth-state JSON，便于上传到云服务器。")
def auth_export(
    site: Annotated[SiteName, typer.Argument(help="站点：merchant（商家端）或 consumer（用户端）")],
    output: Annotated[Path | None, typer.Option("--output", help="导出的 auth-state 文件路径")] = None,
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="登录态校验毫秒数")] = 8_000,
) -> None:
    try:
        payload = export_site_auth_state(site, output_path=output, timeout_ms=timeout_ms)
        emit_json(payload.model_dump(mode="json"))
        raise typer.Exit(code=0)
    except LoginRequiredError as exc:
        emit_json(exc.session.model_dump(mode="json"))
        raise typer.Exit(code=2)


@auth_app.command("import", help="导入 auth-state JSON 到本机/服务器默认路径，并立即做无头校验。")
def auth_import(
    site: Annotated[SiteName, typer.Argument(help="站点：merchant（商家端）或 consumer（用户端）")],
    input_path: Annotated[Path | None, typer.Option("--input", help="待导入的 auth-state 文件路径")] = None,
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="导入后校验毫秒数")] = 8_000,
) -> None:
    try:
        payload = import_site_auth_state(site, input_path=input_path, timeout_ms=timeout_ms)
        emit_json(payload.model_dump(mode="json"))
        raise typer.Exit(code=0)
    except LoginRequiredError as exc:
        emit_json(exc.session.model_dump(mode="json"))
        raise typer.Exit(code=2)


@login_app.command("merchant", help="打开商家端登录页，等待扫码；成功后退出码 0，未完成则 2。成功后会写入本地 profile，可继续执行 auth export merchant 导出云端复用的 auth-state。")
def login_merchant(
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="等待登录的毫秒数，0 表示一直等")] = 0,
    debug_auth: Annotated[bool, typer.Option("--debug-auth", help="登录成功/失败时写出截图、HTML 与 cookie 摘要")] = False,
) -> None:
    _run_login("merchant", timeout_ms, debug_auth=debug_auth)


@login_app.command("consumer", help="打开用户端（小红书 App 同账号）登录页，等待扫码；成功后会写入本地 profile。当前流程主要用商家端。")
def login_consumer(
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="等待登录的毫秒数，0 表示一直等")] = 0,
    debug_auth: Annotated[bool, typer.Option("--debug-auth", help="登录成功/失败时写出截图、HTML 与 cookie 摘要")] = False,
) -> None:
    _run_login("consumer", timeout_ms, debug_auth=debug_auth)


def _run_login(site: SiteName, timeout_ms: int, *, debug_auth: bool = False) -> None:
    try:
        payload = login_site(site, timeout_ms=timeout_ms, debug_auth=debug_auth)
        emit_json(payload.model_dump(mode="json"))
        raise typer.Exit(code=0)
    except LoginRequiredError as exc:
        emit_json(exc.session.model_dump(mode="json"))
        raise typer.Exit(code=2)


@app.command("prepare-products", help="从商家后台同步商品图片，下载商品主图全部图片与详情页图片全部图片，实时写出 phase1-state.json，并收敛更新 today-pool.json；支持断点续传，需已登录商家端。")
def prepare_products_command(
    limit: Annotated[int, typer.Option("--limit", help="目标成功商品数量；会在当前列表中继续补位，直到凑够或候选耗尽")] = 10,
    images_per_product: Annotated[int, typer.Option("--images-per-product", help="兼容废弃参数：保留旧脚本调用，但不再限制每个商品下载图片数量")] = 3,
    force_download: Annotated[bool, typer.Option("--force-download", help="强制重新下载图片，覆盖已有")] = False,
) -> None:
    payload, exit_code = build_phase1_payload(
        limit=limit,
        images_per_product=images_per_product,
        force_download=force_download,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


@app.command("generate-content", help="基于 today-pool 与商品图片生成待发布笔记内容，写出 contents.json，并为每条草稿绑定 selected_image_paths；依赖 LLM 配置与可选 trend-signals。")
def generate_content_command(
    keyword: Annotated[str | None, typer.Option("--keyword", help="类目/趋势关键词，未指定则从商品名推断")] = None,
    contents_per_product: Annotated[int, typer.Option("--contents-per-product", help="每个商品生成的文案条数")] = 5,
    search_limit: Annotated[int, typer.Option("--search-limit", help="（预留，当前未使用）")] = 20,
    detail_limit: Annotated[int, typer.Option("--detail-limit", help="（预留，当前未使用）")] = 8,
) -> None:
    payload, exit_code = build_phase2_payload(
        keyword=keyword,
        contents_per_product=contents_per_product,
        search_limit=search_limit,
        detail_limit=detail_limit,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


@app.command("prepare-trends", help="从 references/history-notes 生成 trend-signals.json，供 generate-content 使用；可选，不跑则 generate-content 用本地兜底。")
def prepare_trends_command(
    keyword: Annotated[str | None, typer.Option("--keyword", help="趋势关键词，默认 发饰")] = None,
) -> None:
    payload, exit_code = build_trend_signals_payload(keyword=keyword)
    emit_json(payload)
    raise typer.Exit(code=exit_code)


@app.command("publish-note", help="直接发布单条笔记的底层调试命令；默认不作为 AI 发布入口。需已登录商家端。")
def publish_note_command(
    product_id: Annotated[str | None, typer.Option("--product-id", help="要发笔记的商品 ID，不传则取 today-pool 第一个")] = None,
    angle: Annotated[int | None, typer.Option("--angle", help="使用 contents.json 中该商品的第几条草稿（1～N）")] = None,
    title: Annotated[str | None, typer.Option("--title", help="直接指定标题（与 --content 一起用时忽略 contents.json）")] = None,
    content: Annotated[str | None, typer.Option("--content", help="直接指定正文（与 --title 一起用时忽略 contents.json）")] = None,
    topic_keywords: Annotated[list[str] | None, typer.Option("--topic-keyword", help="指定话题关键词，可多次传入；不传则从草稿 tags 提取全部 #")] = None,
    image_paths: Annotated[list[str] | None, typer.Option("--image-path", help="指定图片路径，可多次传入；不传则优先用草稿绑定的 selected_image_paths，再回退到 today-pool")] = None,
) -> None:
    payload, exit_code = build_phase3_payload(
        product_id=product_id,
        angle=angle,
        title=title,
        content=content,
        topic_keywords=topic_keywords,
        image_paths=image_paths,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


@app.command("list-publish-candidates", help="列出 contents.json 中全部可发布候选，并结合当日/历史发布记录标记是否可发布；用于编排前查看候选池。")
def list_publish_candidates_command(
    date: Annotated[str | None, typer.Option("--date", help="按指定日期评估去重，默认今天")] = None,
    exclude_published: Annotated[
        Phase3DedupScope, typer.Option("--exclude-published", help="去重范围：today 或 ever")
    ] = "today",
) -> None:
    payload, exit_code = build_phase3_candidates_payload(
        date=date,
        exclude_published=exclude_published,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


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
    payload, exit_code = build_phase3_plan_payload(
        mode=mode,
        count=count,
        date=date,
        dedupe_scope=dedupe_scope,
        seed=seed,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


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
    payload, exit_code = build_phase3_run_plan_payload(
        mode=mode,
        count=count,
        date=date,
        dedupe_scope=dedupe_scope,
        seed=seed,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
