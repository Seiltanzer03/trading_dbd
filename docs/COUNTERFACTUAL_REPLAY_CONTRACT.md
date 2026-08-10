# Counterfactual replay contract

Version: `counterfactual-replay-f2-v1`.

Every completed AI review writes an immutable decision snapshot before its
future outcome is known. The canonical JSON is SHA-256 hashed and stored with a
unique review id, capture time, production/shadow policies, policy/simulator/
scenario/calibration/feature versions and Monte Carlo seed. The persistence API
has no update operation for snapshot content.

Numeric source timestamps named `ts`, `*_ts`, or `*_at` are rejected when they
are later than the review capture time. This is a guard, not a substitute for
source-specific slicing tests.

After the review, real terminal ticks append `(timestamp, price, R)` points to
every unresolved review. Demo observations remain explicitly labelled
`synthetic_demo_points`. On trade resolution, the path is frozen and replayed
through the same execution state machine for:

- `HOLD`
- `CLOSE_10`
- `CLOSE_25`
- `CLOSE_50`
- `EXIT`

The replay applies the costs and ladder/BE state captured at decision time. It
publishes gross/net realized R, best policy, production and shadow regret, MFE,
MAE, time under risk, exit reason and execution path. `EXIT` cannot depend on
future price.

Observed points are not tick-complete. Barrier order between observations uses
the published piecewise-linear barrier-fill/no-slippage assumption. Reports
therefore carry a path-resolution warning and `causal_claim=false`.

Research output is available through `/api/research/counterfactual` and is also
included in `/api/validation`. It never grants production authority.
