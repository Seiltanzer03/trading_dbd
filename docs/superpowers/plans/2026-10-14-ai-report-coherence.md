# AI report coherence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Native implementation, one final independent review of this package. Existing user/AGENTS autonomous release authorization applies.

**Goal:** Publish one consistent manual decision and current recalculation coordinates, with truthful source and quality diagnostics.
**Architecture:** Keep the existing final publication, v19/v20 renderer, bounded market refresh and family-source validation. Repair their shared snapshot/report boundary; do not add providers, fitting or trading authority.
**Tech Stack:** Existing Python, JavaScript, pytest and GitHub CI/deploy.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, effective A/B/C/D plan, user's 2026-10-14 report and request to fix it.

## Global Constraints

- One management_decision; broker execution and confirmations stay manual.
- No source/risk/cost/causality/admission threshold changes or fabricated data.
- Existing Yandex archives, DB/WAL and history are preserved. No acquisition/retraining without new inputs.
- One focused owning profile, one final review/fix pass, mandatory exact-tree CI then authorized merge/deploy/acceptance.

## Review Focus

- A provider report produced before ensemble selection must not retain HOLD commands after an operational EXIT/extended selection.
- Repeated/executed decisions and operational HOLD guards must not produce another closing instruction.
- Long and short refreshes must update both R and price thresholds without changing provider timestamps.
- Stale legacy diagnostics must not look like final selection evidence or a fresh bridge calculation.
- Missing/invalid bar provenance stays unavailable with the actual failed check; numerical availability must not be called calibrated source quality.

### Task 1: coherent final manual report and refresh

**Files:** `seiltanzer/ai_verdict_v19.py`, `seiltanzer/ai_snapshot_market_refresh.py`, `seiltanzer/app.py`; regressions `tests/test_ai_report_coherence.py`, `tests/test_ai_snapshot_market_refresh.py`.
**Interfaces:** consume final manager.management_decision/unified_edge_ensemble and current trade_geometry; normalize_final_report returns consistent text without changing snapshot or authority; refresh_frozen_comparison returns a copied snapshot with fresh thresholds and best eligible Expected.

- [x] RED: old HOLD/strategy/after-execution text with final EXIT, partial and extended actions; executed/repeated decisions; long/short threshold coordinates; stale best_expected.
- [x] GREEN: replace all operational command sections from the final decision; label cached controls separately; preserve guards and independent model opinion. Repair stale continuity only when ensemble replaces the operational plan.
- [x] Run the owning report/refresh/API/guard profile, including one existing isolated API path.

### Task 2: truthful input and quality diagnostics

**Files:** `seiltanzer/edge_family_market.py`, `seiltanzer/ai_runtime_report_v20.py`, `seiltanzer/unified_edge_audit.py`, `seiltanzer/web/js/management_ui.js`; regressions `tests/test_ai_report_coherence.py`, `tests/test_edge_family_market.py` and existing UI smoke.
**Interfaces:** existing source admission remains identical; rejection exposes failed identity/clock checks. Quantitative-base display separates numerical availability from data reliability; weights are unchanged.

- [x] RED: missing/wrong/future/stale provenance is diagnosed specifically; quantitative-base 100% is not displayed as source accuracy.
- [x] GREEN: expose the checks without admitting their inputs; label unavailable historical paths and cached model clocks honestly.
- [x] One integrated focused profile and UI smoke, one fresh final read-only review; fix only Critical/Important findings once.
- [ ] Commit final tree, push PR, one mandatory CI; merge only green exact tree and verify actual deployment. Record dependencies still unavailable.

## Rulings

- Final deterministic publication owns commands; independent LLM opinion is retained as shadow. Cost if wrong: two executable plans or a repeated close.
- Cached legacy diagnostics are labeled, not recomputed with another expensive search. Cost: their old stress evidence cannot validate the new final choice.
- Source failures are diagnosed without manufacturing inputs or relaxing freshness. Cost: some families may remain unavailable until actual completed history arrives.
- Quant numerical availability and renormalized weight are not source confidence. Display correction leaves the decision mathematics unchanged.

## Evidence

Baseline local c98ac7464b20c79032203edec8a905de3f20005c/treec53f4952be121cc6427bd3236ead4eecf83407d4 equals accepted main83d9233093499717fdb6d6f2c902da4d273b746f. Seven exact-SHA contexts passed; deploy38529921391 and stage38529921521 succeeded. Mathematical/source/FOMC publications succeeded on this SHA. Old EDE38524714659 finished failure; no retry, cancellation, threshold change or successful-audit claim.

Fourteen primary regressions observed RED, then GREEN. Added executed-EXIT and stale-event-statistics coverage. Owning nine-file profile160passed39.18s, one existing Starlette warning; UI audit smoke PASS. Last renderer-label delta: focused report/manual-authority plus actual provider-transport API23passed4.02s, same warning. Source checks and weights remain unchanged. Old heavy event probabilities are cleared after quote repricing because no new event simulation occurred; unavailable is not zero. Current production read-only snapshot status was ready with canonical quote available; no user verdict/order/ACK was invoked.

Actual failed identity/clock checks are now exposed. This does not assert which check failed in the earlier user snapshot, nor repair unavailable external history by inventing it. Frozen bars/source clocks and historical fit admission remain required.

One fresh final independent review of c98ac74..35ddacc: no Critical/Important/Minor findings. Reviewer independently verified104changed-module tests5.03s and installed semantics-guard/v20 reproduction PASS (EXIT authoritative, independent LLM HOLD distinct, precise source failures visible). No review-driven code changes, repeat review or deferred minors. Final mandatory CI and release acceptance pending.
