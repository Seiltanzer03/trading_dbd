# Runtime headroom and conditional current-source Yandex repack

User: no disk expansion budget; use existing Yandex storage and continue quickly.
Base26adc8c contains the independently reviewed off-host rebuild, PR545 CI5172PASS.
PR544 bounded-macro transport remains reviewed/green separately; preserve it.

## Task1 — bound optional raw research growth

Read-only current DB19.59GB/free954MB and daily Yandex receipts establish roughly
0.2GB/day growth. A one-time physical copy cannot by itself make this sustainable.
Admission for large optional passive captures and new G1S feature materialization
must preserve the existing1GiB+32MiB reserve plus128MiB batch margin. Once paused,
resume only above an additional128MiB hysteresis. In-memory tests are unaffected;
capacity errors pause optional captures explicitly. No source/watermark changes
on refusal. Existing outcome resolution, exact price/bar capture, current models,
trade/decision audit and user actions remain available. Expose pause and measured
capacity in collector/materializer status; no new observations or confidence.

Implement a shared stdlib admission helper, use the two owning capture methods,
test real SQLite refusal/unchanged watermarks, resumed collection and preserved
resolution/price paths. One final review for this distinct runtime package.

## Task2 — conditional physical choice

Await existing run40856228869 (one OFF-HOST measurement on pinned28Oct seed).
Only actual useful savings justify a new separately journaled current-source
page-repack owner. An old seed must never replace live data. Do not change page
sizes repeatedly to obtain a desired answer if the savings are inadequate.

## Task3 — native operation, only after measured savings

Keep old committed cutover metadata intact. Separate durable state and the
existing canonical service guard/host maintenance EX lock prevent every start
and writer during a bounded current frozen capture. Actual current source hash,
all original rows/typed roots/v2 pins and both cloud images/full GETs bind the
new transfer; unchanged physical reserve/precommit/runtime checks and recovery
on disposable files are mandatory. No new cloud resource, DB deletion, weakened
freshness/risk reserve, provider/model call or trade/ACK.

## Task4 — one final release

After the new scoped review/correction and final integrated CI, restore actual
headroom through the validated operation, then normal authorized main release
and exact-SHA canonical acceptance. Combine already reviewed macro fix where
possible to avoid repeated cold restarts. None of these future steps is complete
merely because PR545 or its old-seed measurement is green.

Ruling: implement Task1 during off-host I/O; it is required regardless of which
physical layout proves useful and does not modify PR545's frozen source/tree.

Progress: Task1 implemented. Actual passive and G1S low-disk captures reproduced
RED (both still wrote data with zero simulated headroom), then GREEN with exact
cursor preservation, automatic hysteresis/resume, actual price/bar writes and
old insufficient-outcome resolution continuing. Canonical reserve preserved.
Found both production status wrappers bypass the raw class status; added
explicit bounded/cache-safe telemetry rather than reporting an intentional
pause as stalled or silently omitting it. Closed-connection reader regressions
reproduced both omissions RED, then GREEN. Symlink measurement now follows the
actual database filesystem after a RED physical-path regression.
Owning28PASS1.38s, one upstream Starlette warning. Python3.12 test process only
stubs its known crashing yfinance import; product has no stub, real3.11 final
CI remains mandatory. One final scoped review:0Critical/0Important/1Minor.
Fixed Minor by supplying explicit high capacity before seeding the integration
fixture; this avoids a test depending on its execution host's free bytes.
Ruling: this is admission/backpressure, not an absolute physical allocation
quota. Other writers and the pre-existing oversized-first-source-row exception
remain; normal capture budgets and physical/source limits are unchanged.
Off-host run40856228869 restored the real19.50GB v2 seed and is now measuring;
no live DB mutation or service stop has occurred.
