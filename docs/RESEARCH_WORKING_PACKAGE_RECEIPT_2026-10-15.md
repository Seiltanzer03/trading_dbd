# Source-bound research working package — implementation receipt

## Accepted scope
Original user plan and handoff remain unchanged. This addendum records the
finite package approved 2026-10-15 at 01:22 Moscow. Accepted baseline is main
`fdffdbde030164bf4cd00fba24ef8f6d0d133121`, tree
`cbe2dee2b61001bc79e674bd2aff83dda1c85b7e` (local `e1f06e8` has the same tree).

## Implemented behavior
- Family heads select DIRECT, INVERSE or fixed BASELINE_SHRINK using early
  purged validation only, then evaluate untouched final chronological labels.
  Delivered head fingerprints bind the transform; legacy heads default DIRECT.
  A negative final result still receives no historical forecast admission.
- Existing completed admitted price bars supply preliminary momentum,
  pressure and eligible opening/closing session hypotheses. Pressure is not
  observed book depth, OFI or broker tape. Same price source is counted once.
- Existing official CPI/NFP actual/previous readings supply a labelled
  release-change reference. Unknown publication times retain actual first
  availability as an explicitly different clock. No pre-news consensus is
  invented. CPI previous reading derived from current/change is labelled.
- Financing assumptions use a six-scenario grid (annual 0/5/15%, charged 1/3
  days) in original risk units. They are not verified broker tariffs or cost
  ceilings and never replace verified Expected/CVaR, costs or execution gates.
- Preliminary inputs stay manual/context-only and out of historical model
  training; factor .25 is an explicit policy limit, not calibrated accuracy.
  Existing trained math opinions and actual family inputs take precedence.
  Frozen prompt and audit preserve roles and numeric scenario assumptions.
- Reviewed canonical Yandex managed restore from PR #471 is included in the
  same release; original manifest/hash/SQLite/epoch/source verification remains.

## Verification and review
Owning integrated profile: **202 passed in 5.19s**, Python 3.12.14.
A regression first reproduced five failures: a future relative to original
snapshot cutoff context, and incomplete alternative metadata. `_valid_context`
now requires observation <= original context origin <= current observation
cutoff <= capture, while allowing an older frozen context after quote reprice.
Consumed alternative metadata is checked before family/prompt/audit projection.

An interrupted earlier review reported those two findings. Final read-only
independent review completed against the full final product diff: **no Critical
or Important findings**, 264 focused tests passed, whitespace check clean.
Full mandatory final PR CI, merge, exact-SHA deploy/acceptance and normal
post-deploy mathematical publication remain pending at this implementation receipt.

The predecessor fdff ActiveEdge run `38643171332` completed successfully,
including all five structured horizons, ML and their publication jobs. It no
longer blocks changing the installed SHA. No duplicate research was dispatched.

## Limits
These are usable manual preferences and labelled alternative inputs, not
reconstructed broker observations, proven optimal weights or net profitability.
Missing observed data, unsupported forecasts and G.1 evidence limits retain
their existing explicit reasons. No orders, fills or user positions are changed.
