# Stop continuity and common-bank economic dominance

Scope: supplied two XAU reports (4055.41/4054.135): a lower unconfirmed
stop replaced a stronger recommendation, and saturated quant scores allowed a
Pareto-inferior stop to beat EXIT. Baseline main8518d125; no source/model scope
expansion, training, risk-gate weakening or automatic execution.

1. Reproduce both behaviors (4 failing tests observed).
2. Ratchet deterministic stop proposals against strongest unresolved published
stop (including superseded without an explicit not-executed receipt). Preserve
actual position geometry separately. Recheck at durable registration; crossed
reference requires reconciliation, no inferred fill. Long and short supported.
3. Exclude same-bank Expected/CVaR-dominated candidates from ranking after
risk/benefit checks, before expert preferences. Original eligibility/economics
retained; expose dominator identities and economic reason. Different banks,
ineligible alternatives and genuine tradeoffs are not filtered.
4. Owning tests, one final independent review, required full CI, authorized PR
merge/deploy, exact production acceptance. No paid diagnostic LLM calls.

Main8518 explicit categorical contract produced live439 with nonzero weight;
contradictory later family synthesis can still be rejected correctly. Its audit
standalone status must also be NOT_ACTIONABLE, not READY.
Review found superseded stop refusal had no reconciliation path: explicit
not-executed receipt now permitted for superseded stops only; stale execution
is still refused. Regression observed RED then GREEN. Missing history, delayed chain
and low confidence are source/evidence limitations; never fabricate weights,
confidence or historical action profit to obtain a desired review rating.

Final owning216PASS6.64s; integration20PASS2.85s (host test-only yfinance
import shim for known native curl issue; production unchanged). One final
independent reviewer:0Critical/1Important reconciliation dead-end, fixed with
real SQLite RED-to-GREEN;0Minor. No repeat review/full local suite.
Mandatory real Python3.11/full WebKit CI and exact live release pending.
