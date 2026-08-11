# G.1B.1 Q Evidence Bring-Up

G.1B.1 makes prospective terminal risk-neutral Q capture observable. It does not
fit Q->P calibrators and does not change production decision authority.

## Contracts

- `g1-q-evidence-v1` — stage/report contract;
- `g1-q-evidence-integrity-v1` — fail-closed freshness/provenance/mapping admission;
- `q-source-capability-v1` — static target/source/relation/transform capability;
- `q-capture-attempt-v1` — append-only capture-attempt ledger;
- `q-native-expiry-cadence-v1` — evidence capture policy;
- `q-independent-collector-v1` — persisted 15-minute Q collector independent of fixed-horizon triggers;
- `q-independent-collector-calendar-v1` — exact expiry-minus-T0 ACT/365 freeze;
- existing `option-q-contract-f32-v1` and `act365-calendar-f32-v1` remain authoritative.

## Evidence flow

`independent background Q collector -> option source -> frozen T0 terminal Q -> expiry -> F.3.2a truth -> G.1A admission -> G.1B metrics`

Q acquisition is intentionally separate from the ordinary 15m/event fixed-horizon
trigger. A service restart therefore cannot hide Q-source availability merely
because a recent 15m observation already exists. The Q cadence is persisted in
the append-only attempt ledger, so restarts also cannot spam duplicate captures.

Failed Q attempts are never converted into synthetic observations. The ledger
records deterministic blockers such as missing provider/chain, market closed,
stale source, invalid expiry, insufficient density support, invalid CDF, proxy
mapping errors, non-direct/stale target prices, and persistence/budget failures.

A successful independent Q run writes exactly one `option_native_expiry` row.
It never creates or duplicates the seven fixed 15m/30m/60m/2h/4h/8h/24h rows.

## Counts are intentionally separate

- capture attempts: real background Q attempts;
- successful Q captures: immutable option-native T0 forecasts with a valid frozen CDF and clean capture-time provenance;
- resolved Q observations: successful captures whose expiry outcome has resolved;
- Q->P eligible: resolved captures that also pass unchanged G.1A admission;
- G.1B metrics eligible: observations accepted by the baseline measurement layer.

A high capture count does not imply independent evidence. G.1A dependency and
effective-N rules remain authoritative.

## Source semantics

Each configured instrument is classified as `NATIVE`, `DIRECT_PROXY`,
`INVERSE_PROXY`, or `NONE`. The configured `proxy_transform` is frozen into the
Q forecast. Inverse proxy mappings cannot silently fall back to direct.

A successful pristine Q capture additionally requires a fresh option-source
snapshot and a fresh, direct, high-quality target-market price at T0. Cached or
stale option data and proxy target prices may remain useful elsewhere in the
terminal but cannot become successful G.1B.1 evidence.

Fixed-horizon forecasts are never relabeled as Q. Only the option-native expiry
cohort can carry `risk_neutral_Q_terminal`.

## Read-only telemetry

- `/api/research/g1/q/status`
- `/api/research/g1/q/instruments`
- `/api/research/g1/q/blockers`
- `/api/research/g1/q/attempts`

`runtime_validated=true` requires at least one real background successful Q
capture. Configuration alone is not runtime validation.

## Authority boundary

Throughout G.1B.1:

- `calibrator_fitted=false`
- `g1_training_allowed=false`
- `physical_probability_published=false`
- `promotion_allowed=false`
- `production_replacement_allowed=false`
- `sample_count_auto_promotion=false`

G.1C starts only after prospective Q evidence exists and G.1B.1 is audited.
