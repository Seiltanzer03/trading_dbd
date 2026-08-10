# Simulator execution contract

Version: `execution-simulator-f0-v1`.

## Economic state

The stochastic model generates one shared set of future paths in R units. All
five policies consume those same paths (common random numbers). The execution
layer maintains, per path, active/closed state, active stop, BE state, ladder
fills, remaining fraction, realized cash flow, exit reason and event time.

The initial stop is `-1R`. Once `be_after` is first crossed, the active stop is
`0R`. Its first subsequent crossing is absorbing. A path closed by stop, BE or
take cannot produce later economic cash flow.

Each future ladder rung can fill once. A fill realizes its R level on the
configured fraction of the original position. At a review, that original
fraction is normalized to the position remaining then:

`future fill fraction = original rung fraction / original remaining fraction`.

Already crossed rungs are never paid twice.

## Within-step ordering

Deterministic replay treats adjacent points as a continuous segment. Upward
crossings are processed by price: rung, BE activation at the same price, then
take. Downward crossings use the stop active before that downward move. For a
gap-like observed bar this is explicitly a barrier-fill/no-slippage assumption,
not tick-exact execution.

Monte Carlo endpoints retain the existing Brownian-bridge outer-barrier logic.
The execution layer shares those outer stop/take events and samples an armed
BE-only bridge crossing when both endpoints remain above zero. The residual
multi-barrier/discretization bias must be measured by step convergence; it is not
represented as exact continuous-time first-passage ordering.

## Costs and policies

The simulator produces the gross strategy outcome for the current remainder.
The existing policy cost layer then applies immediate and deferred full-close
costs. `EXIT` is fixed at current R less immediate cost and is independent of
future path. `HOLD`, `CLOSE_10`, `CLOSE_25`, and `CLOSE_50` combine an immediate
close with the same remaining strategy path.

## Permanent invariants

- BE is absorbing after activation.
- Stop, BE and take terminate economic exposure.
- Remaining fraction stays in `[0, 1]`.
- A rung fills at most once.
- All policies use identical future paths.
- Seeded simulation and execution are reproducible.
- Execution costs are applied once in the policy layer.

The deterministic fixtures A–G and Monte Carlo regression tests live in
`tests/test_execution_simulator.py`.
