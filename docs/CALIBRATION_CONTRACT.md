# Q to P calibration contract

Version: `q-to-p-shadow-f3-v1`.

## Probability names

Current option-derived probabilities are pricing/risk-neutral quantities and
are stored as `risk_neutral_Q`. They are not called physical probabilities.
Until a reviewed calibration is promoted, the API publishes:

- `q_probability`: current option-implied probability;
- `p_calibrated_shadow: null`;
- `physical_probability_published: false`.

Production policy continues under the approved Q/scenario contract.

## Dataset

The first forecast of each closed trade is the independent top-level scoring
unit. Forecast rows store instrument, direction, regime, provenance, chain age,
Q take/stop/no-touch, q10/q25/q50/q75/q90, horizon and timestamp. Outcomes store
the realized competing event and realized R. Instrument and regime breakdowns
must not be pooled silently.

## Scores

- Brier and clipped log loss for take, stop and no-touch;
- the naive observed base rate beside the Q model;
- 10%-wide reliability bins with Wilson 95% intervals;
- empirical quantile coverage and pinball loss;
- CRPS only when the full predictive CDF is retained correctly.

No model is described as useful merely because its standalone score looks good;
it must beat the declared baseline out of sample.

## Time-series validation

Splits are chronological. Training observations whose forecast horizons overlap
validation are purged, followed by an explicit embargo. Random row shuffling is
forbidden. Viewed test periods become consumed research data and cannot silently
remain fresh OOS.

The initial authority threshold is deliberately conservative: fewer than 200
observations, 100 effective independent observations, or 30 events yields
`insufficient_evidence`. These counts allow evaluation only; they never trigger
automatic promotion.
