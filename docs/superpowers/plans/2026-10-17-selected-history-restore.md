# Selected history restore Implementation Plan

> **For agentic workers:** superpowers:executing-plans inline, one final read-only review. Autonomous implementation and release already authorized.

**Goal:** Reconstitute both complete history tables directly from the verified Yandex catalog, without another full backup restore or source connection.
**Architecture:** A separate off-host selected-table artifact, built from pinned catalog bytes and independently verified chunks. Reuse the existing typed merge and original whole-table roots. The full hot/cold union keeps its global FK gate unchanged; this artifact cannot replace production or authorize retirement.
**Tech Stack:** Existing Python/SQLite/S3.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, existing full-catalog plan2026-10-16. Actual439-part publication covers219023rows; original broad union failed its global FK check after whole selected roots passed.

## Global Constraints

- Existing private bucket/prefix only; do not upload/recompress verified parts or restore another19GB source.
- Exact expected catalog SHA required, <=8MiB, <=1024parts, existing500row/128MiB/8MiB-row budgets.
- Keep1GiB worker reserve,25GiB output bound,1800s deadline; exclusive output, clean only owned temporary files.
- Only the two existing selected tables. No real position, source database, provider call or retirement operation.
- Result explicitly `full_database_integrity_verified=false`, `production_authority=false`, `data_retirement_authorized=false`. Never mint a full-restore/union/production token.

## Task 1

Create `scripts/sqlite_history_selected_restore.py`, `tests/test_sqlite_history_selected_restore.py`.
Interface: `restore_catalog_tables(client, destination, *, catalog_bytes, expected_catalog_sha256)->dict`.
- [x] RED: source deleted after catalog publication; restore all typed values/rowids and reader join using only cloud parts; artifact contains exactly both selected tables.
- [x] RED: incorrect pin/root, duplicate JSON members, missing/corrupt parts, existing output, low space/deadline/output bound; no output on failure. Empty tables remain explicit.
- [x] Implement strict catalog pin/coverage/budget checks, exact original schemas, bounded cloud restore and merge, whole-root verification, quick_check and file SHA. Preserve broad FK/union gate.
- [x] Profile selected/catalog/partition/union/restore tests once:104passed3.11s. Added7strict/empty cases, selected18passed0.98s. One final review: no Important blockers, no edits.
- [ ] Mandatory final CI and authorized merge.

## Task 2

One finite Actions run: GET catalog7b78d25d13f249f1ab967828675419a6656fe6b69787e5b78644076a54411a63 from source epochd6e8c423f21585f68cd28b0d837eb08c944d2b273bbba59ad963927e33434535. Restore439existing chunks, compare complete typed roots, compute selected-table groups/joins. Print small proof only; no row payload artifact, no source restore, no cloud writes. Source global FK failure and runtime dependency closure remain explicit blockers to live compaction.

## Review Focus

Pin forgery/duplicate keys; resource bounds during native merge and final hash; hidden-rowid/type preservation; schemas of empty selected tables; result cannot masquerade as a full verified production database.
