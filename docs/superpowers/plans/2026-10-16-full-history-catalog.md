# Full selected-history catalog Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; one final read-only review. Autonomous execution and release are already authorized.

**Goal:** Publish a complete, independently checked catalog of the two selected SQLite history tables in the existing private Yandex bucket.
**Architecture:** Reuse verified immutable restore tokens and bounded lossless partitions. Measure whole-table typed roots independently before partitioning, then bind every ordered chunk to that source epoch. Hydration checks these roots before publishing its output. This prepares off-host migration; it does not authorize live deletion.
**Tech Stack:** Existing Python/SQLite/S3 client, existing CI.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`; `docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`; user-authorized Yandex migration.

## Global Constraints

- Only `passive_market_observations` and `g1s_observations`; original schema, hidden rowids and raw typed values preserved.
- Existing bucket `trading-dbd-backups-2026`; content-addressed research prefix only. No backup-slot overwrite or new resources.
- Keep 1GiB worker reserve; existing 500 rows/128MiB partition and 8MiB/1024-part catalog bounds. Deadline 3600s; no unbounded in-memory payload collection.
- Rehash original bytes once after all exports and before catalog publication. Failures may leave orphan immutable chunks, never a complete catalog.
- No source writes, production retirement, simulated fills, changed gates or automatic weight activation.

## Review Focus

- Missing or duplicated chunks with plausible counts must fail whole-table typed validation.
- Negative/sparse rowids and maximum row sizes must not skip or repeat records.
- Same-size source mutation, failed GET, budget exhaustion and existing output must not publish success.
- Empty selected tables and byte caps must have explicit bounded behavior.
- Existing partial catalog fixtures remain readable without being described as full-table verification.

## Task 1: Catalog producer and complete-root hydration

**Files:** create `scripts/sqlite_history_catalog.py`, `tests/test_sqlite_history_catalog.py`; modify `scripts/sqlite_history_union.py`.
**Interfaces:** `publish_full_catalog(source, destination, *, source_receipt, client, maximum_hydrated_bytes) -> dict`; returns catalog identity and false authority. Catalog adds `full_table_proofs` with original DDL, ordered columns, rowid alias, count and typed SHA256. Union calls `verify_full_tables(conn, catalog)` before publication and reports whether that verification occurred.

- [ ] Write integration tests: complete two-table export over multiple chunks, source unchanged, raw typed roots and join results identical after cloud hydration; no authority gained.
- [ ] Write refusal tests: wrong whole-table root despite correct count, source changed after export, corrupt catalog readback, insufficient budget, existing destination, invalid source proof, empty table, source epochs/duplicate chunks.
- [ ] Run focused tests and observe missing producer/root-check failures.
- [ ] Implement bounded keyset exporter, per-chunk full cloud GET, independent whole roots, final source hash, content-addressed catalog full GET and exclusive local publication; integrate optional full roots in union.
- [ ] Run catalog/partition/union/restore profile. Expected: all pass. One fresh review; fix Important/Critical in one regression-backed pass.
- [ ] Commit and publish final tree; one mandatory full CI, merge only green. Exact-SHA production acceptance stays mandatory.

## Task 2: Full selected-copy drill

Use an existing Actions off-host worker only after code checks. A new full restore is justified for complete selected-cohort proof, not repeated capacity/sample diagnosis. Preflight working-space budget before starting. Archive all rows of both selected tables, then compare restored typed roots and real reader joins against independently recorded original results. Production stays untouched; no partial fixture may be relabeled full migration. Record actual run, source hash, counts, catalog hash and limitations. Live physical compaction and historical-reader/writer closure remain a separate gate.
