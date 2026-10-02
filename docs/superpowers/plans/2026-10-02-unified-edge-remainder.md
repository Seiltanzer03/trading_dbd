# Unified edge remainder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Track steps with checkboxes.

**Goal:** Закрыть подтверждённый остаток реализации исходного ТЗ, сохраняя отдельные статусы недоступных реальных входов и последующей проверки эффективности.

**Architecture:** Read-only production export → bounded immutable archive → causal family dataset → grouped purged validation → pinned runtime context. Comparison развивается независимо. Один integrator объединяет отдельные worktrees; пользователь 2026-10-02 прямо поручил подготовить план на основе существующего ТЗ и сразу начать исполнение, без повторных согласований документов.

**Tech Stack:** Python 3.11+, NumPy, pytest, существующие GitHub Actions и FastAPI; без новых runtime зависимостей.

**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`; сверка `docs/UNIFIED_EDGE_REMAINING_IMPLEMENTATION.md`; детализация pipeline `docs/superpowers/specs/2026-10-02-family-model-pipeline-design.md`.

## Global Constraints

- 13 configured instruments, 12 actions; один общий selector, неизменные hard risk gates и веса.
- Unknown costs остаются unknown; ACK receipt не является broker fill; modeled/path/actual evidence разделяются.
- Source publication/receipt не позже T0, exact identity/horizon/mapping; no network/training in API.
- Archive максимум 512 episodes, 96 MB decoded; существующий export 32 reviews, snapshot 2 MB, path 6000, transport 32 MB.
- Runtime context максимум 48 KB, SHA256 и точный deployment SHA; модели не активируются при отсутствующей validation.
- OOS минимум 20 observations, 2 folds, по 10 distinct trade groups в каждом validation fold; purged horizon, positive proper-score gain, подтверждённые costs.
- Источники не обходить, private snapshots/account IDs не коммитить; synthetic fixtures только CI.
- Merge только после review и полного mandatory green CI; затем exact-SHA deploy/readiness/smoke. Никаких тестовых реальных ордеров.

## Review Focus

1. Конфlicting identity с разными hashes: исключить обе версии, не оставлять случайно первую (Task 1).
2. Повторные reviews и пересекающиеся horizons: считать groups и исключить leakage (Task 2).
3. Net label из assumed/missing costs: исключить, не дополнять нулями (Task 3).
4. Action geometry mismatch при runtime admission: отклонить affected forecast (Task 4).
5. Overlapping reviews в comparison: не изображать portfolio drawdown/profit (Task 5).

## Task 1: Bounded longitudinal archive

**Files:** Create `seiltanzer/edge_family_archive.py`, `tests/test_edge_family_archive.py`.
**Interfaces:** `assemble_archive(exports: list[dict], previous: dict | None = None) -> dict`; output version `edge-family-archive-v1`, `episodes` original export records, `dataset_sha256` hash canonical episodes, `exclusions`, `evictions`. Episodes сохраняют snapshot_json/hash и real path; snapshots являются сохранёнными source records, не ссылки на истёкшие artifacts.

- [ ] RED: тесты immutable deterministic merge, dedupe, identity hash conflict исключает обе версии, snapshot hash mismatch, oversized payload, whole-episode eviction, future export/capture chronology, synthetic demo exclusion.
- [ ] Run: `python -m pytest -q tests/test_edge_family_archive.py`; подтвердить отсутствие implementation.
- [ ] Implement с bounded JSON serialization/finite data и hash verification; вход ≤32 reviews/export, ≤512 episodes, ≤96 MB; сортировка `(captured_ts, review_id)`.
- [ ] GREEN: профильные тесты; self-review, commit в отдельной ветке, report; независимый review.

## Task 2: Deterministic grouped trainer

**Files:** Create `seiltanzer/edge_family_training.py`, `tests/test_edge_family_training.py`.
**Interfaces:** `train_family_models(dataset: dict, *, trained_at: float) -> dict`; dataset version `edge-family-dataset-v1`, `dataset_sha256`, `rows`; row keys `trade_id`, `review_id`, `captured_ts`, `label_end_ts`, `instrument`, `family_id`, `horizon_minutes`, `geometry_sha256`, `features`, `feature_windows_sec`, `action`, `delta_net_r`, `costs_verified`, `synthetic`. Output `models` list of consumer-compatible artifacts, `diagnostics`, `available`, `reason`.

- [ ] RED: fewer than 20 validation rows / 2 folds / 10 groups per fold → no model; repeated trade not independent; horizon overlap purged; nonfinite/future label rejected; positive MSE gain versus train-only mean baseline; negative gain no artifact; deterministic hashes/coefficients; different geometry not pooled.
- [ ] Run isolated tests and record RED.
- [ ] Implement NumPy fixed ridge (lambda=1), intercept unpenalized, training-only scaling translated back to raw coefficients; at most 32 numeric features, ≥20 train groups plus two chronological validation blocks ≥10 groups each. Purge training rows whose label_end reaches validation start and omit any trade appearing in validation. Validate each fold before final artifact.
- [ ] Artifact uses existing model/feature contracts, `regime=ALL`, fixed existing pool `mathematical_edge`, `score_scale_r=1`, HOLD=0 at consumer, positive aggregate MSE gain, geometry hash, train/validation clocks and truthful counts. Final fit only train before final untouched validation; no future/refit misuse. Input row claims independently validated again; unverified/synthetic costs fail.
- [ ] GREEN: profile tests, self-review, commit/report, independent review.

## Task 3: Causal dataset and labels

**Files:** Create `seiltanzer/edge_family_dataset.py`, `tests/test_edge_family_dataset.py`.
**Interfaces:** `build_family_dataset(archive: dict) -> dict` produces Task 2 row contract. `family_geometry_sha256(snapshot: dict) -> str` canonical hash of frozen policy inputs, remaining exposure and candidate geometry; shared with runtime Task 4.

- [ ] RED: future receipts/revisions, wrong instrument/horizon, context-only provenance, assumed costs, no verified broker cost context, missing HOLD continuation, conflicting archive hash, partial path; exact real candidate replay delta vs HOLD.
- [ ] Use existing `build_edge_family_evidence`, `collect_candidates`, `observed_replay`; remove model artifacts before feature extraction. Only verified complete `execution_cost_context_audit` plus matching snapshot identity/units/cost provenance; recheck loader contract rather than trust a Boolean alone. Existing source admission must not be weakened.
- [ ] Labels for supported concrete candidates on same retained real path/horizon; exact geometry hash includes stop/take/rungs/current/max R/exposure and action parameter matrix, excluding T0 absolute clock but preserving relative TIME_STOP deadline. Unknown geometry fields exclude training. label_end is observed horizon/terminal endpoint, not assumed exporter clock.
- [ ] GREEN: isolated tests, self-review, commit/report, independent review.

## Task 4: Artifact packaging, runtime geometry guard, off-host workflow

**Files:** Create `scripts/run_edge_family_pipeline.py`, `tests/test_edge_family_pipeline.py`, `.github/workflows/edge-family-training.yml`; Modify `seiltanzer/edge_family_adapters.py`, related tests and deploy integration only if actual approved model exists.
**Interfaces:** CLI `--reviews`, optional `--archive`, `--expected-sha`, `--output-dir`; output bounded archive/dataset/diagnostics/runtime_context with deployment SHA, document hash and model matrix.

- [ ] RED: full fixture path archive→dataset→trainer→consumer; empty/insufficient real inputs diagnostics and zero active models; oversized runtime context refusal; wrong geometry excludes model; old external artifacts without geometry retain existing contract checks.
- [ ] Add optional `geometry_sha256` admission equality via Task 3 helper; use all causal features/lineage already checked by consumer. No catch-and-promote fallback.
- [ ] CLI strict bounded JSON input, filesystem-only processing, no network/provider/DB writes; write through temporary file/atomic replace. Synthetic datasets cannot produce publishable runtime artifacts.
- [ ] Workflow exact immutable SHA checkout, bounded read-only export and optional accumulated archive input, profile tests, pipeline, artifacts; no production model activation when unavailable and no credentials/private dataset uploads beyond existing restricted workflow artifacts. Document retention/restore inputs; no claim that 14-day artifacts are permanent archive.
- [ ] GREEN: profile then broad Python/frontend checks; independent integration review.

## Task 5: Full descriptive comparison report

**Files:** Modify `scripts/run_unified_edge_comparison.py`, `tests/test_unified_edge_comparison.py`, `tests/test_unified_ack_comparison.py`.
**Interfaces:** Retain old output keys; extend `summary` to include legacy_control and replayed ablations; add `grouped_summary`, selected action distribution including HOLD, review transition metrics, cost breakdown availability and ACK cancellation observations.

- [ ] RED: legacy uses identical paired cohort; ablation chosen candidate independently replayed (not current winner reused); HOLD distribution; missing scheme/path reduces explicit denominator, no KeyError; groups instrument/regime/horizon/family; overlapping reviews no portfolio drawdown.
- [ ] Run profile tests to capture RED.
- [ ] Implement separate model-scenario/path-counterfactual/actual-ACK tables. Report per-trade chronological decision changes and policy reversals with definition, not inferred fills. Rapid cancellation threshold fixed/report-visible 60 sec; materiality threshold explicitly configured/report-visible, no optimal-value claim. Missing actual delay/cost remains null. Portfolio drawdown unavailable absent coherent settled ledger; descriptive worst episode available.
- [ ] GREEN: profile tests, self-review, commit/report, independent review.

## Task 6: Executing-account identity/units producer

**Files:** Existing `execution_cost_context.py`, snapshot producer in `app.py`, new bounded read-only position-context module and tests if valid actual source is available.

- [ ] Inventory existing position/trade inputs and legitimate broker identity records; record which exact producer can independently bind broker/account/trade, currency/quantity/risk units and receipt clocks.
- [ ] If actual feed is available, RED missing/future/mismatched identity & units; implement bounded pinned position import independent of cost file; snapshot integration → cost reprice checks → GREEN/review.
- [ ] If no executing-account input exists, preserve missing status with exact fields/provider requirements. Do not guess the user's broker or invent a configured connection. Complete other independent tasks; keep activation blocker open.

## Task 7: Eight-family source/feature coverage

**Files:** Existing source collector/adapters, source workflow and family tests; exact scope after inventory.

- [ ] Inventory each §7 feature against actual stored source fields; distinguish implementable transforms from absent inputs (earnings/consensus/OI/fund flows/forwards/CFD mappings/physical VRP).
- [ ] For each available exact causal input, RED feature/provenance/clock/mapping case → implement existing adapter → GREEN/review. Every unavailable field has machine-readable reason; no generic zero-filled forecast.
- [ ] Record configured-instrument/source matrix in roadmap. Paid/legal/provider choices remain explicit input dependencies, not silent completion.

## Task 8: Integrate, verify, release and preserve path

- [ ] Integrate reviewed commits in dependency order 1→3→2→4; Task 5 independent. Resolve shared contracts once; update checklist from actual evidence.
- [ ] Profile changed tests, full Python suite and frontend smoke. Disclose pre-existing Python3.12 SQLite WAL failure if reproduced; do not suppress it. Full official CI Python3.11/real WebKit at exact final SHA.
- [ ] Fresh final diff review against original §7/§9 and pipeline design. Fix Important/Critical findings and recheck changed code.
- [ ] Commit/push/draft PR; mandatory green CI → ready/merge; await exact-SHA auto deploy, readiness, smoke and production checks under existing authorization.
- [ ] Update roadmap with SHA/PR/tests, technical readiness and actual-input blockers separately; effectiveness and weights remain later work.

## Execution record

2026-10-02: исходное ТЗ и остаток уже проанализированы; дополнительные document-approval pauses отменены прямым указанием пользователя готовить план и немедленно работать. Старую dirty worktree сохраняем. Новая integration worktree `trading_remaining` основана на docs checkpoint `9eabce1`, включающем production tree PR #391. Нативный Python без pytest; существующая project venv `trading_quotes/.venv/bin/python` используется для проверок.
