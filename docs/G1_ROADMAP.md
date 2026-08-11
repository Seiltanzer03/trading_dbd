# Seiltanzer G.1 Roadmap

The G.1 program measures forecast edge before any production authority changes.
Every stage is gated by the previous stage and remains research/shadow unless a
later, explicit promotion phase proves otherwise.

## G.1A — Prospective Dataset Contract

**DONE.** Deterministic boundary between F.3.2a measurement records and future
calibration research:

- prospective eligibility and explicit exclusion reasons;
- separate forecast-evaluation vs terminal-Q-to-P eligibility;
- deterministic cohort IDs with direct/proxy/inverse provenance;
- T0 anchor dependency and conservative non-overlap effective N;
- source-record hashes and mutation detection;
- immutable cutoff-safe dataset cuts and manifest hashes;
- read-only research telemetry.

G.1A does **not** fit or select a calibrator. `g1_training_allowed`,
`physical_probability_published`, `promotion_allowed`, and
`production_replacement_allowed` remain false.

## G.1B — Baselines + Calibration Metrics

**Current stage.** Consumes only the G.1A dataset contract and measures frozen
reference forecasts without fitting a challenger calibrator:

- Q_IDENTITY for mathematically valid terminal risk-neutral Q only;
- uninformed 0.5 directional baseline;
- chronological cohort-local prequential base-rate baseline using only outcomes
  that are already in the past of the next effective observation;
- Brier score and log loss for the terminal-return-positive event;
- Q reliability bins, ECE and MCE;
- terminal-Q PIT histogram and distance-to-uniform diagnostics;
- Q and fixed Gaussian-reference quantile coverage;
- pinball loss and central interval coverage;
- primary metrics on G.1A-style conservative non-overlap effective samples;
- optional evaluation against immutable G.1A dataset cuts;
- deterministic sample-manifest hashes.

Fixed-horizon Gaussian geometry is explicitly **not** relabeled as Q or physical
P. Terminal Q is evaluated as risk-neutral Q identity and is **not** relabeled as
physical probability. CRPS remains unavailable until a proper distributional
implementation is explicitly added.

G.1B still fits no calibrator, writes no model coefficients, publishes no
physical P and grants no production authority.

## G.1C — Shadow Q→P Calibrators

Only after G.1B audit. Planned shadow challengers:

- Platt/logistic calibration;
- beta calibration;
- isotonic calibration behind an explicit effective-N gate;
- immutable fitted-parameter/model manifests;
- no online self-modifying production behavior.

## G.1D — Purged Walk-Forward OOS Validation

Only after G.1C. Chronological validation contract:

`TRAIN → PURGE → EMBARGO → FROZEN MODEL → FUTURE TEST`

No random shuffle. Planned comparisons include Q_IDENTITY, base rate and all
eligible shadow challengers using ΔBrier, ΔLogLoss, PIT/quantile improvement,
fold stability, regime stability, degradation rate and confidence intervals.
A single good fold is never sufficient for promotion.

## G.1E — Intelligence Cockpit + Research Registry

Only after the backend evidence chain exists. The cockpit will display pristine
N, effective N, cohort coverage, baseline scores, challenger OOS deltas, PIT,
reliability, quantile coverage, walk-forward folds, evidence status and explicit
promotion blockers. It must explain evidence, not decorate it.

## G.1-M — Real Trade Management Edge Engine

Separate later program. It uses real trade-management data to compare actual
management with HOLD/CLOSE_10/CLOSE_25/CLOSE_50/EXIT using regret, MFE, MAE,
risk time, drawdown and human-override impact. It must never be mixed with the
G.1 market-forecast dataset.

## Promotion boundary

No G.1 stage automatically changes production action authority. A later explicit
production-promotion research phase is required even if evidence becomes
SUPPORTED.
