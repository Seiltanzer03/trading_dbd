# Provider finalization: bounded reuse of identical simulations

## Observed defect

The ordinary production review `ai-ee429d751ec44a64b312` on main
`bd615fbd5481a61625c31e62d19f91fd2277dcfd` returned HTTP 200 but fell back
to deterministic mode. Run `39490395260`, attempt 2, supplied the phase trace:

- provider HTTP: 5489.973 ms; decode: 1.053 ms;
- local provider validation: 2053.638 ms;
- outer six-second provider guard expired before validation finished;
- the same extended-policy simulations had already run before the provider.

This is not evidence that the model failed to answer. Repricing repeated
the identical deterministic simulation during local validation.

## Bounded correction

Each actual review owns a fresh ContextVar scope, copied through the existing
worker pools. It retains at most 32 read-only terminal outcome vectors, each
one-dimensional and at most 1200 finite floats. Keys include the complete
immutable PolicyInputs, seed and applicable conditional-action execution
parameters. Nothing is shared between reviews. Late workers retain only
their original review scope.

Only pure simulated outcomes are reused. Source authority, geometry, costs,
hard floors, material benefit and execution admission are checked again.
No cached admission result, full path bank or snapshot is retained. Model,
route, provider timeout, fallback, risk rules and manual execution are unchanged.
The new `provider_extended_validation` trace separates this local work.

## Verification and release boundary

- Regression before correction: 8 failed / 1 passed (4.52 s), including a
  real bounded-provider timeout with repeated simulated work.
- Regression after correction: 9 passed (2.27 s).
- Owning profile: 88 passed (9.77 s), including extended management,
  provider guard, structured contract, workers and actual API route.
- Independent final review: no critical, important or minor findings;
  independently 9 passed (2.54 s). Reviewed key completeness, source/risk
  rechecks, memory bounds and late-context isolation.

One mandatory final-tree CI, exact-SHA deployment and one ordinary live review
remain required. Test success is not a claim that the production LLM budget
has already passed. The observed slow startup (742 s, mostly storage checks)
and insufficient research disk reserve are separate, unchanged blockers.

The remaining global scope stays in TERMINAL_REMAINING_WORK_2026-10-20.md:
actual LLM acceptance, one guarded physical hot/cold transition, applicable
runtime experts and final acceptance. No new model search or DB proof copy
is required by this correction.
