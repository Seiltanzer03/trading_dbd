# SDD ledger — plan: docs/superpowers/plans/2026-10-27-q-audit-metadata-covering.md

Pre-flight: one task, no shared interfaces. Base 15a61ac/tree551518b8 equals current main ad7158c; existing linked worktree reused.
Ruling: user/AGENTS.md already authorizes inline fixes, PR, merge after full green CI and exact-SHA deployment. No renewed design/release approval; no implementer agents.
Ruling: optimize only the observation metadata lookup. Preserve the attempt timestamp index and tied cohort; do not add a second attempt index or cache freshness. Cost if insufficient: unchanged production latency gate still blocks acceptance.
Ruling: local focused tests may stub the unused optional yfinance module to avoid the previously verified native Python3.12 BusError. Full mandatory Python3.11 CI remains authoritative and unmodified.
Task 1: regression construction in progress. No production implementation yet.

Task 1: real file-backed SQL-plan regression observed RED: observation PK/table lookup rather than covering read; tied-window/current-resolution characterization already passed. Added one four-column observation index and an explicit indexed left join, preserving the attempt index/order and all classification logic. Full+summary paths, missing joined observations, exact tied LIMIT order and current resolution updates are covered. Owning profile:22 passed in1.12s; one existing Starlette/httpx warning; git diff --check clean. No production latency recovery claim yet. Final review/CI/release pending.

Final independent review of 15a61ac..83396f4: zero Critical/Important/Minor findings. Independent focused profile:10 passed in1.03s. Reviewer also checked a synthetic 117,540-observation/45,122-attempt SQLite fixture before and after ANALYZE: exact original5,000-row tied cohort preserved, original attempt index and covering observation lookup used. No re-review.
Final Ruling: actual cold-host SLA, production index-build duration/disk consumption and full Python3.11 CI remain release gates. No local result substitutes for them; cost if the fix is insufficient is another blocked release, not a larger latency allowance.
Final Ruling: retain the original SQL ordering instead of introducing a new tie policy. Supported tests show no new planner/cohort regression; universal future SQLite planner behavior is not asserted. Mandatory Python3.11 CI and actual production query/latency evidence remain required.
