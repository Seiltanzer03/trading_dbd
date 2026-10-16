# Verified off-host history union — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans.
> User selected autonomous inline execution with accelerated focused tests.
> Continue without another approval menu; one final read-only reviewer.

**Goal:** Reconstruct a complete history SQLite copy from verified hot input
and its pinned private Yandex partition catalog, for existing SQL readers.

**Architecture:** Keep active SQLite local and unchanged. An off-host worker
copies a verified immutable hot snapshot, fully verifies each required cold
partition and merges exact typed rows with original rowids. Publish the new
file only after all catalog counts and integrity checks pass; never overwrite
hot rows or silently accept an omitted/conflicting partition.

**Tech Stack:** Existing Python3.11+, sqlite3, Yandex S3 client and reviewed
scripts/sqlite_history_partition.py. No new library, provider or scheduler.

**Spec:** docs/YANDEX_HISTORY_EXECUTION_2026-10-16.md and
docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md. Detailed original hot/cold
requirements are preserved in the ops checkpoint YANDEX_HOT_COLD_CONTRACT.

## Global constraints

- No source writes, live retirement, schema/clock/rowid changes, real orders,
  fills, ACK, synthetic labels, admission/promotion or weaker risk/source gates.
- Catalog raw bytes SHA256 must match cold_history_catalog_sha256 in the
  verified hot input's storage_manifest; no caller-selected partial catalog.
- At most8MiB catalog bytes,1024 partition receipts,25GiB output. Each partition
  retains existing500row/128MiB/8MiBrow/30s SQL limits. Copy budget300seconds;
  overall operation1800seconds, checked before/after each bounded operation.
- Existing1GiB reserve: require2×catalog maximum output plus3×128MiB staging
  before allocation. Never remove source or replace an existing destination.
- Source restore fd identity including ctime must remain unchanged throughout.
- A source catalog/typed union is not a portable complete-backup retirement
  receipt; production_authority and data_retirement_authorized remain false.
- Missing partitions, inconsistent schema/counts, rowid/PK conflicts and failed
  FK/quick_check are explicit refusals. Missing is not zero.

## Review focus

- A missing/extra catalog item must fail, not shrink historical cohorts.
- An identical duplicate may be idempotent; a different row at an existing
  rowid or PK must fail without publishing output or changing hot input.
- Preserve both hidden rowids and SQLite storage classes, even invalid-UTF8
  TEXT, and retain every unarchived newer hot row.
- Target INSERT triggers, foreign keys and interrupted SQL must not mutate
  unrelated history or leave a half-published output.
- A late file replacement or insufficient worker capacity must refuse without
  source deletion, fallback authority, synchronous HTTP/cloud or VPS writes.

## Task1 — authenticated catalog and complete typed union

**Files:** create scripts/sqlite_history_union.py and
tests/test_sqlite_history_union.py. Existing archive helper stays unchanged
unless a covering failing test proves a shared-contract defect.

**Interfaces:**
- Consumes restore_verified_snapshot.restore receipt, catalog bytes whose
  SHA256 is pinned in receipt.storage_manifest, existing cloud partition
  receipts, sqlite_history_partition.restore_from_cloud.
- Produces hydrate_snapshot(source, destination, *, source_receipt,
  catalog_bytes:bytes, client) -> dict. Output identifies actual file SHA/size,
  hot SHA, catalog SHA, typed/count verification, false production/retirement
  authority and a new local file identity. No source global clock is changed.
- Catalog contract sqlite-history-catalog-v1: partitions list, exact
  expected_table_counts for every partition table, maximum_hydrated_bytes.

- [ ] Write RED cases: exact hot+cold union; preserve newer hot row; identical
  overlap; differing rowid and differing PK; wrong/missing catalog pin; missing
  object; duplicate object entry; wrong counts; existing destination;
  copy/source interruption; low capacity; trigger refusal.
- [ ] Run focused tests; expect missing hydrate_snapshot or refused invariant.
- [ ] Implement bounded bytes parsing, full input identity binding, immutable
  sqlite backup and typed merge in a disposable file. Check every overlap by
  original column names/storage types and original rowid before insertion.
- [ ] Run archive+union profiles; expect all PASS without network/paid calls.
- [ ] Commit explicit files only; preserve unrelated worktree artifacts.

## Task2 — real archived-row reader proof and handoff

- [ ] Reuse small private row partitions from successful38893965916; do not
  restore another whole19GiB input merely to repeat known capacity/codec facts.
- [ ] On a disposable explicitly partial test cohort, prove same SQL results
  before partitioning and after hydration; label this subset, not full source.
- [ ] Full production-cohort equivalence and any live cutover remain separate
  gates requiring the actual complete catalog and affected reader/writer map.
- [ ] One final read-only review; one Critical/Important fix pass with RED/GREEN.
- [ ] Publish branch evidence. One canonical CI only when the finite package
  is ready for release; no blind main/deploy/model reruns.

This helper is not permission to prune a hot DB. Future selection must also
retain the highest source rowid in every pruned table to prevent implicit
rowid reuse, and must finish all resolution/enrichment/dependency owners.
