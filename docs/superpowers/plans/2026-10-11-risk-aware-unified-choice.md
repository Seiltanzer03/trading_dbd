# Risk-aware unified choice Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; one final read-only reviewer. The user explicitly authorized autonomous implementation and release of the proposed existing-flow improvements.

**Goal:** Make all twelve actions compete consistently on expected benefit or bounded protective benefit, retaining every available expert inside the original 100% budget.

**Architecture:** Reuse the current evaluator, frozen scenario bank, registry and manual decision path. One small benefit contract serves independent extended admission and common ranking. Quantitative utility includes bounded tail protection; expert preferences are relative to HOLD, with transparent attribution and a small score margin against unnecessary changes.

**Tech Stack:** Python, numpy, existing JavaScript audit renderer and GitHub CI/deploy.

**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, sections 1–5, 9–12, and the user's 2026-10-11 instruction to implement the proposed decision improvements autonomously.

## Global Constraints

- Preserve authoritative-price, parameter, hard-risk, strategy-event, reliability and manual execution checks. No broker orders, fabricated forecasts, new providers or research searches.
- Preserve balanced 40/15/15/15/15 and llm20 budgets, freshness and lineage discounting. Missing scores remain null. No minimum fabricated vote or automatic LLM budget expansion.
- Reuse existing economics, costs and simulated paths; no new broker-cost work. Expected/CVaR remain unchanged by utility normalization.
- One focused RED/GREEN profile, one final reviewer, full exact-tree CI and actual automatic deployment/readiness/smoke/publication receipts.

## Design decisions

- Benefit: robust Expected improvement above the existing indifference band, or a protective action with matched worst-seed CVaR gain >=0.05R and robust Expected loss no worse than that band. EXTEND_TAKE remains a profit-seeking action.
- Both independent extended evaluation and common-bank admission enforce the same contract. A rejected source/risk/parameter gate is never resurrected.
- Quant utility: ΔExpected + 0.35 × ΔCVaR, with positive tail credit capped at the existing indifference band. Negative risk changes retain their penalty. 0.35 reuses the existing manager's risk tradeoff; it is a stated starting policy, not a calibrated optimum.
- Fixed quant scale 0.12R, independent of irrelevant candidates. Explicit expert HOLD scores are subtracted; deviations are scaled down only if they exceed [-1,1]. Sparse opinions remain sparse. Show raw/reference/relative scores and exact per-family attribution.
- Retain HOLD unless the best intervention beats its score by >0.01. This dimensionless stability margin is not an added broker cost. Existing executed-reduction and geometry/idempotency guards remain mandatory.

## Tasks

### Task 1: Consistent protective benefit and utility

Files: new `seiltanzer/management_benefit.py`, existing `extended_policy_evaluation.py`, `unified_candidate_economics.py`, `unified_edge_ensemble.py`, new `tests/test_unified_decision_utility.py`.

Interfaces: `benefit_assessment(policy, delta_expected_r, delta_cvar_r, band_r, expected_lower_r=None, tail_lower_r=None)` returns an explicit admission basis; `quantitative_utility` returns Expected/tail decomposition without altering economics.

- [x] Write tests for real common paths with small Expected gain and material tail gain, robust bounded loss, excessive loss, EXTEND_TAKE, differing seeds, and preserved price/risk refusal.
- [x] Run new profile; expected failures reproduce Expected-only admission and missing tail utility.
- [x] Implement the shared contract, matched seed tail delta and risk-aware quant score. Re-run new and owning profiles; expected PASS.

### Task 2: Fair relative expert influence, truthful audit and stability

Files: ensemble, audit, management UI, same new regression file, web audit smoke.

Interfaces: candidates carry benefit/quant utility and score decomposition; contributions carry raw score, HOLD reference and normalization divisor. Compact audit and both renderers retain the bounded fields. Family attributions subtract their own contribution to the actual HOLD reference.

- [x] Write regressions for five available full nominal experts, one near-tie flipped by each expert, constant score offsets, missing scores, family contribution conservation, irrelevant candidates and HOLD stability margin.
- [x] Observe RED, implement relative scoring and bounded explanation, then owning profiles and actual Node audit smoke PASS.
- [x] Verify existing actual-ledger repeat-reduction, unchanged target geometry and ACK idempotency tests; extend only a reproduced gap.

### Task 3: One review and accepted release

- [x] Update roadmap with finite scope and factual tests; commit final diff.
- [x] One final independent read-only review. Critical/Important findings get one RED/GREEN fix pass; minors are recorded separately.
- [ ] Publish branch/draft PR; wait mandatory full Python, SQLite, frontend/WebKit and acceptance checks on final tree. Mark ready and merge only after green.
- [ ] Wait actual delivered SHA, readiness, functional smoke, public HTTP and SHA-bound math/source/FOMC publications. Record receipts and stop this package.

## Review Focus

- Opposite Expected/CVaR seed effects: only matched conservative improvement may admit protection.
- Sparse or flat expert vectors: absence is not an opposing vote; common offsets do not choose actions.
- Missing or invalid common banks: fallback cannot manufacture authoritative data or resurrect independently blocked actions.
- Parameterized variants and executed partials: unchanged geometry/repeated same thesis cannot issue a new executable action.
- Compacted/archive audits: scores/utility must retain their semantics, attribution and bounded size, with no execution controls in archives.
