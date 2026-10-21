# Post-cutover deployment admission

Native epoch `39582128571` completed with `COMMITTED/service_started=true`.
Worker `39584052093` independently restored the complete hot/cold union and
finished successfully. Public hosted-runner GETs for `/` and `/api/state`
returned 200; no new cutover or original restore is needed.

Deployment `39584729937` then refused before repository delivery. There was
no `cutover.active`; the existing startup validator exhausted its five-second
SQLite budget and classified the interrupted check as `HOT_HISTORY_STATE_INVALID`.
The same read-only validator with a diagnostic-only 20-second budget validated
the unchanged live database in 10.499 seconds. Production limits were unchanged.

## Owning read cost

Retained identity queries selected a rowid table walk over wide source rows.
Production measurements: 86,100 G.1S identities took 1.496 seconds; 108,171
passive identities took 4.519 seconds. Reading the same typed IDs and rowids
through their existing complete single-column observation-ID indexes produced
identical proofs in 0.625 and 0.725 seconds. Partial/expression/wide indexes do
not qualify; no new index, database mutation or schema change is introduced.
Rowid ordering, maximum rowid, storage classes, size checks and root encoding
remain unchanged. A missing suitable index retains the previous complete scan.

The final projection admission join could scan every unrelated live dataset
membership. Keeping the archived set as the outer join loop uses existing
indexed identity lookups and still rejects eligibility under every dataset
version. Actual query measurement was 0.223 versus 0.713 seconds.

An identity-only candidate still exceeded the cold check once. The complete
candidate was therefore tested with both changes using a temporary read-only
application package, the real live database and the unchanged installed guard:
all three five-second checks passed in 4.849, 3.027 and 2.728 seconds
(`39601812257`). Installed production modules were not changed by that probe.

## Validation and delivery boundary

Two covering-read regressions failed before the fix; a real SQLite VM-work
budget regression with 20,000 unrelated memberships also failed before the join
fix. The final owning profile passed 119 tests in 5.45 seconds, including typed
roots, partial-index fallback, tampering, cross-version rejection, native startup,
runtime readers and current hot/cold union. Independent read-only review found
no Critical/Important findings. Exact-tree mandatory CI and production delivery
remain required before calling this release complete.

The covering read scans later index entries above the retained watermark before
filtering. Review measured that tradeoff with 20,000 later rows. Future index
growth can increase validation cost; the unchanged deadline continues to fail
closed. This is a follow-up capacity concern, not permission to weaken checks.

The old installed validator runs before an ordinary deploy can fetch new code.
Delivery must therefore bootstrap only the two reviewed, green-CI stdlib reader
modules under the existing host EX lock, exact file/epoch/SHA checks and terminal
COMMITTED evidence. Preserve the guard, journal, database and running service;
then require the actual unchanged startup guard to pass and use ordinary
exact-SHA deployment. Never restart or repeat the completed cutover for this fix.
