# P6 Q evidence: saved working checkpoint

Base: merged PR #414, main `4c64e27f723e48a43523e0706e42b10f5d4f5e4a`,
tree `7df2b0185cc0eaab29d972997a94907f1e44f598`.
Branch: `fix/p6-q-summary-evidence`. This change is not released.

The bounded Q summary counted distinct dependency anchors as effective N,
included membership without successful background capture provenance, ignored
evidence maturity, and counted repeated capture attempts as separate resolved
observations. It now uses the existing non-overlap effective-N helper and
authoritative evidence-status criteria, filters forecast eligibility and mutated
sources, and deduplicates observation IDs while retaining actual attempt counts.

The capture provenance query uses an IN subquery rather than scanning the attempt
ledger separately for each observation. No schema migration, source collection,
model fitting, gate relaxation or production authority change is introduced.

Four behavioral regressions failed before the fix. Final targeted verification:
17 passed (Q materialized evidence, G1C materialized readiness, Intelligence
cockpit); one installed Starlette/httpx deprecation warning. Independent read-only
review approved the diff with no blocking findings. Full CI and production
acceptance are reserved for the next combined release, not this checkpoint.

P1 independent scheduler remains inactive because the spare host is unavailable.
Existing GitHub delivery does not prove an independent 600-second cadence.
P2–P6 remain evidence gated; this correction supplies no new observations or
validated models. Historic audit counts are not fresh production measurements.
