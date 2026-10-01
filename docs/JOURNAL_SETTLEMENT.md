# Confirmed position executions and journal settlement

The journal closes automatically when a confirmed fill exhausts the current
remainder. The fill and journal update share the PositionLedger SQLite transaction;
an interrupted write cannot leave a new fill with an open journal row. Rejected
actions, stop/take changes and armed conditions do not close the trade. A TIME_STOP
closes it only when the broker fill is confirmed.

`result_r = sum(fraction_closed_of_original_position * execution_r)`.
R uses the initial entry/stop distance. Two successive CLOSE_50 executions close
50%, then 25% of the initial quantity. Only the final 25% remains for the next
exit. The journal is the result of the entire trade, not the final quote alone.

Broker prices yield AVAILABLE gross results. Quote estimates remain ESTIMATED;
missing prices or incomplete historical quantities yield UNAVAILABLE, not zero.
Actual commission/swap data is not available in this contract. The UI distinguishes
price-based gross accounting from a user-entered whole-trade result.

`POST /api/trade/close` accepts an optional residual execution_price. Old clients
may explicitly supply result_r as a whole-trade override instead; it is never
treated as the R of the remaining quantity. A closed trade needs no further form.
Retries of the same AI/conditional/manual fill remain idempotent after closure,
including after the next trade has opened.

`POST /api/trade/fill` records actual manual or ladder reductions. It requires a
broker execution price, fraction of the current remainder and stable request_id;
the UI also sends the reviewed state_version to reject stale volume. It records
no broker order and never infers a fill merely because price touched a rung.

`GET /api/trade/management?trade_id=...` returns fills and management events for
open or closed journal trades. The journal contains an execution-history button,
weighted total, quantity, price provenance and each fill's contribution.

Startup reconciliation repairs legacy ACK-only exits and legacy residual-only
journal entries when a complete confirmed ledger exists. Deleted trades and
explicit whole-trade overrides remain untouched. Old independent MANUAL_EXIT
totals are ambiguous and are retained, because their form asked for a weighted
whole-trade result. Original totals overwritten by reconciliation are preserved
in journal_result_reconciliations. History that was never recorded is not invented.

Acceptance covers long/short accounting, ladder/manual/AI/time exits, repeated
half reductions, filled-versus-armed conditions, price estimates, unknown prices,
invalid/stale quantities, atomic rollback, historical repair, manual overrides,
CSV and actual iPhone WebKit history/confirmation rendering. Production smoke
exercises accounting on a temporary DB without changing a user's real position.
