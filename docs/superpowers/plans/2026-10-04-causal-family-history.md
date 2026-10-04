# Causal family history implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox syntax.

**Goal:** Produce causal positioning changes and related-market historical facts from actually received observations.
**Architecture:** Two bounded optional source extensions and pure transforms, integrated by one writer across collector/adapter/frozen dataset/runtime. Preserve legacy facts and every existing admission gate.
**Tech Stack:** Existing Python/pytest; no new dependencies or requests.
**Spec:** `docs/superpowers/specs/2026-10-04-causal-family-history-design.md`.

## Global Constraints

- Position extension `edge-family-position-history-v1`: 2048 UTF-8 JSON bytes, depth 8, at most 8 previous records.
- Intermarket extension `edge-family-intermarket-history-v1`: 4096 UTF-8 JSON bytes, depth 8, at most 3 actual Coinbase USD series, exactly 7 consecutive one-minute closes.
- Price lag is 60 seconds, return length 300 seconds; feature windows 360 / 300 seconds. Breadth requires 2 admissible peers.
- Source bundle bound 1,000,000 bytes; selected review facts bound 8,000 bytes. No new GETs, fitting, dependencies, proxy authority or historical backdating.
- Preserve original source packets/labels, frozen PIT snapshots, candidate IDs, risk/cost and OOS/whole-trade/purge floors. Missing remains missing.

## Review Focus

1. Receipt after immutable bundle capture but before later review must reject.
2. Child synthetic/context/horizon declaration must not disappear in derived provenance.
3. Correct hash with mismatched kind/unit/provider/quote/series must not admit.
4. Missing own-asset peer or gapped windows must not produce zero-filled breadth/returns.
5. Compact producer facts must actually pass the unchanged 8,000 byte consumer boundary.

## Task 1: Coupled historical transforms and prospective integration

**Files:** Create `seiltanzer/edge_family_history.py`, `tests/test_edge_family_history.py`; modify `edge_family_sources.py`, `edge_family_adapters.py` and affected source/runtime/dataset tests; change runtime/dataset only if required to enforce constituent admission. Update roadmap and this spec with actual implementation details.
**Interfaces:** `position_history_features(source: dict, cutoff: float) -> dict`; `intermarket_history_features(source: dict, cutoff: float) -> dict`. Both return features/feature_provenance/rejections as specified. Collector freezes optional proof; adapter recomputes before _add; dataset/runtime retain constituent guards.

- [x] Write failing deterministic tests for correct delta, 60s-lagged 300s returns, ordered-pair differences, related peer breadth and original source immutability; malformed proof/identities, duplicates/gaps, future clocks, applicability and bounds/legacy checks.
- [x] Run `../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_history.py`; save actual RED failures before product implementation.
- [x] Implement the pure bounded contracts, compact no-extra-GET collection, strict adapter admission and provenance/window propagation. Keep COT publication upper bounds and all existing mappings unchanged.
- [x] Add RED→GREEN end-to-end tests for unchanged runtime byte cap, bundle capture vs review clock, child declarations and frozen dataset/path noninterference; retain legacy source facts.
- [x] Run focused history/source/adapter/source-runtime/dataset/training/pipeline tests; record command/count and fix relevant regressions.
- [x] Commit scoped changes and report. Root dispatches fresh task review, then fresh whole-feature review; fix Important/Critical findings with regression evidence.
- [ ] Root runs one final broad Python/frontend verification, records known unchanged Python3.12 SQLite/WAL baseline if present, then draft PR and mandatory green exact-SHA official CI/WebKit before authorized merge.
- [ ] Root verifies mainCI/deploy/readiness/functional/public/source/math and records residual code/data; do not claim learned lead-lag/valuation/event completion or profitability.

Execution: parallel read-only domain audits are completed; one isolated writer owns shared source contracts. Root owns Git/remotes, reviewers are read-only. Preserve all prior worktrees and execution evidence. Document approval pauses are waived by the existing user execution record.

## Final whole-feature review follow-up (actual implementation)

The root's final review ruling extended the coupled contract to existing trainer
row admission. Added a shared bounded normalized-history validator and recursive
row applicability check, explicit producer history version/hash manifest, and
cross-feature source ID/body/series consistency. Legacy absence remains compatible;
recognized partial/unknown proof is rejected. No fitting or OOS thresholds changed.
Actual producer→dataset output passes; independently supplied contradictory proof
with a recomputed dataset checksum rejects. Final fix RED/GREEN evidence and the
scoped commit are recorded in task-1-report.md; fresh scoped review and final
exact-tree official CI/WebKit/release remain root gates.
