# PR C — Option derivative shadow contract

This state is collected for out-of-sample validation. It cannot change policy
scores, hard-risk constraints, confirmations, or `HOLD/CLOSE/EXIT` selection.

## Authority and cadence

- `family = option_distribution`
- `independent_vote = false`
- `authority = shadow_context`
- `policy_influence = none`
- server observations only; browser animation is never an input
- at most one accepted observation per 30 seconds
- slopes require at least 6 unique timestamps spanning at least 5 minutes
- acceleration requires at least 8 observations spanning at least 10 minutes
- estimator: exponentially weighted least squares with Huber IRLS residual weights

Every metric publishes value, slope, acceleration, robust residual noise, sample
count, time span, confidence, source quality, units, estimator and availability.
Unavailable estimates remain null; two-point slopes are prohibited.

## Option geometry

`BOP = log((P_take + eps)/(P_stop + eps))` with `eps=1e-6`.

`up_tail = q90-q50`, `down_tail = q50-q10`,
`tail_ratio = up_tail/max(down_tail, eps)` and
`tail_log_ratio = log(tail_ratio)`. R coordinates are favorable-positive for
both long and short positions, so a falling tail log-ratio is adverse.

`width = q90-q10`. Width velocity is interpreted with center and tail velocity,
not as a standalone direction signal.

The finite-horizon barrier EV remains `T*P_take-P_stop`; no-touch mass is not
folded into stop and this value is not the full marked-to-market policy EV.

## Conditional first-touch hazard

For checkpoint interval `k`, with survival at its start
`S(k-1)=1-P_take(k-1)-P_stop(k-1)`:

`h_take(k) = (P_take(k)-P_take(k-1))/S(k-1)`

`h_stop(k) = (P_stop(k)-P_stop(k-1))/S(k-1)`

The local ratio is `log((h_take+eps_h)/(h_stop+eps_h))`, using a half-path
continuity correction only to keep the logarithm finite. Counts and survivor
mass are exposed for audit. `next_window` is a fixed `1/n_slices` of the model
horizon, independent of the adaptive visualization checkpoints, so hazards at
different current prices remain comparable. Separate take and stop conditional medians are now
published; legacy `median_years` remains the mixed resolution clock solely for
compatibility.

## Analytic GEX geometry

For robust-scaled OI×Black-Scholes-gamma weights `g_i`:

`FIELD(S) = sum(g_i exp(-(S-K_i)^2/(2h^2)))`

`FORCE(S) = -dFIELD/dS`

`STIFFNESS(S) = d2FIELD/dS2`

Both derivatives are analytic Gaussian-kernel derivatives and are tested
against central finite differences. Their bounded scores use the same bandwidth:
`tanh(FIELD/2)`, `tanh(FORCE*h)` and `tanh(STIFFNESS*h^2)`. Because free OI does
not reveal actual dealer positioning, GEX stays context-only.

## Interaction normalization

All composites expose formula, components, explanation and source quality.
Robust dynamic components are standardized by their own residual noise and
bounded with `tanh`; final composite output is also `tanh(z)`. There are no
independent votes inside the interaction state. The family summary is the equal
arithmetic mean of available same-family attribution components, never a count
of confirmations.

## Promotion gate

PR D may compare a candidate policy in shadow logs, but authority requires
enough realized outcomes to evaluate agreement, tail loss, false early exits,
false holds, turnover and execution-cost change. Until then every AI snapshot
must explicitly state that these derivatives did not alter the selected action.
