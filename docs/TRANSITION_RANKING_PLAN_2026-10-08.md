# Finite transition comparison checkpoint and ranking correction

Baseline: accepted PR422 remote77407e8/treee0a39202; local equivalent68cfff4.
Spec: TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md and locked global P1–P6 plan.

1. Record actual automatic37753286968 export/discovery/transition publication,
   preserving its SHA and absence of all-gate trading authority. Do not rerun it.
2. Reproduce valid zero score components being ranked as missing in transition
   horizon and combined top lists. Preserve numeric zero; retain missing-score
   penalty, deterministic ID tie break, templates, maturity and authority.
3. Check scoped transition/loading/discovery/report consumers; one whole-package
   review, one mandatory final CI, then authorized exact-SHA release acceptance.

Scope: existing comparison correctness; no new models/sources/search budget.
Real published snapshot verifies storage compatibility and origin exit; one
successful export does not establish permanent headroom or profitable edge.
