# G.1B Baseline Measurement Contract

## Purpose

G.1B measures the quality of already-frozen forecasts admitted by the G.1A
prospective dataset contract. It does not fit a Q→P calibrator and does not
change any production decision path.

## Source boundary

Only `g1-prospective-dataset-v1` members with `forecast_eval_eligible=1` are
consumed. Source-mutated observations are excluded. Reports may evaluate either
the live G.1A eligible view or a frozen immutable G.1A dataset cut.

Primary metrics use a deterministic cohort-local non-overlap subset. One T0
dependency group contributes at most one representative per cohort and an
observation whose future window overlaps the previously accepted window in that
cohort/instrument does not increment primary effective N.

## Directional event

The binary event is versioned as `terminal-log-return-positive-v1`:

`Y = 1[future_log_return > 0]`

This is a terminal event. It is not a first-touch event.

## Baselines

### Uninformed 0.5

A fixed `p=0.5` reference for the terminal-return-positive event.

### Prequential base rate

`g1-prequential-base-rate-laplace-v1` is cohort-local and chronological. Before
each prediction it uses only prior effective observations in that cohort:

`p_t = (successes_before_t + 1) / (n_before_t + 2)`

The first prediction is therefore 0.5. The current observation outcome is added
to state only after its prediction has been emitted. G.1A non-overlap sampling
ensures the previous accepted outcome window has ended before the next accepted
sample in that cohort.

### Q identity

Q identity is evaluated only when G.1A marks the observation `q_to_p_eligible=1`.
For the terminal up event:

`Q(S_T > S_0) = 1 - F_Q(0)`

where the support is log return relative to T0 spot. No transform to physical P
is applied.

## Binary metrics

G.1B reports:

- Brier score;
- log loss;
- 10 fixed reliability bins;
- ECE;
- MCE.

Any Q-vs-baseline deltas are explicitly descriptive. Positive improvement means
baseline loss minus Q loss. They are not an edge claim.

## PIT

For Q-eligible effective observations, G.1B recomputes:

`PIT = F_Q(realized_log_return)`

and verifies it against the frozen F.3.2a stored PIT using tolerance `1e-6`.
Mismatches fail closed for G.1B Q metrics. The report includes a 10-bin PIT
histogram, PIT mean/variance, KS distance to Uniform(0,1), and maximum histogram
bin deviation. No p-value is published because small/correlated samples would
invite false precision.

## Quantile metrics

Levels: 0.10, 0.25, 0.50, 0.75, 0.90.

For each level G.1B reports empirical coverage, coverage error and pinball loss.
It also reports central 50% and 80% interval coverage.

For option-native Q the quantiles are inverted from the frozen terminal Q CDF.
For fixed horizons the already-frozen Gaussian reference quantiles are evaluated
only as `historical_gaussian_reference_geometry_not_Q_not_physical_P`.

CRPS remains `null` in G.1B.

## Evidence language

`INSUFFICIENT`, `EARLY`, `PROVISIONAL`, and `SUPPORTED` in G.1B describe the
amount/span of baseline measurement evidence only. They do not mean a Q→P
calibrator has edge and they do not permit promotion.

## Authority invariants

Every G.1B report keeps:

- `calibrator_fitted=false`;
- `calibrator_registry_writes=false`;
- `g1_training_allowed=false`;
- `physical_probability_published=false`;
- `production_authority=false`;
- `promotion_allowed=false`;
- `production_replacement_allowed=false`;
- `sample_count_auto_promotion=false`.

G.1C remains a separate gated stage.
