# Current hot/cold history reconciliation — implementation plan

> Execute inline, one integrator. The user explicitly authorized autonomous
> design, implementation and normal release. Existing immutable source plans
> and evidence/resource/risk gates remain unchanged.

**Goal:** Preserve current verified hot outcomes while restoring every retired
> row from the pinned complete cold catalog, then give stock off-host research
> consumers a full verified database.

**Architecture:** Strict historical `hydrate_snapshot` keeps its original
> semantics. A distinct `hydrate_current_snapshot` validates all cold table
> roots, immutable overlap fields and all original hot typed rows. A bounded
> immutable state row inside the compacted SQLite file binds its catalog and
> exact retired rowids, and survives normal backup/restore. No live pruning,
> live restore or trade action occurs in this package.

**Spec:** Original `TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, Package B of
> `2026-10-16-terminal-completion-and-ai-review.md`, and findings in
> `HOT_COLD_HISTORY_REVIEW_2026-10-18.md`.

## Fixed contracts

- Canonical bucket `trading-dbd-backups-2026`; catalog key derived from the
  pinned catalog source SHA and catalog SHA, under existing `research/sqlite-history/v1`.
- Only passive `resolution_status`, `resolved_ts`, `outcome_json`,
  `calendar_elapsed`, `trading_elapsed`, `market_open_fraction` may differ
  between a retained hot row and its cold capture. All other fields, their
  SQLite storage classes, raw bytes and rowids must match. G1S has no mutable
  capture columns.
- Missing cold-prefix rows must equal the exact retired rowid set in the hot
  state. An absent state allows no missing catalog rows in the current mode.
  Appended rowids remain unchanged. Unexpected holes and resurrected retired
  rowids fail closed.
- Verify complete original cold roots by streaming the ordered verified
  partition rows through the existing typed hash codec. Verify the entire
  original hot subset before and after merge. Output is the verified current
  union, not a claim that its updated outcomes equal the old historical root.
- Existing bounded catalog, partition, output, time and disk limits remain.
  One owned worker output; no source/cloud writes during hydration; any error
  leaves the source and absent destination untouched.
- Hot state is canonical JSON <=8 MiB with <=1,000,000 retired rowids, a
  canonical payload hash and immutable write triggers. Manifest pins must
  match the state read from the fully verified database bytes.
- Full research snapshots consume the state and remove this transport marker
  only from their disposable output. They retain all domain tables and current
  hot data; neither restore proof nor model authority is relabelled.
- The embedded state also pins the retained prefix's schema and typed
  rowid/capture-id mapping for both selected tables. Normal backup/restore
  revalidates these small identity fields. Marked sources refuse VACUUM-based
  local fallback; ordinary page-preserving backups and off-host replication
  remain available under their existing capacity checks.

## Tasks and acceptance

1. **State and transport:** stdlib `seiltanzer/history_storage_state.py`,
   off-host loader, cutover writer and backup/replica/restore pin propagation.
   Test malformed/duplicate/oversized state, incorrect pins, unknown retired
   rows, absence compatibility and immutability. Reject existing state rather
   than silently replacing its epoch in the compactor.
2. **Current union:** reuse strict merge transport and typed codec with a
   distinct current entry point. RED fixtures: pending-to-resolved and current
   outcome preserved; capture mutation rejected; missing/unlisted/resurrected
   rowid rejected; post-catalog writes unchanged; all cold roots independently
   checked; failed output unpublished. Strict historical tests stay unchanged.
3. **Research routing:** bounded off-host preparation CLI and actual EDE
   workflow integration. Raw hot snapshot continues to be backed up; only
   research paths select the hydrated complete snapshot. Plain databases are
   a no-op without cloud access. Metadata/manifest disagreement fails before
   research. Test real readers against the full union on causal fixtures.
4. **Review and release boundary:** focused profiles while editing; one final
   independent review and mandatory final CI before any merge/deploy. Record
   remaining physical cutover protocol and hot-only runtime counter work.

The successful old 439-part drill is reused as historical transport evidence;
it is not rerun unchanged to make this package look complete. A fresh source
epoch for a future live compaction must be authenticated separately.

## Progress before the physical protocol

Current union RED8 absent-feature cases, then GREEN with updated outcomes,
immutable input conflict rejection, exact retired-rowid coverage and original
cold-root validation. Strict historical mode still refuses conflicting updates.
Transport RED5 real path failures: replica omitted pins, ordinary local backup
omitted pins, uploader reached cloud with a wrong pin, immutable verifier and
full cloud restore accepted that pin. Fixed in the owning paths. Additional
RED cases confirmed altered rowid mapping and an unsafe marked VACUUM fallback;
both now refuse. Profiles:43/1.55s,82passed+1existing optional skip/2.45s,
29/1.40s and40/2.17s. These are focused development checks, not full CI.
Workflow YAML and both research consumers structurally verified. Stdlib-only
worker loader and `python -S scripts/prepare_research_history.py --help` pass.

Integrated development profile before the next protocol:211passed,1existing
optional skip,1existing warning in5.66s. A subsequent zero-eligible fixture
reproduced publication of a larger no-op database; the builder now refuses it
before bulk copy. No full CI or production change has occurred at this stage.

The full production cutover remains blocked. A fresh live prefix cannot be
silently treated as the old full typed root. Next prove either a new verified
epoch or exact cold-identical retirement evidence, then finish runtime reader
coverage and a durable failure/recovery protocol before any destructive action.
