# Portable family geometry implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Pool affine-equivalent supported action labels without absolute price starvation or relaxed geometry/risk admission.
**Architecture:** Optional shared normalized R descriptor and bounded original geometry proof, validated independently by producer, trainer, packaging and consumer. Extended price actions remain exact; legacy contracts remain supported.
**Tech Stack:** Existing Python/NumPy/pytest, no new dependencies.
**Spec:** `docs/superpowers/specs/2026-10-04-portable-family-geometry-design.md`.

## Global constraints

- Version `edge-family-affine-r-geometry-v1`; normalized ratios round to 12 decimals only for descriptor identity.
- Portable actions CLOSE_10/CLOSE_25/CLOSE_50/EXIT plus existing TIME_STOP relative binding; HOLD is replay control.
- Preserve all source, cost, independent position, purge, OOS, JSON/model bounds and production action IDs.
- Equal normalized descriptors only; no learned geometry conditioning/extrapolation in this version.
- Missing genuine inputs remain missing; fixtures are artificial contracts, not broker outcomes.

## Review focus

1. Unknown extension version must reject instead of legacy fallback.
2. Proof/hash valid but candidate not in original matrix must reject training.
3. Different exposure, R state or topology must not pool despite affine price equivalence.
4. Explicit portable metadata on an extended price action must reject.
5. Numerical cancellation/tiny original risk must not admit nonfinite or inconsistent geometry.

## Task 1: Coupled portable producer/trainer/consumer contract

**Files:** Create `seiltanzer/edge_family_geometry.py` and `tests/test_edge_family_portable_geometry.py`; modify dataset/training/adapters and `scripts/run_edge_family_pipeline.py`; update affected existing tests, design and roadmap.
**Interfaces:** `portable_geometry(snapshot: dict) -> dict`, `geometry_descriptor_sha256(dict) -> str`, `geometry_evidence(snapshot: dict) -> dict`, `portable_row_reason(row: dict) -> str | None`, `portable_artifact_reason(artifact: dict) -> str | None`, `portable_geometry_matches(artifact: dict, snapshot: dict) -> bool`. Exact extension fields and 48 KiB evidence/32 KiB descriptor bounds are in the spec. Existing geometry_sha256 means normalized hash only with the explicit version; otherwise legacy exact hash.

- [ ] Write failing tests for affine long/short equivalence, immutable original labels/candidates, grouped training and current runtime admission; unknown versions/forged proof/incompatible geometry/nonportable actions/legacy behavior.
- [ ] Run new tests with `../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_portable_geometry.py`; record expected RED reasons before product code.
- [ ] Implement shared contract, default supported-action producer integration, independent trainer validation and artifact propagation, runtime equality admission, packaging validation; keep extended actions exact.
- [ ] Run focused portable/dataset/training/adapters/TIME_STOP/pipeline tests; fix relevant regressions, record actual counts and RED→GREEN.
- [ ] Independent read-only final review against baseline; fix Important/Critical findings with regression evidence.
- [ ] Root updates roadmap/test evidence, verifies diff and runs full suite once; disclose existing Python3.12 SQLite/WAL baseline failure if present.
- [ ] Root commits/pushes draft PR; full mandatory green official CI and exact final SHA/tree review before authorized merge; await main CI/deploy/readiness/functional/public/source/math checks.

Execution: one isolated writer owns the shared contract; reviewer read-only;
root owns Git, integration and release. Prior worktrees are preserved.
