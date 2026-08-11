# Phase G.1C — Shadow Q→P Calibration Engine

G.1C is Seiltanzer's first trainable research layer. It does not change any
production trading decision. Its job is to freeze simple challenger mappings
from option-implied terminal risk-neutral Q to a shadow calibrated probability
that can later be tested honestly in G.1D.

## Data boundary

Training input is only `G.1A q_to_p_eligible` data from an immutable G.1A dataset
cut. Before fitting, G.1C revalidates the cut manifest and source-record hashes.
Unresolved, replayed, dirty, synthetic, wrong-runtime, fixed-Gaussian or mutated
observations are not training data.

The initial binary event is `terminal_log_return > 0`. Raw Q is computed from the
frozen T0 terminal distribution as `1 - F_Q(0)`.

## Challenger families

- Platt: monotonic logistic calibration of logit(Q).
- Beta: monotonic beta calibration with non-negative shape coefficients.
- Isotonic: weighted PAVA, admitted only behind the larger evidence gate.
- PIT isotonic CDF: research foundation for a future calibrated terminal CDF;
  it is not a first-touch model.

All fitting is deterministic and dependency-aware. Every dependency group has a
total training weight of one, so repeated observations from the same anchor do
not gain arbitrary influence.

## Evidence gates

Platt/Beta require at least raw N 60, effective N 30, 15 positive and 15 negative
outcomes. Isotonic/full-CDF research require raw N 120, effective N 60, 30/30
outcomes and sufficient Q variation. G.1D readiness is a separate, larger gate:
raw N 200, effective N 100, 30/30 outcomes, multiple temporal periods and expiry
clusters, and no critical contract errors.

Crossing a gate never grants production authority.

## Immutable registry

G.1C stores append-only fit runs, shadow models and prospective predictions.
Model identity includes algorithm version, scope, training cut hash, target and
weight contracts, parameters and artifact SHA256. Old models are never rewritten.

Refits are checked at most every six hours and require new effective evidence of
`max(10, 10% of previous effective N)` unless the model contract itself changes.

## Prospective shadow predictions

The most important G.1C output is not a training score but a frozen future-facing
prediction ledger. A new option-native Q observation may receive shadow
predictions only from models that already existed at its T0 and whose training
cutoff is strictly earlier than T0. An observation that was part of a model's
training cut cannot be predicted by that model.

The T0 Q row itself must pass the current schema/runtime/background/direct-price,
expiry, Q-contract and frozen-CDF admission checks before a shadow prediction can
be written. No model can be backfilled into old observations and called
prospective.

## Authority

Every G.1C artifact remains `research_only`:

- `production_authority=false`
- `production_replacement_allowed=false`
- `promotion_allowed=false`
- `sample_count_auto_promotion=false`
- `physical_probability_published=false`
- `edge_claim=false`
- `production_model_training_allowed=false`

Training diagnostics such as Brier/log loss/reliability are explicitly in-sample
and `oos_validated=false`. G.1D is responsible for determining whether any
challenger actually improves future unseen forecasts.

## Research APIs

- `/api/research/g1/calibrators/status`
- `/api/research/g1/calibrators/models`
- `/api/research/g1/calibrators/cohorts`
- `/api/research/g1/calibrators/predictions`

With today's still-unresolved Q evidence, the correct production result is zero
frozen models plus explicit `INSUFFICIENT_EVIDENCE` readiness blockers. That is a
healthy fail-closed state, not a reason to weaken the evidence contract.
