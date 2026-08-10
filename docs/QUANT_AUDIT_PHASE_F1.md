# Quant audit Phase F.1 and Phase G foundation

Starting reference: `3f74ba728db7d378c2c4a98eef3b572d58f27238`.

## Correctness changes

| Area | Previous failure | F.1 contract |
|---|---|---|
| AI review | journal no-lookahead ValueError escaped as text/plain 500; frontend parsed it as JSON | scheduler timestamp removed from snapshot; stable JSON API, request correlation and deterministic provider fallback |
| Resolution clock | independent one-sided browser Brownian clock selected a barrier heuristically | competing TAKE vs STOP/BE extraction from execution MC; unresolved mass can make P50 null |
| Q outcome | lifetime max R and final P&L labelled finite-horizon forecasts | canonical path is sliced at forecast H; incomplete paths are censored |
| Baseline | descriptive test-set event rate could be presented as naive improvement | OOS baseline is fitted on TRAIN and frozen before TEST |
| Counterfactual risk time | every policy inherited full path duration | per-policy calendar and exposure-weighted time; EXIT equals zero |
| MC ranking | valid zero could become `-999` via truthiness | explicit `None` handling |
| Convergence | independently regenerated path sets were called nested | honestly labelled fixed-seed path-count study; bridge/BE high-step bias is offline diagnostic |
| Stability | transitions crossed trade boundaries | transitions are grouped within `trade_id`, with instrument/regime breakdown |

The Gaussian Brownian-bridge formula remains an approximation under transformed
skew increments. `execution_step_convergence()` measures bias against a higher
step reference; it makes no exactness claim and runs research/offline only.

## Phase G prospective foundation

Headline OOS unit is the first eligible forecast per trade. Repeated reviews are
a secondary panel clustered and bootstrapped by `trade_id`. The initial
challenger is a low-dimensional coherent simplex Platt transform of the Q vector
TAKE/STOP_OR_BE/NO_TOUCH. Identity parameters reproduce Q; fitted parameters must
come from TRAIN and be frozen before TEST. The experiment registry freezes the
hypothesis, formula, features, thresholds and chronological TRAIN/VALIDATION/TEST
windows before outcomes, and consumes a TEST window once.

Forecast quality (Brier/log loss/reliability/quantile loss) remains separate from
policy value (counterfactual R/regret/CVaR/risk time/cost/turnover). No current
sample proves edge.

## Authority invariants

- `promotion_allowed=false`
- `production_replacement_allowed=false`
- `sample_count_auto_promotion=false`
- `physical_probability_published=false`
- production HOLD/CLOSE/EXIT authority is unchanged
- LLM failure cannot change deterministic policy

