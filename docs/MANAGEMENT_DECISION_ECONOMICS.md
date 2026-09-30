# Management decision explanation and remaining-position economics

The production policies remain HOLD, CLOSE_10/25/50 and EXIT. This change does
not lower qualification thresholds, increase intervention frequency or grant
broker execution authority. A new manually accepted reduction can operate on
an already reduced position; one decision ID can be executed only once.

After an executed partial reduction, another equal or weaker cut must pass a
separate material-change check as well as the current qualification gate. It
requires an adverse 0.15R move from the previous executed review, an increase in
Expected benefit of at least the configured indifference band, a 0.15R increase
in CVaR benefit, a new observed adverse/live family, a newly qualified stronger
policy, or a base optimizer that now selects reduction instead of HOLD. Otherwise
the effective action is HOLD on the updated remainder. Review IDs, timestamps,
shrinking volume and intermediate HOLD reviews do not reset the executed anchor.
An eligible EXIT continues to require its own full gate. These are operational
repeat guards, not historically calibrated claims of statistical superiority.

## Decision causality

The base Expected optimizer uses the economic indifference band and NET CVaR
eligibility. A qualified deterministic risk-overlay can choose another policy.
The arbiter gives a confirmed overlay priority by rule; its displayed scores
and +0.015R diagnostic bonus do not select the winner.

Compact snapshots now retain candidate qualification failures, selected checks
and thresholds, support basis and observed metric identifiers/provenance. Source
winner share is distinct from source feasibility share used by an overlay.
Compaction must not turn omitted evidence into an assertion that no evidence
exists. A base optimizer HOLD boundary does not cancel a pending overlay action.

## Position scale

Expected, median and CVaR in the original policy table are per unit of the current
remaining position and use the original entry-to-stop price risk as R. For
remaining fraction f and realised weighted result y, a policy's total result is
y + f * future_result. The same affine transformation applies to Expected,
median, CVaR and its eligibility floor. Differences versus HOLD scale by f.
This preserves the policy ordering and eligibility; it is not evidence of a
statistically validated advantage. Future-profit probability must not be read
as the probability that the entire partly realised trade will be profitable.

After CLOSE_50 at -0.287R, y=-0.1435R and f=0.5. A second CLOSE_50 closes 25%
of the initial position and leaves 25%. For the reported model, its Expected
benefit versus HOLD is now +0.007 initial R and its CVaR gain is +0.178 initial R,
instead of +0.014 and +0.356 per unit of the remaining position.

The event ledger records fills and relative fractions, not an independently
reconciled broker lot count. Manual broker changes must be recorded in position
state. Actual broker fill price can now be supplied when confirming a standard
reduction. Without it, the result uses the quote at acknowledgement and remains
explicitly estimated. Missing fill R produces an unavailable total result, never
zero realised P&L. Historical fill costs are unavailable; total results combine
gross realised R with modelled net future R and are labelled accordingly.

## Verification and next statistical work

Regression checks cover the reported HOLD-to-CLOSE_50 overlay, EXIT's independent
and live-family failures, source winner share 1/8 with feasible support 8/8,
preserved HOLD when tail-risk benefit is insufficient, byte compaction and two
sequential half reductions with idempotent execution. The iPhone WebKit test
checks 50% remaining → close 25% of initial → 25% remaining and broker fill entry.

`production-management-explanation-audit` reads at most 200 immutable reviews
and 12 indexed position events with SQLite mode=ro/query_only. It can inspect
the exact reported decision without computing a new review or writing to the
production database. Old snapshots may already have lost candidate details;
they must not be recreated as if they had been recorded.

Economic superiority remains a research question: compare sequential policy
outcomes on common observed paths, with costs and actual residual volume, against
the same strategy baseline. Separate unique trades from repeated reviews, use
chronological out-of-sample evaluation and uncertainty intervals for Expected,
drawdown/CVaR and intervention cost. Many reviews of one trade are not independent
statistical confirmations. No new statistical validation or calibration is claimed
by this explanation change.

## Research result availability

The policy-edge observation is excluded when realised pre-review R is unknown.
Its frozen payload keeps this value null. The legacy NOT NULL storage column
retains its compatibility placeholder only on the excluded row; that placeholder
must never enter replay or evidence aggregation.

Execution attribution requires finite R for every closed fraction in the event
ledger. A fully closed position with an unpriced earlier cut is closed but has
no known terminal result, compliance delta or execution-edge eligibility.
Known terminal ledger results are gross initial-position R. Historical fill costs
remain unavailable and net execution-edge eligibility is false. A user-supplied
fill price does not establish independent broker confirmation.
