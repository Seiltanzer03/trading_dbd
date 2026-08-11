# G.1B Baseline Measurement Contract

## Purpose

G.1B measures the quality of already-frozen forecasts admitted by the G.1A
prospective dataset contract. It does not fit a Q→P calibrator and does not
change any production decision path.

## Source boundary

Only `g1-prospective-dataset-v1` members with `forecast_eval_eligible=1` are
consumed. Source-mutated observations are excluded. Reports may evaluate either
the live G.1A eligible view or a frozen immutable G.1A dataset cut.

Per-cohort metric diagnostics use deterministic cohort-local non-overlap samples.
One T0 dependency group contributes at most one representative per cohort and an
observation whose future window overlaps the previously accepted window in that
cohort/instrument does not increment that cohort's metric-task N.

Top-level evidence N is stricter: it reuses the G.1A aggregate
instrument/dependency non-overlap contract across all horizons and cohorts. Thus
seven horizon tasks from the same T0 cannot become seven independent pieces of
system evidence. The report keeps both `pooled_metric_task_n` and the stricter
system `effective_n` so the distinction is visible.

## Directional event

The binary event is versioned as `terminal-log-return-positive-v1`:

`Y = 1[future_log_return > 0]`

This is a terminal event. It is not a first-touch event.

## Baselines

### Uninformed 0.5

A fixed `p=0.5` reference for the terminal-return-positive event.

### Prequential base rate

The current integrity contract is
`g1-prequential-base-rate-resolved-time-v2`. It is cohort-local,
chronological and Laplace-smoothed:

`p_t = (successes_available_before_t + 1) / (n_available_before_t + 2)`

A prior outcome may enter the historical state only when its recorded
`resolved_ts <= current captured_ts`. Merely reaching the prior target time is
not enough. If `resolved_ts` is absent, that outcome never enters historical
state. The current observation is added to the history only for later T0s.

This prevents a retrospective report from using an outcome that was not actually
available when the next forecast was made.

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

Pooled cross-cohort metrics are diagnostic task-level summaries; top-level
system evidence status uses the stricter G.1A aggregate effective N. Any
Q-vs-baseline deltas are explicitly descriptive. Positive improvement means
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

## Reproducibility

The top-level report includes a deterministic aggregate dependency-evidence
manifest hash. Pooled metric-task rows retain a separate task-level manifest.
Frozen G.1A dataset cuts can be supplied by `cut_id` for exact historical
reproduction.

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
