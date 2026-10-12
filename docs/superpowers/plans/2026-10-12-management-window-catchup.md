# Local management history catch-up

Goal: ensure bounded processing reaches every existing local management observation and later resolvable paths. Separate next package, not an addition to finalPR453.
Baseff82fa8 (PR453 equivalenttree8661ebb8); user asks to continue substantive global work during release waits. Spec: existing13instruments/12action management and lockedglobalplan; no new evidence, changed frozenhorizon, relaxed admission, terminalprofit claims, orders or broker-cost work.
Task1: reproduce materialize_windows(limit=1) processing the same complete first observation repeatedly. Exclude only observations with all4configured horizon windows; incomplete prior windows must be repaired. Preserve immutable bytes, target clocks and original eligibility.
Task2: reproduce pending first window without a valid observed path starving a later resolvable window under resolve_due(limit=1). Use deterministic process-local round-robin keyset cursor(target_ts,window_id), wrap once on reaching end. Advance only the scan cursor, not resolution state; missing/invalid paths stay pending and are revisited. Preserve exact pre-target path and SHA checks.
Task3: scoped verification, one independent final read-only review and onefixpass; publish separately only after finalPR453 boundary and mandatoryCI. Do not merge or claim deployment early.
Review focus: partially materialized observation after an interruption; equal targettimestamps; new earlier-target windows behind scan cursor; missing/hash-invalid path must stay pending; a restart resets scan but never fabricates outcomes.
