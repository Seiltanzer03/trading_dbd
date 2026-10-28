# AI review integrity and provider deadline — 2026-10-28

User explicitly resumed development after the quota cutoff and requested quick,
complete correction of the supplied XAU report. Baseline main/installed SHA:
255813b72bcaa9d8835da7ffe27e8237668d14f8. Existing linked worktree is clean;
branch fix/ai-report-reliability-20261028. No crypto-specific changes or orders.

## Verified cause and finite scope

Read-only production receipt 40645397922/job122672921863 confirms latest review
436 has no metric_coverage, but retains trade_geometry, net floor -1.01,
selection floor -1.01 and 21 derivative rows. The report normalizer requires
coverage and therefore returns legacy text unchanged: wrong gross/net comparison,
fake 0/0, missing metric audit and old AI arbiter description. The emergency
snapshot compactor explicitly drops coverage. Fix that owning layer and allow
structured report normalization without presentation coverage; unknown stays
UNAVAILABLE. Restore the entire existing metric audit including tail_log_ratio.
No arithmetic/risk/source thresholds change, and lower Expected is not described
as a proven profit advantage. Existing authoritative decision and continuity stay.

Same receipt correlates the supplied ai-925000aec9194b429b4b with an actual complete
provider response: HTTP 6211.713ms, 9304 prompt/500 completion tokens, finish=stop.
The outer default deadline discards it at six seconds (elapsed6610.753ms), although
the route has an unchanged12s ceiling and4s post-provider reserve. Use the already
allowed8s provider maximum by default, still capped at remaining route time minus
4s. Keep explicit operator timeouts, model, one-call policy and transport cap.
Do not infer that every future provider response will fit this budget.

## Execution and acceptance

1. Reproduce compacted normalization, coverage loss, omitted tail metric and
   provider budget failures in focused tests before implementation.
2. Fix only the owning contracts; test the installed HTTP fallback path with a
   compact snapshot as well as the structured formatter. Verify no repeated close.
3. Run relevant owning tests once, one independent final read-only review, then
   mandatory final-tree CI and authorized expected-head merge.
4. Automatic exact-SHA deployment/readiness/smoke/public checks; inspect saved
   canonical review for actual LLM status/weight/latency and correct audit/CVaR.
   No completion claim from CI alone. If live LLM still fails, use that trace to
   decide next work; never silently label deterministic fallback as LLM success.

Current limitations remain explicit: calibrated management advantage is not
proved by four available market forecasts, and source-history deficiencies cannot
be filled by fabricated observations. Model-risk, delayed/proxy roles, missing
broker costs, fills and pre-release consensus retain existing honest labels.

## Verification ledger

Eight regressions observed failing before the production correction; focused
29PASS, owning API/report/guard87PASS52.89s, real6.1s reply included in guard11PASS.
One independent final review: no Critical/Important/Minor; independent30PASS6.89s.
First CI40646071100 had5076PASS and three failures: two old tests still required
the former6s default; update those test assertions only, including preservation
of an explicit6s operator override. The unrelated restored-mtime/source-mutation
test also failed in that run; it passed unchanged in the12-test focused follow-up.
Do not suppress it or change source-integrity gates. Final full CI is required
on this updated test-only revision. Native transport, WebKit and production
acceptance remain mandatory; no additional independent review of unchanged code.
