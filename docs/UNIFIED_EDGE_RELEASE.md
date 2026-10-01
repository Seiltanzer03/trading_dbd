# Unified management selection

The runtime freezes all 12 management policies, including parameter-specific
extended actions, before the independent LLM response. A single guarded ranking
combines quantitative, mathematical, Active Edge, historical LLM and current LLM
preferences. The balanced starting budget is 40/15/15/15/15; llm20 and quant100
are frozen counterfactual controls, not extra model requests.

Existing confirmation, execution-price and hard-risk gates remain mandatory.
Shared net scenario economics add a further gate; they never reinstate an action
rejected by its original evaluator. Stop breaches and mandatory strategy events
take precedence. Publication rechecks the active trade and geometry under the
journal/position locks, keeps one pending manual command, and compensates a failed
journal write. No automatic order execution is enabled.

The UI and saved report expose nominal/effective budgets, age/quality and lineage
discounts, excluded actions, contribution scores, common Expected/CVaR estimates,
component ablations and scheme comparisons. Missing scores remain null. LLM
self-confidence does not set its weight; unavailable budgets return to quant.
Macro/session/flow derivatives sharing a primary source do not create extra
independent votes. Family forecasts share an existing component budget.

## Evidence and limits

* [Finite mathematical search](mathematical_edge_unified_search_2026-10-01.md)
  enumerates all 13 configured instruments and uses actual saved bars for ten.
  Three crypto instruments lack source bars; JPY100 has no supported model.
  Generic 2bp path heads apply only when actual action geometry/horizon matches.
* [Eight family adapters](EDGE_FAMILY_ADAPTERS.md) extract actual point-in-time
  facts and admit only validated immutable out-of-sample net-action artifacts.
  Unresolved feeds and calibration remain explicit. An adapter alone is not a
  proven economic edge. Current local official macro inputs are included without
  a provider call in the review request.
* [Actual-review comparison](unified_edge_actual_review_comparison.md) exports
  bounded frozen reviews read-only and performs all scenario/path calculations on
  a GitHub runner. It separates scenario scores, observed path counterfactuals and
  unavailable settled ledger net profit. Missing continuations stay unavailable.
* [The working regime layer](EDGE_REGIME.md) uses completed direct price observations and verified
  published events. It controls applicability and adds no independent vote.
  Regime-specific artifacts must use the same regime contract; uncalibrated
  thresholds do not authorize dynamic percentages. Missing source authority is
  explicit UNKNOWN. All byte-budget tiers preserve bounded operational clocks,
  quality, lineage, roles and path-heads; oversized inputs are explicitly excluded.

The comparison bank is shared across candidates, but the old production
evaluator does not retain a complete replayable bridge bank. Its exact simulated
execution events cannot be reconstructed from an old snapshot. This additional
bank uses disclosed piecewise-linear execution; original gates remain intact.
Weighted preference scores and probability proper scores do not establish net
profit or causally justify the starting percentages.

Release acceptance requires full Python/frontend/WebKit CI on the exact PR SHA,
then the existing exact-SHA automatic delivery/readiness/functional-smoke
transaction. This document does not claim those checks passed before they run.

## Local pre-release verification

The final full local Python run completed with 1,789 passes and one failure in
the unchanged sparse-backup WAL quiescence test. The identical failure reproduces
on base main `c435974dc034d9faa7e0eaf3deace509648ae13a`; that storage code is not
modified here. All frontend module checks and Node smoke commands passed.
Operational compaction, mathematical-report compatibility and regime changes
also passed their targeted regressions. GitHub release acceptance runs separately:
full Python/frontend/WebKit CI and runner comparisons must pass on the final PR
revision before merge, followed by automatic deployment and production acceptance.
