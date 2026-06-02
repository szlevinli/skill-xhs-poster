#!/usr/bin/env bash
set -euo pipefail
uv run python -m compileall src
uv run xhs-poster --help >/dev/null && echo "help OK"
