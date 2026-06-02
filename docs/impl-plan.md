# 实施计划（里程碑级）

> 配套方案：`docs/refactor-plan.md`（v4 定稿）。本文件是可执行的逐里程碑计划。
> 工作方式：**一个 session 只做一个里程碑**，做完 `/clear` 再开下一个。

---

## 给每个新 session 的使用说明（务必先读）

`/clear` 之后你没有上一轮对话记忆。按以下步骤冷启动，**不要依赖任何对话历史**：

1. 读本文件的「进度跟踪」表，找到第一个未完成（⬜）的里程碑 = 你这次要做的。
2. 读该里程碑的完整小节（目标 / 前置状态 / 涉及文件 / 改动点 / 验收 / 回滚 / 收尾清单）。
3. 读 `docs/refactor-plan.md` 对应章节作为设计依据；读项目记忆 `memory/refactor-v2.md`（含隐藏耦合陷阱）。
4. 核对「前置状态」与当前代码是否一致（前序里程碑应已落在 git 里）。不一致就停下来问，不要硬上。
5. 编码 → 跑「验收」里的命令 → 全绿后执行「收尾清单」。
6. 收尾必须做：① git 提交（中文信息）② 把进度表该行改 ✅ ③ 必要时更新 `memory/`。提示用户 `/clear` 后继续下一个。

**铁律**：每个里程碑结束，`uv run python -m compileall src` 与冒烟必须绿，系统不留半坏中间态。只改与当前里程碑相关的代码。

---

## 进度跟踪

| M | 名称 | 状态 | 提交 |
|---|---|---|---|
| M0 | 基线固化 | ✅ | fda7355 |
| M1 | 文案链路精简 + originality 简化 | ⬜ | |
| M2 | consumer/SiteName 移除 | ⬜ | |
| M3 | 术语统一 + 命名重构（products/content） | ⬜ | |
| M4 | 输出契约改造 | ⬜ | |
| M5 | 发布会话复用 + publish 命名落位 | ⬜ | |
| M6 | 条件等待改造 | ⬜ | |
| M7 | 反检测间隔 | ⬜ | |
| M8 | 可观测性（--verbose + 失败证据） | ⬜ | |
| M9 | 健壮性 | ⬜ | |
| M10 | systemd 运行化 | ⬜ | |

> M2–M10 当前只有 §9 概览（见 refactor-plan.md）。**做到哪个，就在它前一个里程碑收尾时把它展开成下面 M0/M1 这样的详细小节**，避免一次写一大份很快过时的细节。

---

## M0 — 基线固化

**目标**：建立后续里程碑可独立对照的基线（耗时 + 文案样本）+ 冒烟脚本，使后续验证不必真发笔记。纯新增，不碰 `src/`，零风险。

**前置状态**：refactor 分支干净，无任何里程碑改动。

**涉及文件**（全部新增）：
- `scripts/smoke.sh` — 冒烟脚本
- `docs/baseline/contents-before.json` — 现状文案样本（发现 E）
- `docs/baseline/perf-baseline.md` — 现状耗时估算（发现 D）

**改动点**：
1. **冒烟脚本** `scripts/smoke.sh`：
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   uv run python -m compileall src
   uv run xhs-poster --help >/dev/null && echo "help OK"
   ```
   赋可执行权限。后续每个里程碑收尾都跑它。
2. **文案基线**：把当前 `xiaohongshu-data/contents.json` 复制到 `docs/baseline/contents-before.json`（若不存在则记录"无现状样本"）。M1 重写 prompt 后用它做人工对比。
3. **耗时基线**：读历史 `xiaohongshu-data/phase3/<date>/publish-records.json` 的 `attempted_at` 时间戳，算相邻成功记录的间隔，估算"现状单篇耗时"，写进 `docs/baseline/perf-baseline.md`。**不真发笔记**。若历史记录不足，注明"基线不可得，以 M5/M6 实测为准"。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 全绿；
- `docs/baseline/` 下两个文件存在且有内容。

**回滚**：删 `scripts/smoke.sh` 与 `docs/baseline/`。纯新增，无副作用。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿
- [ ] git 提交：`基线固化：冒烟脚本 + 文案/耗时基线`
- [ ] 进度表 M0 → ✅
- [ ] 提示用户 `/clear` 继续 M1

---

## M1 — 文案链路精简 + originality 简化

**目标**：删掉空转的文案模块，把内容生成收敛为「商品信息 + 视觉LLM图片分析 → 笔记」；originality 从硬闸门改为纯相似度查重。`generate-content` 仍产出可发布的 `contents.json`，且能挡住雷同稿。

> ⚠️ **隐藏耦合（务必先懂，详见 refactor-plan §4.5 发现 A）**：`originality.py` 的 `assert_publishable_originality` 是发布前硬闸门，要求每个 draft 带合格 `OriginalityCheck`，而该字段是旧 content_gen prompt 专门生成的。若只重写 prompt 不改闸门 → 后续 publish 阶段每篇抛错、发布全挂。所以 **prompt 重写与 originality 简化必须在本里程碑一起做**。

**前置状态**：M0 已完成（`docs/baseline/` 存在）。代码仍是 phase* 命名（M1 不改名），consumer 仍在（留 M2）。

**涉及文件**：
- **删除**：`src/xhs_poster/trend_signals.py`、`hot_notes.py`、`history_notes.py`、`image_facts.py`、`facts_builder.py`、`phase2_report.py`
- **改写**：
  - `phase2.py` — 去掉对上述模块的 import 与调用，主流程收敛为：load today-pool → 逐商品 `analyze_product_image_semantics`（视觉LLM，保留）→ `generate_product_contents`（精简）→ `allocate_image_paths`（配图，保留）→ 写 `contents.json`。删除写 `image-facts/hot-notes/product-facts/phase2-report` 等产物的代码。
  - `content_gen.py` — prompt 重写为纯「商品信息 + semantic_facts」；删除 history_style_refs / trend_analysis 入参与 `OriginalityCheck` schema 生成；保留 LLM 调用、JSON 解析、模板兜底。
  - `originality.py` — 删 `build_default_originality_check` 的主观闸门（core_input_type / supporting_differences）与历史查重（`_check_history_template_reuse`）；保留 `similarity_ratio` + `_check_generated_draft_reuse`；`assert_publishable_originality` 改为只基于"与已发布/已生成草稿的相似度阈值"。
  - `models.py` — 删随模块移除而无用的模型：`HotNotesAnalysis`、`HistoryStyleReference`、`ProductImageFacts`、`ProductSemanticFacts` 中如有 facts 专用字段酌情保留（vision 仍需）、`ProductFactsSnapshot`、`OriginalityCheck` 的主观字段。**保留** vision 相关模型与 `ContentDraft`。
  - `config.py` — 删 `image_facts_path / hot_notes_analysis_path / raw_hot_notes_path / product_facts_path / phase2_report_path / history_style_refs_path / trend_signals_path` 等无用路径。
- **注意**：`infer_search_keyword` 原在 `hot_notes.py`，删该文件时若 prompt 仍需关键词，内联到 `content_gen.py` 作小函数；否则删。`consumer.py` 在删 `hot_notes.py` 后变成孤儿，**本里程碑不删它**（留 M2 统一处理 consumer 概念）。

**验收**（新 session 可独立执行）：
- `bash scripts/smoke.sh` 绿；
- `grep -rE "trend_signals|hot_notes|history_notes|image_facts|facts_builder|phase2_report" src/` 无残留引用；
- `uv run xhs-poster generate-content`（需 `xiaohongshu-data/today-pool.json` 存在 + `.env` 有 LLM key）产出 `contents.json`，结构含 `contents[product_id]` 草稿列表且每条有 `selected_image_paths`；
- 人工抽检：对比 `docs/baseline/contents-before.json`，文案质量不劣于现状；
- 相似度查重生效：可临时构造两条高度相似草稿，确认 `assert_publishable_originality` 能挡（或加一个最小 pytest）。

**回滚**：`git revert` 本里程碑提交（删除的模块随之恢复）。

**收尾清单**：
- [ ] `bash scripts/smoke.sh` 绿 + `generate-content` 实跑通过
- [ ] grep 无残留
- [ ] git 提交：`文案链路精简：删空转模块 + originality 改相似度查重`
- [ ] 进度表 M1 → ✅；**展开 M2 的详细小节**（参照本节格式）
- [ ] 若发现新的隐藏耦合，更新 `memory/refactor-v2.md`
- [ ] 提示用户 `/clear` 继续 M2

---

## M2–M10

概览见 `docs/refactor-plan.md` §9。每个里程碑在其**前一个里程碑收尾时**展开为上面 M0/M1 的详细格式（目标 / 前置状态 / 涉及文件 / 改动点 / 验收 / 回滚 / 收尾清单）。这样保证展开时基于最新的代码现状，而非过早写死。
