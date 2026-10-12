# Confirmed execution state and effective expert participation

Baseline: main fc52f412bbf6b40842d587c1e2f311da98adc5a5,
tree b017b32edca717a3fdce9655ad23d7100166dbd4. Scope authorized by the user's
2026-10-12 report of false stop/take HTTP400 and ineffective expert weights.

## Reproduced causes

Read-only production position133: NAS100 long, entry30735, original stop30531,
take31190. The observed OANDA price was approximately30695. The only stop
amendment event was BE_ARM from strategy_rule, without broker confirmation.
The old ledger treated that proposed entry stop as executed and blocked reviews.

Latest saved review, 2026-10-12 12:02UTC: balanced nominal40/15/15/15/15;
actual quantitative92.5%, current LLM7.5%, others unavailable. Three absent
budgets plus dependency discount were all transferred to quant. All11
alternatives to HOLD were excluded before selection, including positive-score
CLOSE_25 and TIGHTEN_STOP. This is not evidence that HOLD beats alternatives.
Those weights are a dated saved review, not a new current provider request.

## Finite tasks and interfaces

1. Separate BE_ARM strategy intent from manually confirmed stop events.
   Actual stop/state/price preflight and simulation inputs must agree. Past
   max_r remains observed history and is not proof of broker execution.
   Add explicit live BE confirmation to the policy/execution inputs; retain
   legacy research behavior when confirmation is absent. Show pending strategy
   BE separately. Do not change production trades or fabricate executions.
2. Normalize available expert preference budgets proportionally after existing
   quality/freshness/dependency checks, including sparse per-action abstention.
   Preserve null missing opinions and compulsory risk calculations. Nominal
   priors stay40/15/15/15/15; statistical optimality is not claimed.
3. For a complete authoritative open-position manual snapshot, admit base
   candidates to comparison without requiring a prior legacy winner selection.
   Keep geometry/source, execution-cost contract, hard CVaR, common-bank checks,
   material benefit and manual broker confirmation. Reduced legacy snapshots
   retain their old admission path. Option anchors and extended-source checks
   remain required; no stale anchor or missing family forecast is fabricated.

The state change feeds policy inputs and common replay; the preference/admission
change consumes those consistent economics. These shared interfaces have one
integrator. Fourteen regressions were observed RED, including conditional replay propagation
and newly activated take/spike levels below a past peak but above the current
price. Past touches do not execute orders created later. Final owning profile:
452 passed in4.64s. Final review/CI and exact-SHA acceptance remain pending.

Stop after the integrated owning checks, one independent review, full mandatory
CI and accepted deployment. Verify one legitimate fresh review for the current
open trade, if no actual terminal barrier applies; do not send broker orders.
Record live component/candidate reasons before declaring every family active.
All12 action handlers existing does not imply all12 actions apply to every
market state;24 fitted market/family groups is not eight universally available
predictive families.
