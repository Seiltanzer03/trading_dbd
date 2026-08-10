# PR D — Derived scenario and validation contract

The production action remains the v14 deterministic policy result. PR D adds a
v15 robustness ensemble and a shadow candidate, not automatic authority.

## Scenario ensemble

Every AI review evaluates `BASE`, `EDGE_CONTINUATION`, `EDGE_MEAN_REVERSION`,
`VOL_EXPANSION`, `SKEW_ADVERSE`, `GAMMA_STRESS` and `CORRELATION_STRESS`.
Weights come from robust derivative confidence, normalized derivative signal,
transparent interaction scores and observed correlation stress. `BASE` has one
anchor unit and all raw weights are normalized to sum to one.

Scenario perturbation scales are reused from existing tested policy contracts:
drift ±0.04R and skew ±0.05 from policy-v5 local stability, and sigma +15% from
source-authority stability. Execution costs and the active net CVaR floor are
installed in the same contexts used by the production policy calculation.

For every policy the ensemble reports weighted Expected net R, weighted scenario
median, weighted scenario CVaR10, P(loss), worst stress Expected/CVaR, stress
survival, policy stability and existing source stability. A shadow candidate
must pass the net hard-CVaR floor in BASE and every material weighted stress,
then uses the existing 0.03R Expected indifference band.

## No automatic promotion

`promotion_allowed=false` is invariant in this PR. The production recommendation,
confirmation gate, hard-risk rules and execution permission are not mutated.
The verdict states explicitly whether only the shadow candidate changed or no
policy changed.

The journal records old policy, candidate policy, reason, review R, expected/CVaR
and execution-cost differences before the future result is known. Closing the
trade resolves those observations. The report calculates agreement, changes,
expected/tail diagnostics, turnover and false-exit/false-hold proxies. Final
trade R is not a causal counterfactual for an unexecuted action, so observation
count alone can never promote the model; reviewed out-of-sample calibration is
required.

## Deterministic thresholds and explanation

Derivative switch thresholds are found by reweighting the already-simulated
stress ensemble over a fixed 0.05 bounded grid. They are model outputs, not LLM
arithmetic. The final report starts with the production action, then shows what
changed, what supports it, what contradicts it, what was ignored for low
confidence, and what deterministic derivative stress threshold would switch the
shadow candidate.

