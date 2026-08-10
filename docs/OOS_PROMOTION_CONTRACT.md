# OOS promotion contract

New features and models start at Level 0 or 1. No runtime code promotes them.

| Level | Authority |
|---|---|
| 0 | context only |
| 1 | shadow prediction |
| 2 | shadow policy candidate |
| 3 | low-authority confirmation |
| 4 | production policy influence |

Before evaluating a test window, the experiment registry freezes hypothesis,
features, formula, thresholds and ordered train/validation/test periods. A test
window can be consumed once. Threshold changes after viewing it require a new
experiment and future test period.

A promotion report must include sample and effective sample size, date span,
instruments, regimes, source quality, production/HOLD/price-only/without-feature
baselines, Brier, quantile loss, CRPS where valid, realized R, CVaR, drawdown,
tail avoidance, time under risk, decision regret, bootstrap intervals, ablation,
sensitivity, worst instrument and worst regime.

Correlated members of one mathematical family are ablated as a family. P(take),
barrier EV, quantiles, tails and BOP remain `option_distribution`; they are not
independent votes. Dealer-sign-dependent GEX stays context unless dealer sign is
observed.

Promotion requires an explicit reviewed code/config change and a new release.
Sample count, a single backtest, one instrument, one regime, or a favorable
shadow result never changes production authority automatically.
