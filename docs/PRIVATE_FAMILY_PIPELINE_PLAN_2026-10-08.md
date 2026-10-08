# P2: connect the verified private archive to the existing causal pipeline

Authority: locked P2/global plan and original unified-edge specification.
The attached historical research briefs are context, not new execution requests.
Preserve information -> OOS -> decision -> economics, instrument-specific
state, immutable source clocks, existing costs and admission gates.

Baseline: accepted PR423 remote7a681457/tree239caf24. PR424 already contains
the reviewed bounded transport; this extension supplies its actual caller.
One integrator; isolated existing worktree. One final whole-package review.

1. Add optional exact-SHA verification before and after the existing bounded
   read-only review export. Preserve legacy CLI compatibility. RED: mismatch
   still exports/writes; GREEN: both checks, 32-review/byte limits unchanged.
2. Restore verified private history before export; run existing causal pipeline
   off-host, commit archive through the two-slot helper, expose only explicit
   aggregate counters. RED: corrupt latest still invokes exporter, synthetic
   input/raw identities leak in public summary, history/clock changes. GREEN:
   two sequential generations restore exact source history and no models is a
   valid committed result. No runtime activation or database writes.
3. Wire only trusted main workflow_dispatch with current exact SHA. Serialize
   all actual object-storage writers through one constant job concurrency group
   and never cancel an active writer. Preserve the private-repository GitHub
   artifact path and public refusal for that backend. No public raw artifacts.
   Include regression tests in credential-free contracts. Profile tests first,
   final mandatory CI once; actual private roundtrip only after accepted release.

Interfaces: existing exporter receives optional expected_sha; private job
receives injected storage client/export callback, current source SHA and positive
run generation. Restore before every attempt resolves ambiguous prior commits.
Public summary is newly constructed from counts/status flags, never a filtered
copy of private diagnostics. Original historical research archives remain local.

Review focus: trusted-event/SHA/credential boundary, workflow cancellation and
writer serialization, stale/ambiguous storage state, public raw-data exposure,
synthetic model admission, bounds/native clocks/history, zero-model outcome.
Completion: tested controlled workflow plus actual storage/production receipts;
source capture != validated model != forecast != realized economic benefit.
