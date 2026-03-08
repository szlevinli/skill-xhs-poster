from __future__ import annotations

import json
from typing import Annotated

import typer

from .auth import LoginRequiredError, login_site, probe_site_session
from .models import SiteName
from .phase1 import build_phase1_payload


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="小红书自动发帖工具 CLI（机器优先 JSON 输出）。",
)
auth_app = typer.Typer(add_completion=False, no_args_is_help=True, help="登录态探测。")
login_app = typer.Typer(add_completion=False, no_args_is_help=True, help="人工登录。")


def emit_json(payload: dict) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@auth_app.command("probe")
def auth_probe(
    site: Annotated[SiteName, typer.Argument(help="站点名称：merchant 或 consumer")],
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="探测超时时间（毫秒）")] = 8_000,
) -> None:
    payload = probe_site_session(site, timeout_ms=timeout_ms)
    emit_json(payload.model_dump(mode="json"))
    raise typer.Exit(code=0 if payload.authenticated else 2)


@login_app.command("merchant")
def login_merchant(
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="登录等待超时（毫秒，0 表示无限等待）")] = 0,
) -> None:
    _run_login("merchant", timeout_ms)


@login_app.command("consumer")
def login_consumer(
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", help="登录等待超时（毫秒，0 表示无限等待）")] = 0,
) -> None:
    _run_login("consumer", timeout_ms)


def _run_login(site: SiteName, timeout_ms: int) -> None:
    try:
        payload = login_site(site, timeout_ms=timeout_ms)
        emit_json(payload.model_dump(mode="json"))
        raise typer.Exit(code=0)
    except LoginRequiredError as exc:
        emit_json(exc.session.model_dump(mode="json"))
        raise typer.Exit(code=2)


@app.command("phase1")
def phase1_command(
    limit: Annotated[int, typer.Option("--limit", help="提取商品数量")] = 10,
    images_per_product: Annotated[int, typer.Option("--images-per-product", help="每个商品下载图片数")] = 3,
    force_download: Annotated[bool, typer.Option("--force-download", help="强制重新下载图片")] = False,
) -> None:
    payload, exit_code = build_phase1_payload(
        limit=limit,
        images_per_product=images_per_product,
        force_download=force_download,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)


app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
