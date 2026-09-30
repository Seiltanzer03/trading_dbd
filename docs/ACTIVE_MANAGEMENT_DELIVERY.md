# Active management and faster acceptance

The independent deterministic selector evaluates MOVE_TO_BE, TIGHTEN_STOP,
TRAIL_GAMMA_FLIP, EXTEND_TAKE, REDUCE_TAKE, SCALE_OUT_ON_SPIKE and TIME_STOP
when the effective base command is HOLD. Qualified CLOSE/EXIT retain their
existing priority. One registered manual command is published, not parallel
orders. Absence of qualified extended candidates leaves HOLD unchanged.

Stop/take candidates require paired counterfactual Expected improvement whose
95% Monte Carlo lower bound exceeds the economic band on both seeds, and CVaR
above the strategy floor. Conditional candidates use shared option-driver paths
and the authoritative piecewise-linear execution replay, including ladder and
absorbing BE/stop/take. This discretisation is explicit; Monte Carlo intervals
measure sampling uncertainty, not historical profitability or model correctness.
Historical broker costs are not measured. Gross and net floors, assumed future costs and cost availability remain
explicit; no real-market statistical edge is claimed.

Gamma trailing proposes a frozen stop at the current mapped gamma flip only
with a live option snapshot no older than 120 seconds and a valid tighter level.
It is reassessed on subsequent reviews, not continuously executed by the app.
TIGHTEN_STOP uses a defined 0.5R buffer. Spike reduction uses a distinct trigger
before take, avoiding existing ladder levels, and 25% of current remainder when
the condition is armed. Its recorded broker quantity remains fixed if position
size subsequently changes. TIME_STOP uses half the current model horizon.
These parameter rules are candidates tested by the gate, not calibrated edges.

The existing durable action ledger handles stop/take changes, conditional arming,
manual fill acknowledgement, cancellation, remaining volume and idempotency.
Nothing submits broker orders automatically. The 0.65 legacy registration field
for deterministic candidates is explicitly a compatibility threshold, not an
inferred probability or LLM confidence. Reports label the deterministic source.

CI now runs Python and browser checks in parallel and publishes ci/full-webkit
only after both pass. The lattice workflow runs its specific Python regression
instead of repeating the full suite; full Python coverage remains in CI.
The existing exact deployment lease, acquired after HTTP-ready, wakes the
research core during startup grace. The core still runs and must finish before
readiness/smoke. Ordinary startup without that lease retains its five-minute
grace. No research acceptance, readiness or smoke gate is skipped.

## Completion acceptance (30 September 2026)

The cached production path bank and fresh stress runs use the same net-cost
pricing function. EXIT equals current model R minus immediate full-close cost;
HOLD pays deferred cost; partials pay the volume-weighted mix. Legacy final-R
fields contain net results, with explicit gross/net aliases and cost flags.
The 0.01R fallback remains an assumption, never measured broker execution.

A small frozen reliability contract survives normal, strict and emergency
compaction. The report and extended gate read the same most restrictive
published level. Missing reliability or cost inputs blocks an extended override.
Low quality requires the existing independent/live observed-evidence gate.
Gamma and option-wall anchors require live/ok option data no older than 120s.

Every extended candidate publishes its exact parameters, paired model Expected
before/after, delta and lower MC bound, CVaR before/after, floor, materiality,
path count and cost source. Rejection before simulation is UNAVAILABLE, not zero.
Spike triggers start above both current R and the recorded maximum, avoid
strategy rungs and must precede take. Position-level delta scales with the actual
remaining original volume. The registered manual action is the final report
conclusion; until broker confirmation the clearly labelled strategy continues.

Only explicit TIME_STOP order deadlines in the known instruction contracts are
exempt from the no-future-source-timestamp check. Future market observations
remain rejected. LLM claims contradicting missing stress checks or the degraded
manual rule are withheld from human rationale but retained in the raw audit.

The completion regression exercises all seven parameter/replay contracts,
compaction tiers, cached-vs-fresh net pricing, drawdown spike generation, journal
causality, actual fallback API registration and manual acknowledgement. The
existing production smoke now validates the net-cost audit and candidate gates
on its usual verdict request; it submits no broker orders or acknowledgements.
This establishes software readiness, not out-of-sample trading profitability.
