# Repository Guidelines

## Project Structure & Module Organization

Core code lives in `src/xhs_poster/`. Keep new modules phase-oriented and focused: `phase1.py` prepares product data, `phase2.py` generates note content, and `phase3.py` publishes a single draft. Shared CLI entrypoints are in `src/xhs_poster/cli.py`; configuration and paths are centralized in `src/xhs_poster/config.py`.

Runtime outputs are written to `xiaohongshu-data/`, including `phase1-state.json`, `today-pool.json`, `contents.json`, `publish-log.json`, and downloaded images under `xiaohongshu-data/images/{product_id}/`. `prepare-products` is resumable and convergent: `--limit` means target successful products, the workflow should continue scanning later candidates to backfill when earlier products have no usable images, completed products should be skipped unless `--force-download` is used, and partial success should still produce an up-to-date `today-pool.json`. Each product may keep 1-3 images; only products with zero usable images should be excluded from phase2/phase3. Reference inputs for trend preparation live in `references/history-notes/`. Treat `references/` and `xiaohongshu-data/` as data directories, not source code.

## Build, Test, and Development Commands

- `uv sync`: install Python 3.13 dependencies from `pyproject.toml` and `uv.lock`.
- `uv run xhs-poster --help`: inspect the full CLI surface.
- `uv run xhs-poster login merchant`: open the merchant login flow and persist the Playwright profile.
- `uv run xhs-poster prepare-products --limit 10 --images-per-product 3`: converge toward 10 successful products, downloading up to 3 images per product.
- `uv run xhs-poster generate-content --contents-per-product 5`: generate content from product/image inputs; only pass `--keyword` when you need to override auto-inference.
- `uv run xhs-poster publish-note --angle 1`: publish one draft.
- `uv run python -m compileall src`: lightweight smoke check for syntax and import errors.

## Coding Style & Naming Conventions

Use 4-space indentation, type hints, and `from __future__ import annotations` in new Python modules to match the existing code. Prefer small, explicit functions and keep CLI payloads JSON-friendly. Use `snake_case` for files, functions, and variables; reserve `PascalCase` for Pydantic models and settings classes. Keep user-facing command help concise and operational.

## Testing Guidelines

There is no formal automated test suite yet. Validate changes with targeted CLI runs and inspect the generated JSON artifacts in `xiaohongshu-data/`. At minimum, run `uv run python -m compileall src` before opening a PR. For workflow changes, document the exact command used for verification, for example `uv run xhs-poster generate-content --contents-per-product 5`.

## Commit & Pull Request Guidelines

Recent commits use short, task-based Chinese summaries such as `完成阶段2`. Follow that style: concise, imperative, and scoped to one change. PRs should include a clear summary, affected phase or module, manual verification commands, and screenshots when browser automation or publishing behavior changes. Call out any `.env` or data-layout changes explicitly.
