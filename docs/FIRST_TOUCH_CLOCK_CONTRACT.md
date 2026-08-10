# First-touch clock contract

Version: `first-touch-clock-f1-v1`.

Resolution is the first terminal managed-position event: TAKE, original STOP,
or absorbing 0R break-even after BE is armed. A partial ladder fill is not a
resolution; a ladder that exhausts the remainder is favourable terminal
resolution. Horizon paths are censored for quantile identification.

The clock is extracted from `strategy_exit_time` and `strategy_exit_reason` in
the same `simulate_option_paths()` execution bank used for Expected R, CVaR and
policy geometry. It does not resimulate or change the distribution.

For each time slice:

`F_take(t) + F_stop_or_be(t) + S(t) = 1`.

Unconditional P50 is the first time total resolved mass reaches 50%. If resolved
mass at H is below 50%, P50 is `null` and `median_status=beyond_horizon`.
`conditional_median_given_resolved_minutes` is separately labelled and never
displayed as ordinary P50. Time basis is `calendar_elapsed`; payload units are
minutes (with an explicit years adapter for existing charts).

Cone and Fan consume the same backend object. Browser `touch_clock.js` is a
formatter only: no Brownian inversion, barrier selection, RV/IV adjustment or
distance fallback remains. Missing/stale option state produces no fabricated
time.

Live display extraction is parameter-bucket cached to respect the frame budget;
an AI/policy review uses its exact cached policy bank. The first review after a
new parameter state performs the heavy calculation, while unchanged ticks only
read cached state.

