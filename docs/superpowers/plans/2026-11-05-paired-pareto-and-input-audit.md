# Paired Pareto and current-input audit implementation plan

> Execute inline, using the existing TDD/review/release workflow; one final whole-diff review.

**Goal:** fix the NAS100 exclusion of a less intrusive candidate on an uncertain point estimate, and verify the two supplied NAS100/GER40 reports against actual current inputs.

**Architecture:** calculate pairwise evidence once from the same already priced net path vectors and their original weights/seed groups. Ranking may remove a point-dominated candidate only when paired expected-return and matched-seed tail bounds support that comparison. Reuse this evidence for ablations and audit; no extra scenarios, provider calls, training, thresholds or orders.

**Spec:** user request2026-11-05, both attached reports; existing unified edge specification and locked plan. Baseline local06e3c7f/tree73e9 matches installedmain7f0ccd. User explicitly requests immediate implementation; no additional design choice or permission is needed.

## Constraints and review focus

- Preserve source identity/original receipt clocks, net risk floors, paired admission, costs, existing0.03R sacrifice/materiality and0.01score margin. MC bounds are simulation uncertainty, never validated market confidence.
- Never ACK/replace unknown broker outcomes. Current actual trade143 GER40 has pendingCLOSE_10; prior142state is historical.
- No dominance across different banks, missing evidence, ineligible candidates, or equal economics. Constant non-worse outcomes with strict tail benefit remain detectable.
- Seed disagreement and concentrated/zero weights must not become false evidence. Small stable gains may be real within the declared model; do not invent a new tolerance merely to force HOLD.
- The latest saved GER40 report has0/8; NAS100 was earlier. A later background capture has5/8 and44available/92metrics, but is not a replacement completed report. The actual saved LLM request contains92readable fields. Existing event/order-flow missing data cannot become fabricated models.

## Task1 — actual paired dominance

Files: `seiltanzer/unified_candidate_economics.py`, `seiltanzer/unified_edge_ensemble.py`, `seiltanzer/unified_edge_audit.py`; tests `tests/test_paired_pareto_uncertainty.py`, existing owning suites.

- [x] RED: real common-bank noisy example with net HOLD0.184R/current exit0.195R: CLOSE_25 point-dominates CLOSE_10 but its paired lower gain is negative, so CLOSE_10 remains eligible. Independent literal paths/weights; no mocked pricing.
- [x] RED controls: deterministic/low-noise dominance, disagreeing seed groups, mismatched banks, equal outcomes, missing pair evidence and no extra replay/draw during ablations.
- [x] Implement bounded pair evidence from retained net outcomes/group CVaRs, then robust Pareto admission; publish the exact reason/bounds in the existing audit. All existing hard-risk/benefit gates run first.
- [x] GREEN owning economics/ensemble/audit/runtime/benefit checks; commit.

## Task2 — diagnose reported source/interpretation problems

- [x] Complete the already running eight-sample current-input probe across a5m boundary. Inspect actual publication SHA, input age, model reasons and quote latency. Reproduce and fix only an observed infrastructure defect; no freshness relaxation.
- [x] Trace EWG relative-return-to-R scaling and LLM crypto evidence/lineage using the actual saved92field provider request. Keep a structured preference distinct from standalone actionability and from calibrated confidence; do not change user-approved expert weights based on an uncalibrated self-rating.
- [x] Compare the mathematical preliminary rules vs trained model statuses in the two reports; correct contradictory attribution if reproduced. Existing finite historical management reports provide measured evidence; no duplicate large DB migration or invented return history.

## Reproduced current-input failures

- Eight45sec production observations on installed7f0:44/92 available throughout, but actual capture ages48.69/93.77/138.91/184.16/17.45/62.50/16.87/1.16sec. HTTP22.5–214.1ms, quotes fresh, no memory pause; strict≤55sec criterion failed.
- A nonblocking frozen-input lock collision silently left the runtime READY and deferred another attempt30sec. Record DEFERRED/reason/last attempt/duration; retry only this local capture at5sec under the existing singleflight scheduler. Preserve the original last-good capture and source clocks.
- A trained reference family awaiting the current completed5m candle produced an apparently READY0/8 snapshot. Mark it CURRENT_REFERENCE_MODEL_INPUTS_PENDING and retain the existing20sec failed-build backoff; no provider call from the request path, no synthetic candle. Cached older success must remain unavailable until recovery.
- RED: two busy/invalid quote tests failed on old READY state; readiness and attribution tests failed on ready0/8 and missing render contract. GREEN:425owning tests, including prior READY→pending→recovery, paired economics, compaction, real local capture contention and unchanged source authority.

## Final review correction — 2026-11-06

- Read-only review of06e3c7f..3ba38ab found one Important issue, no Critical/Minor: flooring n_eff−1 at one understated uncertainty when weights concentrated.
- Actual priced/ranked regression weights[.99,.01] changed the lower EXIT−TIGHTEN_STOP bound from+0.001799R to−0.045286R; the tightened stop must remain eligible. A second[1,1e-20] case explicitly leaves a nonconstant bound unavailable when n_eff rounds to1.
- Both regressions observed RED before correction. The corrected pairwise estimator uses actual positive n_eff−1, preserves exact constant differences and zero-mass semantics, and propagates unavailable bounds without Pareto exclusion.138owning tests pass. Existing HOLD-admission formulas and thresholds are outside this correction and remain unchanged.

## Release

- [ ] One final independent review/correction pass, mandatory final-tree full CI, authorized merge and normal exact-SHA delivery/readiness/smoke/publication acceptance.
- [ ] Repeated actual metric/forecast/source receipt/latency verification, concrete explanation of remaining unavailable inputs, and assessment of both reports. No8/10 claim from test counts alone.
