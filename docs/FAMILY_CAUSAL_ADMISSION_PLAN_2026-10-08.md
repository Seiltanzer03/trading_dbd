# P2/P3 causal source → outcome → forecast package

Accepted remote main54988fc75cd50c74b97109975364cae3cb078ba7, local1987fa8,
tree09e4a1f02cb9a2382d3d2fd36ca89b1dd5440e7b. Scope follows
LOCKED_GLOBAL_PLAN_2026-10-07.md and the original unified-edge specification.

1. Preserve actual receipt and explicit availability separately. A consensus
   received after publication cannot use an earlier available_at to become a
   prepublication input. Both clocks must precede publication. Dataset metadata
   and trainer independently revalidate retained declared availability. Historical
   fetched_at remains separate from PIT availability. RED: late receipt masked by
   early availability, malformed explicit receipt, late availability; GREEN:
   valid dual clocks, availability-only legacy/historical control, causal rows.
2. Reject explicitly conflicting imported outcome labels, execution assumptions
   and R units before fitting. Canonical observed-path counterfactual labels use
   per_unit_of_current_remaining_position and the existing piecewise-linear
   execution assumption; they are not broker fills. Legacy omitted fields remain
   compatible. RED: each conflicting field admitted/model produced; GREEN:
   exclusions, canonical/control models and existing costs/purge intact.
3. Carry these declared semantics in new model validation and check them at both
   packaging and runtime admission. Reject conflicting supplied evidence kind,
   net basis and execution assumption even with valid OOS numbers. RED: modified
   valid artifact gets packaged/votes; GREEN: refusals plus canonical trained
   artifact packages/admitted forecast. Update the factual P1–P6 map from existing
   actual publications/inventory; no family availability/model claims fabricated.

One integrator; read-only scoped preflight; one final whole-package review.
Profile checks per changed behavior, one exact final full CI, authorized merge
and actual deploy/readiness/smoke/public/required publications. No new source,
model template/search budget/gate/authority/scheduler; no duplicate frozen audit.
Existing automatic audit37736484124 is running, completion remains separate.
