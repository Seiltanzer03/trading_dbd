# Seiltanzer G.1 Roadmap

The G.1 program measures forecast edge before any production authority changes.
Every stage is gated by the previous stage and remains research/shadow unless a
later, explicit promotion phase proves otherwise.

## G.1A — Prospective Dataset Contract

**Current stage.** Build the deterministic boundary between F.3.2a measurement
records and future calibration research:

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

Consumes only the G.1A dataset contract. Planned measurement layer:

- Q_IDENTITY baseline;
- historical/train-frozen base-rate baseline;
- Brier score and log loss;
- PIT evaluation and reliability bins;
- ECE/MCE;
- quantile coverage and pinball loss.

Goal: measure how well the unmodified risk-neutral terminal Q predicts realized
market outcomes. G.1B still does not fit challenger calibrators.

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
