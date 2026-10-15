# AI review latency follow-up — 15 October 2026

PR473 release `2319da5` passed complete CI and production readiness. Cold Q
readiness returned200 in1664ms against the unchanged3000ms gate. The subsequent
real AI smoke returned200 in12940ms, exceeding the unchanged12000ms limit;
canonical source/model handoffs were correctly skipped. This release is not
fully accepted. Original unified-edge plan and handoff remain unchanged.

Read-only request trace `ai-a550dba363184cf6b23e` and saved review390 confirm a
successful structured LLM response, with effective weight15.789%. The actual
costs were active management3727ms, provider5868ms, ensemble2129ms,
publication128ms. The fallback/structured-preference report correction works;
the remaining blocker is total request latency.

A bounded pure replay of review390 identified scalar execution-path iteration
as the main avoidable CPU cost. No provider, journal writer, broker or engine
was passed to this replay. No decision was published and no position changed.

The follow-up skips only segments which cannot cross any execution event level,
then retains the original stateful event ordering and cash flows. Common-bank
and conditional admission replays compute crossing masks in batches of at most
256 paths. Every original path, step coordinate, seed, cost, risk floor and
source gate remains. Fractional time stops use the original scalar interpolation
to retain its exact rounding. This is a calculation optimization, not a new
model, shorter sample, changed policy or larger latency budget.

Within one active-management selection, identical HOLD simulations are reused
by their complete immutable PolicyInputs and seed. Source/risk gates still run
for each action. This caller-local cache is discarded after selection, is never
serialized into a snapshot and is not shared between requests.

Regression evidence: the quiet-path ordering regression failed before the fix;
batch API/validation cases failed before implementation.1474 complete execution
results match a frozen digest from the original simulator, including event
timelines, BE confirmation, coincident spike/rung, fractional time stop and
epsilon-boundary cases. A separate513-path case crosses both batch boundaries;
6000 complete scalar-reference results matched the batch implementation in a
local comparison. Owning simulation/economics/risk/rollover profile157passed
before the additional boundary case; API integration is checked separately.

Exact frozen-review admission rows and the entire ensemble audit are compared
before/after in one isolated diagnostic process. First scalar-only optimization
gave merely1.086x on that host; it was not shipped as a claimed latency solution.
Final batch measurements, focused checks and exact-release acceptance are
recorded in the separate release checkpoint. Actual production acceptance,
normal exact-SHA model/source publication and remaining input unavailability
must be reported separately; dispatch is not delivery.

Final pure-replay proof38769672660 on the actual production host: original
active-management plus ensemble4.6478s, final2.8036s (1.658x). Independent
admission rows and the entire ensemble audit matched exactly. This comparison
ran only in its separate diagnostic process; serving modules were not patched.
Owning profile200passed/2.89s. A narrow independent supplemental review is
limited to this necessary latency diff; it does not reopen PR473 or add research.
