# Четыре пакета: рабочие оценки восьми семейств сейчас

User authority: 2026-10-10, 13:09 Moscow — implement the original four packages immediately, record progress/boundaries, exclude new broker-cost work and waiting for future data. Native execution in the existing isolated worktree; one final reviewer, one mandatory final-tree CI, authorized merge and actual server acceptance. This instruction supersedes the previous position/cost follow-up and repeated skill approval handoffs.

Spec: `TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, §§3–5,7,9–12. Historical mathematical working heads, all12 actions, common economics and mandatory risk remain. No new sources, orders, calibrated-probability claims or manufactured historical profit.

## Fixed design and interfaces

One existing provider response adds optional `shadow_decision.family_assessments`, indexed by the existing eight family IDs. Each opinion cites1–4 exact observed `feature_names`, gives1–3 bounded `policy_scores` and a short `reason_ru`. The server admits only actual available frozen features and their causal provenance. These are labelled current LLM interpretations, not historical family forecasts. Old provider payloads remain compatible; empty/missing input stays unavailable.

`seiltanzer/edge_family_working.py` owns parsing, admission, shared-lineage grouping and blending. `working_family_preferences(families, llm, excluded_family=None)` returns combined scores, eight bounded audit rows, per-policy attribution. For each scored action, a nonflat global view and family views each use half the existing current-LLM pool; absent family scores leave that global opinion intact. Flat global vectors carry no relative opinion and are discarded when admitted family views are present, leaving unscored actions absent. Families sharing any cited dependency form one group. Actual contributions are decomposed using the applied LLM weight. No extra expert percentages are added.

## Task 1 — working source-bound opinions

- RED `tests/test_edge_family_working.py`: all8 actual adapter fixtures admitted without trained net models/broker files; absent/stale/future/mismatched/cited-missing facts cannot gain a vote; malformed/bool/nonfinite scores rejected; shared lineage cannot multiply influence; frozen inputs unchanged.
- Implement parser/admission/blending in the new module. Exact limits above, scores[-1,1], no runtime/network/model fitting.
- GREEN focused new tests + existing adapters.

## Task 2 — one provider call and one decision

- Extend existing payload parser and single-call combined prompt/transport; preserve accepted opinions in the frozen shadow. Family response is optional for legacy compatibility; prompt asks all8, empty rows for unavailable facts. Output max2400 tokens, existing8s HTTP timeout unchanged.
- RED integration tests in `tests/test_runtime_memory_metric_report.py` and new family tests: provider opinions survive one call; family preference changes eligible ranking, current-LLM pool remains within100%, all12 actions retained and hard-risk refusal preserved.
- Wire `collect_components` to admitted working opinions. Historical model admission remains independent.
- GREEN affected runtime/ensemble profiles.

## Task 3 — attributable UI and ablations

- RED tests: selected-action family contributions sum to the family portion of the LLM contribution; removing an accepted family reranks cached candidates without provider calls or scenario resimulation; actual historical forecast flag remains false for an interpretation.
- Expose eight working-status rows, scores, applied contribution and `family_counterfactuals` (scope explicitly working interpretation only). Preserve through compact audit and show in real report/UI alongside historical model availability.
- Run Python audit tests and the actual renderer smoke, including escaped source text and all12 candidates.

## Task 4 — common release and stop

- Record RED/GREEN counts, one final read-only reviewer and one Critical/Important regression fix pass; no second reviewer.
- Mandatory full CI on final tree once. Authorized branch/PR/merge; actual deploy/readiness/smoke/public and necessary exact-SHA publications.
- Finish with accepted SHA and concrete missing-source rows. Do not wait for future observations, fit unchanged bad histories, optimize weights or pursue storage/scheduler/broker work inside this release.

## Review Focus

No unobserved feature or self-declared family identity can vote. Multi-source shared lineage is grouped by intersection, not only exact list equality. Partial sparse opinions do not turn missing actions into zero votes. Attributions use actual post-freshness/dependence weights and remain dimensionless. Working-family ablation removes only the interpretation, preserving history/risk/economics and matched parameter variants. Existing independent-provider masking and one-call budget remain intact.

Final independent review: two Important findings accepted and fixed in one pass. Flat global levels no longer suppress source-bound sparse family views; family ablations preserve every finalized non-LLM component, including common-bank identity and dependence weights. Four new RED regressions reproduced both failures before the fixes; affected ranking/audit profile120passed. No Critical or Minor findings. Actual provider latency/completeness, predictive profitability and live availability of all eight inputs are not established by fixtures; release CI and live readiness are separate required receipts.

Final changed profile:355passed32.84s (one existing Starlette warning); real interface renderer PASS; no actual orders or paid provider calls. Mandatory remote full exact-tree CI and server acceptance remain required before claiming release.
