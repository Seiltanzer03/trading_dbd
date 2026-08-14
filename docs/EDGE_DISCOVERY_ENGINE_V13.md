# Edge Discovery Engine v1.3

EDE v1.3 keeps the v1.2 causal-validation contract and moves the active research focus to bounded selective edge on 15/30/60 minute prospective T0 data.

## Selective search

- primary comparator remains `GLOBAL_RET5_PERSISTENCE` on identical filtered outer-test rows;
- search is predeclared and bounded to at most 320 templates and three conditions;
- price, volatility, options, option dynamics, cross-asset and regime features participate only when causally available at T0;
- Benjamini-Hochberg FDR and primary-only outer aggregation are unchanged;
- diagnostic folds never increase edge maturity or AI confidence.

## Practical interpretation

The research report contains practical coverage, dependency-family redundancy, temporal stability/decay, asset concentration, economic path summaries, baseline-failure regimes, family comparisons and option/static-dynamic interaction sections. These fields rank and explain research results; they do not grant authority.

v1.3.3 adds post-selection stratified diagnostics. For each primary candidate, the audit reconstructs the same outer-fold conditional and global-persistence probabilities and scores them by instrument, session and available trend/volatility/macro/wavelet regime. A stratum is displayed as descriptive only after 20 raw and 10 effective observations. These strata are explicitly excluded from selection, FDR and `EDGE_MATURITY`, so `WHERE_IT_HELPS`/`WHERE_IT_HURTS` cannot become a post-hoc promotion mechanism.

Family ablation is also reported separately for 15/30/60m. It means the best bounded conditional candidate inside each family envelope; it is not presented as a separately refitted multivariate ML model.

## Regime-transition sub-audit

v1.3.4 adds a separate bounded sub-audit for transition state that the V3 collector was already freezing before the outcome. It exposes macro boundary distance, transition velocity/acceleration and state coordinates, plus wavelet spectral concentration, persistence, ridge velocity/power slope, dominant period, energy-transfer rate and cycle-shift state.

`regime.wavelet_phase` was aligned in PR #100 to the actual numeric `phase_stability` value already materialized by the base adapter. The transition sub-audit reuses that canonical ID in interactions; it does not duplicate the same phase-stability value under another feature name.

No historical value is reconstructed. The sub-audit reads only `g1s_evidence_v3` frozen at T0 and rejects stale or future-dated source blocks. It uses the same `GLOBAL_RET5_PERSISTENCE`, nested temporal folds and FDR machinery but a separate predeclared universe capped at 120 templates and two conditions. This prevents adding transition variables from silently changing the canonical EDE multiple-testing universe after results are observed.

Any transition signal remains diagnostic until a separate review adds the additional feature IDs to the canonical registry. `shadow_eligible=false` for this sub-audit, even if a research signal is found.

## Prospective shadow

Candidates that meet the existing canonical research gate and have a causal deployment refit may create immutable `PROSPECTIVE_SHADOW` predictions only after the rule already exists. Prediction records precede the target outcome; resolution is appended separately. Rolling 25/50/100/all evidence tracks decay without auto-promotion.

## Safety

`production_authority=false`, `production_directional_authority=false`, `auto_promotion=false`, and `may_trigger_exit_or_close=false`. EDE v1.3 does not change HOLD/partial/CLOSE/EXIT policy and does not enlarge the production decision authority.
