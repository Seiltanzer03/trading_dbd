# G.1-M.1 extended-action pending window repair — 2026-10-14

Production continuation38573621412 verified installed main93c97d5 and its
green CI, then correctly failed before readiness on the worker core error
`KeyError: 'TIGHTEN_STOP'` at `g1m_local:resolve`. The new cooperative gate
was acquired, observed by the worker and released on failure. No skipped
acceptance step was represented as successful. No reinstall or restart occurred.

Root cause: management capture already retains all seven extended actions
with `EXTENDED_POLICY_NOT_IN_FIXED_ACTION_SET`, excluding them from fixed
partial-close attribution. Local windows also retain these descriptive records,
but their resolver indexed a five-policy outcome dictionary with the extended
production action. The terminal resolver already excludes these observations.

Repair: retain the immutable source and windows, advance the existing bounded
scan cursor, and leave an unsupported local outcome unavailable. Never alias an
extended stop/take/conditional action to HOLD or invent a realized result. The
next supported window resolves normally. Existing upstream exclusion diagnostics
remain the explanation; all source, time, risk and eligibility rules are unchanged.
No live order, ACK, position edit, database cleanup or new model is introduced.

Seven parametrized regressions reproduced the exact KeyError before the fix.
They exercise actual capture, frozen windows, observed horizon paths and the
bounded resolver for every extended action, followed by a supported CLOSE_25
window. After the fix:36 owning local-feedback/catchup/eligibility/edge/management
tests passed in1.01s, with one existing Starlette deprecation warning. Regressions
also verify source/window immutability, no false extended-policy outcome,
unchanged exclusion reason and the supported result0.65R versus HOLD0.8R.

The former real AI HTTP200/8367ms result remains installed-version evidence,
not formal acceptance of this new repair. One independent final review is clean
(no Critical/Important findings; seven regressions independently passed in0.54s).
Mandatory CI for the changed final tree, authorized merge/deploy and actual
exact-SHA acceptance remain required. Family models without causal inputs stay unavailable; research
profit and extended-action local attribution are not claimed.
