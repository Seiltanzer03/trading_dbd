# Verified off-host history union — 16.10.2026

Continuation of the original terminal plan, not a replacement specification.
Previous archive/lifecycle package merged as49174b; its exact-SHA production
acceptance is still running at this checkpoint. No live rows retired.

## Implemented finite step

`scripts/sqlite_history_union.py::hydrate_snapshot` reconstructs an off-host
SQLite copy from a verified immutable hot input and its pinned complete catalog.
Raw catalog SHA must match the verified storage manifest. Every required cloud
partition is fully read back and typed-verified. Original hidden rowids, SQLite
types, opaque JSON and all newer hot rows are preserved. Identical overlaps are
idempotent; conflicting rowids/PKs, missing objects, incorrect schemas/counts,
INSERT trigger execution, FK errors, changed source, deadlines or insufficient
worker capacity refuse publication. An existing destination is never replaced.

Catalog bounds:8MiB/1024 partitions; output at most25GiB. Worker preflight keeps
1GiB reserve plus2×maximum output and3×128MiB staging. Copy≤300s, each merge≤30s,
whole operation≤1800s. No live checkpoint, HTTP request-time cloud fetch, VPS
credentials, new provider, scheduler or production trading action is added.

Output has `history_union_verified`, not `full_restore_verified`; production
and data-retirement authority remain false. A catalog pin is mandatory: existing
snapshots without an authenticated catalog are deliberately not accepted as
hot/cold inputs. The catalog producer is not implemented by this helper.

## Evidence

- Initial12 tests failed because the helper was absent;18 then passed.
- One independent read-only final review found an Important SQL trigger guard
  bypass via comments/case. Both cases reproduced RED; SQLite's actual trigger
  authorizer now refuses execution, while UPDATE-only T0 guards remain intact.
- Final integrated union/archive/restore/lifecycle profile:85passed in1.88s.
  Full mandatory CI belongs to the release candidate, not repeated local runs.
- Actual private Yandex drill38898501123 at12:09UTC used reviewed code68aafcd
  and existing two32-row objects, with no whole19GiB restore or cloud writes.
  A disposable explicitly partial fixture retained half its rows locally and
  hydrated all64 rows from the pinned fixture catalog. Full typed row hashes
  and52 nonempty instrument/horizon SQL groups matched the pre-split fixture;
  hot input stayed unchanged. Only public hash/count proof was exported.
  See YANDEX_HISTORY_UNION_PARTIAL_PROOF_2026-10-16.json.

## What this does NOT establish

This is not equivalence of the full production cohort, all197 historical SQL
references, resolution/enrichment writers or live API latency. It does not free
production disk, validate cold-row eligibility/dependency closure, implement a
production catalog or prove a safe physical cutover. No arbitrary retention,
source-clock rewrite, forecast admission or smaller WAL reserve is authorized.

Next finite gate: complete catalog/closure and affected reader/writer proof,
then a rowid-preserving compacted-copy cutover with current backup, bounded
pause, rollback and actual OS free-space verification. SQL DELETE alone does
not free disk; VACUUM may renumber hidden rowids. Any future hot retirement must
retain the highest rowid in each table to prevent automatic rowid reuse.
Only after actual source/WAL headroom returns can the blocked EDE stage resume.
