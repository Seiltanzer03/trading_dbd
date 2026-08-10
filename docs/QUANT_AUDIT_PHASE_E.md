# SEILTANZER Quant Audit — Phase E

Implementation baseline: `06e80bea0327618ac32b8f3879ccb91a1a299f5a`  
Public runtime after Phase E: `ai_policy.py → ai_policy_v16`,
`ai_verdict.py → ai_verdict_v18`.

## 1. Executive summary

Phase E tightens semantics and validation around the existing derivative-state
work. It does not add production authority. The deterministic v14 policy path
still selects `HOLD / CLOSE_10 / CLOSE_25 / CLOSE_50 / EXIT`; v16 evaluates and
logs a shadow robustness candidate. The invariant is:

```text
promotion_allowed = false
sample_count_auto_promotion = false
production recommendation = authoritative v14 result
```

The completed corrections are:

- `GAMMA_STRESS` and `CORRELATION_STRESS` now perturb different model dimensions;
- hard stress feasibility uses BASE plus only formally material dynamic stresses;
- derivative values, slopes and accelerations have separate dimensional units;
- robust slope normalization is bounded in horizon and protected by a numerical
  noise floor;
- option-state aggregation is hierarchical, so transforms of one distribution
  cannot create confirmation by count;
- v18 separates improvements from deterioration and production action from the
  shadow candidate;
- switch levels are labelled deterministic scenario-weight sensitivity, not OOS
  calibrated market thresholds;
- the journal stores the state before the outcome and resolves it later without
  claiming a causal counterfactual;
- execution-cost sensitivity is explicit robustness context;
- Cross-Asset describes relationship level separately from measured relationship
  change;
- critical JavaScript smoke tests are part of the deploy gate.

## 2. Stress semantics

### GAMMA_STRESS

Observed free option OI and model gamma reveal magnitude/geometry, but not the
actual dealer inventory sign. Phase E therefore does not manufacture an adverse
directional drift.

```text
gamma magnitude g ∈ [0, 1]
effective sigma = base sigma × (1 + 0.15g)
effective drift = base drift
```

Drivers come from GEX stiffness/force interaction geometry inside the existing
`option_distribution` family. The scenario means local pinning, unstable gamma
curvature or a harder boundary. It remains non-directional unless a separately
observed directional input exists.

### CORRELATION_STRESS

Correlation stress represents loss of diversification/regime confidence rather
than local option-field geometry.

```text
correlation magnitude c ∈ [0, 1]
effective sigma = base sigma × (1 + 0.20c)
effective drift = base drift × (1 - 0.50c)
```

This shrinks favorable drift toward zero and widens risk. It does not infer
causal lead/lag or automatically reverse direction. Its drivers are observed
network tension, fragmentation, break velocity and break count in the single
`correlation` family.

The two scenarios are therefore mechanically distinct:

| Scenario | Drift | Volatility | Interpretation |
|---|---:|---:|---|
| GAMMA_STRESS | unchanged | `+15% × g` | local option-field instability |
| CORRELATION_STRESS | confidence shrink | `+20% × c` | systemic coupling/regime dislocation |

## 3. Materiality

BASE is always required. A dynamic scenario receives hard-feasibility authority
only when all published conditions pass:

```text
normalized weight >= 0.05
driver confidence >= 0.30
source quality >= 0.48
observed sample span >= 5 minutes
```

The 0.05 weight floor equals the existing deterministic sensitivity-grid step.
The confidence floor is the minimum used for a derivative estimate to be more
than a weak diagnostic. The 0.48 quality floor is aligned with the lowest
accepted option-anchored snapshot quality; scenario-only fallback quality is
0.25 and cannot pass. Five minutes is the derivative contract's minimum real
span.

Every scenario publishes `material`, `materiality_reason`, weight, confidence,
source quality and span. Non-material stresses still contribute to the weighted
diagnostic distribution, but cannot hard-veto a candidate. Candidate feasibility
is evaluated against BASE and each currently material stress. A tiny nonzero
weight is never equivalent to hard authority.

## 4. Units and normalization contract

Every temporal metric exposes distinct fields:

| Quantity | `value_units` | `slope_units` | `acceleration_units` |
|---|---|---|---|
| `p_take`, `p_stop`, no-touch | probability | probability/min | probability/min² |
| barrier EV | R | R/min | R/min² |
| q10/q50/q90, width | R | R/min | R/min² |
| IV/RV/VRP/skew/term | native ratio or vol points | native/min | native/min² |
| mapped price distance | price or R | price/min or R/min | price/min² or R/min² |

The legacy `units` field remains as a deprecated slope-units alias for compatible
consumers. New code must use the explicit fields.

Slopes require at least six unique timestamps across five real minutes;
accelerations require eight across ten. Estimation is EWLS with Huber IRLS
residual weighting. The standardized derivative signal uses:

```text
signal change = slope × min(observed span, 20 minutes)
normalization noise = max(robust residual noise, 0.5% of canonical metric unit)
z = signal change / normalization noise
bounded signal = tanh(z)
```

The horizon cap prevents a steady old trend from growing indefinitely merely
because the process has been running longer. The numerical floor makes flat and
near-flat series converge toward zero instead of exploding when residual noise
is almost zero. Stale states are unavailable rather than treated as fresh zeroes.
Synthetic tests cover flat data, clean trends, isolated outliers, noisy trends,
regime shifts, one-point spikes, duplicate timestamps, irregular cadence, stale
series and long spans.

## 5. Option-family aggregation and policy authority

The option distribution is aggregated by concepts, not number of transforms:

```text
OPTION DISTRIBUTION
├── EDGE: barrier EV and BOP
├── TAIL: q10/q50/q90, width and tail asymmetry
├── LOCAL_HAZARD: take/stop conditional hazard
├── VOLATILITY: IV, RV, VRP, skew and term
└── GEX_CONTEXT: field/force/stiffness and wall/flip geometry
```

Within each group, available confidence/attribution is averaged first. Groups
then receive equal conceptual treatment. EV and BOP do not count as two
independent votes. Quantiles, width and tail ratio do not create four votes.
GEX remains context-only because dealer sign is unknown.

Authority by family remains:

| Family | Role |
|---|---|
| option distribution | one shadow robustness state; no independent votes |
| live price | observed directional context |
| order-flow levels | one levels family |
| correlation | stress/confidence context, never causal direction |
| macro | strategy context |
| wavelet | derived-price explanatory context |
| policy outcome | joint Expected/median/CVaR/P(loss) distribution |
| hard risk / ladder / BE | authoritative constraints, unchanged |

Statistical confidence and source quality are separate. Confidence describes
whether the time-series estimate is stable enough; source quality describes the
feed, freshness, proxy mapping and option-anchor quality. High statistical
confidence cannot repair a stale or weak source.

## 6. Verdict and threshold semantics

V18's deterministic section is ordered as:

1. production action and shadow candidate;
2. main reason;
3. what improved;
4. what deteriorated;
5. material pressure;
6. ignored/non-material/low-confidence context;
7. sensitivity that would switch the shadow candidate.

It never uses “ПОЧЕМУ ИЗМЕНИЛОСЬ” when the production action did not change. If
the shadow candidate differs, the report explicitly says production remains the
v14 action. If execution-cost support disappears on the tested grid it prints
`EDGE NOT ROBUST TO EXECUTION COST`.

`scenario_weight_sensitivity_thresholds` reweights cached scenario results over
a 0.05 bounded grid while holding other current raw weights, paths, noise and
confidence relationships fixed. Output is explicitly:

```text
type = scenario_weight_sensitivity_threshold
method = deterministic_cached_scenario_reweighting
oos_calibrated = false
llm_generated = false
```

It is not an empirically calibrated raw-market threshold and is not a promise
that production action will change. The legacy function name remains only as a
compatibility alias. Arithmetic and thresholds are deterministic; if an LLM
omits required sections, v18 replaces its response with the deterministic
fallback.

## 7. OOS shadow validation and execution costs

Before an outcome exists, each review stores:

- production and candidate policy plus reason;
- review R and expected/CVaR/cost deltas;
- scenario weights and material scenarios;
- selected derivative state;
- option-state confidence and source quality separately;
- instrument and observed regime;
- full execution-cost sensitivity summary.

Closing a trade adds final R, resolution time, stop/take/other classification and
MFE when available. MAE remains null when the journal has no observed adverse
excursion; it is never fabricated.

The report exposes agreement, policy changes, candidate stability, tail/expected
diagnostics, false-hold/false-exit proxies, turnover, execution-cost fragility,
time to resolution and outcome classes. Final trade R is not a causal
counterfactual for the unexecuted policy. These are proxy diagnostics only.
No number of samples automatically enables promotion; a future promotion needs
separate reviewed OOS evidence for tail-loss improvement, false exits/holds,
turnover, regime stability and cost robustness.

Execution-cost sensitivity tests `[0.005, 0.010, 0.015, 0.020]R` when cost is an
assumption, or `[0.5×, 1×, 1.5×, 2×]` of an observed baseline. It subtracts only
the explicit incremental turnover burden between old and candidate close
fractions because the simulator already charges eventual closing costs. It is
`robustness_context`, `independent_vote=false`, and not causal PnL.

## 8. Cross-Asset, motion and performance

Cross-Asset continues to publish every finite off-diagonal pair. Relationship
level and change are now separate explanatory states:

- `STABLE_HIGH_COUPLING`;
- `STABLE_DECOUPLED`;
- `CORRELATION_BREAK`;
- `CORRELATION_REVERSAL`;
- `SYSTEMIC_RECOUPLING`;
- `TRANSITION`.

No direction is drawn without a measured lead/lag model. Packet activity still
uses observed delta/velocity/tension and decays to rest. Wavelet transfer remains
based on real energy-share change; GEX motion uses price impulse and analytic
field geometry; Macro targets update only after accepted market/model data.

Phase E adds no high-frequency Monte Carlo loop. Scenario paths remain cached
within one review. Existing frame budget, staggered analytics, offscreen pause,
IV mesh reuse, camera gesture budget and Lattice behavior are retained. The
camera guard now rejects a stale responsive HOME-camera reset during gesture
settling while preserving an intentional HOME command and multi-touch lifecycle.

## 9. Test and deployment gate

Python tests cover distinct gamma/correlation perturbations, material versus tiny
stress veto, dimensional units, robust derivatives, family anti-double-counting,
scenario feasibility, verdict wording, sensitivity labels, cost robustness,
OOS persistence/resolution and Cross-Asset relationship states.

Both CI and the main-branch production workflow directly run:

```text
correlation_modes_smoke.mjs
plotly_toolbar_smoke.mjs
real_market_motion_smoke.mjs
plotly_camera_guard_mobile_smoke.mjs
```

CI additionally runs real WebKit iPhone camera and Cross-Asset control tests.
Production deploy remains push-to-main, runs the complete Python suite and
frontend smoke gate first, then verifies server HEAD, service state, `/api/state`
and all analytics endpoints before publishing `production/seiltanzer` success.

## Final authority statement

Phase E improves mathematical honesty and observability, not authority. Derived
states may explain robustness and create an OOS candidate, but cannot alter the
live recommendation until an explicit, reviewed, separately committed promotion.

