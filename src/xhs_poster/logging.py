from __future__ import annotations

import sys


def log_summary(message: str) -> None:
    """面向人的单行结果/进度，输出到 stderr（systemd journal 可读）。"""
    print(message, file=sys.stderr, flush=True)


def log_error(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def log_step(message: str, *, verbose: bool) -> None:
    """verbose 模式下实时打印单步进度到 stderr；非 verbose 静默。"""
    if verbose:
        print(message, file=sys.stderr, flush=True)
