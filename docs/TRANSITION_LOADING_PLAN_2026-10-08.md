# Transition loading package — 2026-10-08

Spec: LOCKED_GLOBAL_PLAN_2026-10-07.md and TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md.
Accepted production: 9d51651cec151a9789d63a586e0b4dcbd15ae50a (PR419).
Local baseline:60779a6; treec0c07fc583874dfcb3815c68f5b28c6a4b684471.

## Objective and stop

Remove deterministic table-sized raw/decoded JSON amplification from the
existing off-host transition report. Shutdown in audit37705574141 at01:17:46Z
followed transition start01:07:37Z; discovery completed01:07:30Z. The logs
establish cancellation, not its OS/root cause. No OOM assertion or automatic
research-success assertion follows from this fix.

## Tasks

1. Stream explicitly immutable off-host source payloads in batches of32, retaining every row and
   order. Close cursors on normal completion or exception. Expected: real SQLite
   source regression fails against fetchall baseline, then exact output/145row
   control passes. Mutable/live readers retain atomic fetchall under lock. An indexed
   real SQLite same-connection resolution write reproduced mixed extraction
   before this guard and preserves the original unresolved row afterward.
   No source/feature/outcome contract or query-only change.
2. Read only requested transition observation IDs (parameter batches128),
   decode/apply/discard each original document. Preserve T0/quality/provenance
   admission and duplicate caller rows. Expected: subset returns only requested
   payloads; unsupported horizon/missing/stale/future/T0 mismatch unchanged;
   cross-batch145observations complete.
3. Process only existing transition horizons15/30/60 independently. Retain
   unresolved context through causal option/cross transforms and compute full
   coverage before baseline filtering. Retain eligible rows for fingerprint and
   search; sum exact baseline counters. One adapter keeps its original common
   available_asof. Expected: mixed resolved/unresolved two-instrument fixture
   yields identical rows, coverage, gate and fingerprint to all-horizon loading.
   Add flushed stage progress for subsequent shutdown diagnosis.

No samples truncated; no new model/source/horizon/template; no evidence gate,
search budget, policy authority or timeout increase. Retained eligible feature
rows and causal-bar caches still scale with input data; total memory is not
constant and runner success needs actual execution evidence.

## Verification and release

Three regressions red before code (after correcting test-only script import
path). Final combined profile106passed/3.82s, diff-check clean. Independent final
review and one mandatory exact-head full CI precede authorized merge; delivery,
readiness, smoke, publicHTTP and report receipts are awaited afterward.
No manual duplicate database export or unchanged full discovery is launched.
The existing automatic chain supplies subsequent production research evidence.

Independent real SQLite augmentation measurement (80 original fixture documents,
102400-byte unrelated padding each): baseline Python allocation peak17270825bytes,
streaming900475bytes (94.79% reduction,19.18x). Full augmented rows and coverage
match. This synthetic memory control is not market evidence; tracemalloc excludes
SQLite native allocations and does not prove total-memory bounds or shutdown cause.
