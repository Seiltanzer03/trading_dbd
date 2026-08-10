# Quant audit — Phase F measurement foundation

Audit base: `origin/main` at `098e6139cff97600ce59433a2b66f009a9f9c343`.

## Result

The suspected BE defect was present. `ai_policy_v2.baseline_strategy_outcomes`
inferred fills from final `max_r` and changed a negative terminal value to zero
when `max_r >= be_after`. That allowed an economically dead path to cross BE and
later receive take/ladder cash flow. The production simulator now carries an
absorbing execution state. No challenger stochastic model was added.

## Audit matrix

| Component | Source/formula | Units/cadence | Family/authority | Finding | Disposition |
|---|---|---|---|---|---|
| Path diffusion | option sigma, shrunk forward, skew, term; stepped Gaussian path | R per AI review | option distribution, production baseline | Shared paths and deterministic seed were correct; continuous-time approximation remains discretized | KEEP + MEASURE |
| Brownian bridge | endpoint conditional lower/upper crossing | event fraction | option distribution | Outer barriers handled; prior economic BE was absent. Multi-barrier order retains measurable approximation error | FIXED + CONVERGENCE |
| Ladder | configured rungs/fraction and trade `max_r` | R/fraction | strategy execution | Current-remainder normalization was correct in v2; max-based future payout was not path-dependent | FIXED |
| Break-even | `be_after=1.5R`, active stop `0R` | R/event time | strategy execution | Terminal clipping was mathematically different from stop-at-BE | BROKEN → FIXED |
| Policy comparison | immediate fraction plus remaining outcome | net R per review | deterministic policy | Common random numbers already correct | KEEP |
| Execution costs | immediate/deferred full-close R cost | R per policy | execution | Applied once in v4 and compared with net CVaR floor | KEEP |
| Expected R | sample mean of net policy outcomes | R per review | policy objective | Correct estimator but false precision was possible | KEEP + SE/CI |
| Median | sample median | R per review | policy diagnostic | Correct, dependent on same outcomes | KEEP |
| CVaR10 | mean of worst 10% net outcomes | R per review | hard risk | Correct empirical definition; uncertainty absent | KEEP + tail SE/seed spread |
| P(loss) | empirical net outcome `<0` | probability | policy risk | Correct; uncertainty absent | KEEP + binomial SE |
| First event geometry | next rung versus active stop | probability/time | strategy execution | Previously used original stop after BE | FIXED |
| Seed stability | repeated deterministic simulations | winner/share/R spread | numerical diagnostic only | Was mixed with parameter robustness rather than isolated | ADDED |
| Option forecast probability | option barrier/RND scenario | Q probability per chain/review | option distribution | UI naming could be read as physical probability | Q LABEL + CALIBRATION SHADOW |
| Shadow scenario ensemble | derivative-weighted stress scenarios | net R | option distribution, shadow | Correctly `promotion_allowed=false`; coefficients are not OOS proof | KEEP SHADOW |
| Policy shadow outcome | final trade R proxy | R per trade | research | Explicitly warned non-causal but could not answer alternative-policy outcome | REPLACED BY PATH REPLAY |
| AI snapshot | tick/ridge/policy state | immutable review | explanation only | Full JSON existed in verdict history but lacked dedicated versioned research key/path | IMPROVED |
| Macro 3D endpoint | runtime adapter uses real 5m history and observed correlation history | context | macro | Correct runtime adapter replaces an obsolete synthetic prototype still present in the class body | KEEP runtime; clean dead prototype later |
| Wavelet endpoint | runtime adapter computes CWT from real 5m history | context | price-derived | Correct runtime adapter replaces an obsolete synthetic prototype; insufficient history returns no-data | KEEP runtime; clean dead prototype later |
| TradingView feed | authenticated WS if token exists; Yahoo fallback otherwise | price tick | source quality | Empty token raises an explicit error and fallback provenance is preserved | KEEP; verify production secret |

## Before/after execution example

Path `0 → 1.5 → 0.5 → 0 → 2.0` previously armed BE via `max_r`, then could pay
the final take because only a negative terminal was clipped. It now fills the
1.0 and 1.5 rungs, exits the remainder at the first 0R touch, and ignores 2.0.

## Numerical observability

Each policy now reports scenario/effective path count, Expected-R SE and 95% CI,
CVaR tail count and conditional-tail SE, and probability SE/CI. The live review
adds five deterministic seeds, winner stability, ranking agreement and Expected/
CVaR seed spreads. `decision_uncertain` is diagnostic only. The 1k/3k/6k/12k/24k
convergence ladder is an offline helper so routine reviews do not blindly pay the
24k cost.

## Edge measurement machine

Completed reviews persist immutable model inputs, all five policy outputs,
source state, versions and seed. Real post-review paths support cost-aware policy
replay, regret, MFE/MAE and time under risk. Q forecasts now retain full
calibration fields and are scored against base rate with leakage-safe split
contracts. Human overrides and experiment hypotheses must be frozen before
outcomes/test results.

All new components remain measurement/shadow infrastructure. Production policy
authority and automatic deployment mechanics are unchanged.

## Environment follow-up

The successful production run still prints pip warnings for invalid
distributions named `~%iltanzer` and `~0iltanzer` under
`/opt/seiltanzer/.venv/lib/python3.12/site-packages`. Service restart, the full
test gate and HTTP checks all succeeded, so these are stale/corrupt package
metadata entries rather than an active import failure. They should be inspected
and removed during an explicit production-venv maintenance window, followed by
`pip install -e ".[dev]"` and the full deploy gate. The deploy workflow does not
blindly delete site-packages directories.

`TRADINGVIEW_AUTH_TOKEN` remains an optional deployment secret. When absent,
the direct TradingView fetch raises an explicit provenance error and the Yahoo
fallback is labelled as indicative/broker fallback; it is not silently labelled
authenticated or direct.
