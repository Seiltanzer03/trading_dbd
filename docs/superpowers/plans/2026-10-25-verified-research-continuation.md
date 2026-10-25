# Verified research continuation — Implementation Plan

> Execute inline with superpowers:executing-plans; one final independent review.

**Goal:** Correct the verified #518/#519 integrity/provenance defects and resume the existing off-host EDE chain without weakening its history proof.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`; `docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`; user continuation after Argon quota exhaustion.
**Architecture:** Preserve current SQLite and official-source contracts. Reuse a startup integrity result only for an unchanged DB/WAL identity, preserving its original check time/scope. Keep each Federal Reserve series receipt and family-specific URL through bundle/runtime ingestion. Diagnose the actual union bound/capacity failure before changing analytical hydration.
**Tech Stack:** Python/SQLite, existing GitHub CI and Yandex Object Storage.

## Constraints

- Baseline main a2110bc41326730274b041bbcde78a6b5f931d6c. No newer PR found at start; remote-only partial branches are checked separately.
- No live orders/ACK, fabricated clocks, models, quotes, source authority or profit. Existing risk/admission gates remain.
- Do not repeat completed cutover/cleanup/search or export a large DB for a small diagnostic.
- One profile per changed area, one final branch review, mandatory exact-tree CI and authorized release/acceptance.

## Tasks

1. Storage: add failing changed-source and WAL-change regressions after successful prestart; cached schema calls must not hide a later change. Record DB/WAL/journal stat identity around a real integrity check; reuse only a matching stable identity, keep the original integrity timestamp/scope, and otherwise run the real post-schema check. Preserve the unchanged-file fast path. Profile `tests/test_storage_availability_guard.py` plus owning storage tests.
2. Macro: reproduce wrong NFP URL through actual fallback/bundle ingestion. Return family-specific primary URLs plus all seven bounded URL/body-hash/receipt/vintage records; bind proof to canonical payload without adding deploy metadata to it. Validate family/series provenance and preserve first-seen semantics. Profile official bundle/numeric/transport/history tests.
3. EDE: inspect latest failure, pinned manifests/catalog and actual effective output bound versus physical worker capacity. Add the smallest behavioral regression for the confirmed cause; change owning hydration/capacity logic only after diagnosis. Preserve verified raw backup and all cold/hot identity checks. Resume from existing verified input where possible.
4. Final: update factual 8×13/model/release status, one independent whole-branch review, mandatory full CI, authorized PR/merge/deploy and exact-SHA publications. Read actual child workflow outcomes. Do not claim fresh LLM provider participation from an old report or price-unavailable fallback.

## Review focus

- WAL-only changes and repeated schema calls must invalidate stale integrity reuse.
- A check's timestamp/scope cannot become newer by reusing it.
- Mixed ALFRED/FRED transport must preserve all per-series provenance and use NFP URLs for NFP.
- Current FRED observations cannot acquire historical ALFRED availability.
- Union page/output limits and physical free space are distinct; no partial analytical snapshot may be published.

## Progress and rulings

Pre-flight: storage and macro share only final release; EDE consumes verified snapshot/catalog independently. Runtime and off-host macro consumers must change together if the private fallback return shape changes.
Ruling: existing clean linked worktree is reused; no new environment/dependency setup or whole-suite baseline repetition. Earlier exact-tree CI and focused review already establish baseline; new behavior receives RED→GREEN and mandatory final CI.
Ruling: execution/release is already authorized by the user and AGENTS.md; no additional plan approval gate.

Task 1: local RED→GREEN: changed/corrupt DB, committed WAL change after cached schema pass, honest reused scope/time; 10 availability tests passed.
Task 2: local RED→GREEN: family-specific CPI/NFP URLs, seven original response receipts, wrong-family/rehashed proof rejection, mismatched vintage rejection, latest receipt distinct from immutable first-source/available_at. Two nullable metadata columns preserve the first and latest receipts without changing legacy rows or canonical numeric payload hashes.
Task 3: pinned catalog metadata observed by read-only run40174509315: maximum19,858,247,680B, current hot18,562,621,440B, 439partitions/12,710,387,712B uncompressed, fresh worker free91,903,422,464B. Tiny real-SQLite reproduction hits SQLITE_FULL in the same _merge while hot file remains below the old page cap. Both current-hot growth and merge-cap regressions observedRED→GREEN; history union/prepare owning34tests passed.
Ruling: the frozen catalog's byte bound describes the old full image. Current analytical hydration gets old-image budget plus measured current-hot bytes, capped at the unchanged25GiB global limit; physical capacity preflight uses that effective limit. Old catalog bytes/pin, row/root proofs and ordinary historical hydration limits remain unchanged. Cost: current growth eventually exceeds25GiB and then still fails closed; this is not durable retention.
Owning integrated profile:339passed/1failed/1existingStarlette warning. Failure test_storage_sparse_backup_guard::test_quiescent_sparse_clone_replays_wal_only_into_backup is the previously known Python3.12/SQLite3.53 source-signature failure; sparse clone source is untouched. Mandatory Python3.11 CI remains required.

Final independent review ofa2110bc..35ee640:0Critical,1Important,1Minor. Important receipt loss after reconfirmation reproducedRED→GREEN; immutable first receipt now remains available to its originalT0 while latest receipt is current operational metadata. Updated macro profile28passed1.49s. No re-review.
Final minor (deferred): rehashed ALFRED metadata with mutually matching malformed vintage_date strings passes the receipt validator; normal exact-SHA builder generates valid ISO dates. Date-shape hardening is outside this fix pass.
Final Ruling: mandatory CI/production/actual EDE outcomes were deferred by the reviewer to root — they remain release gates, not inferred success. The old sparse-WAL failure was reproduced on unchanged baseline; no source guard is relaxed. Cost if the environment diagnosis is wrong: mandatory Python3.11 CI blocks merge.

PR520 mergedf29bfa7 on matchingtreebc7104e. PRCI40175484396 andmainCI40175875986 both4812passed5skipped13subtests; pinnedSQLite/WebKit/acceptance green. Actual deploy40176150355 delivered/readiness passed but macro build failed FED_SERIES_VINTAGE_MISMATCH; smoke/public/postresearch skipped. This release is NOT fully accepted.
Actual observer40177003759 at08:32:22UTC:installedf29,PID3828215,NRestarts0,HTTPstate200125ms,worker core140.6s completed/last_errornull,RSS584MiB normal. Startup492.6s, of which post-schema verification290s. No fabricated fast-start claim.
Bounded source diagnostic40177231958 reproduces ALFRED requested2026-10-25 returning CPIAUCSL_20261024 and PAYEMS_20261024; FRED returns undated current columns. Original strict normalization correctly rejects mismatched vintage, but this semantic failure lacked the existing official FRED failover. New focused regression observedRED; fix acquires and validates fresh FRED bytes after rejected ALFRED parsing, preserving rejected URL/hash/reason separately and leaving historical availability unasserted.44macrotests passed1.27s.
Ruling: do not relabel ALFRED's returned24Oct column as25Oct; use current official FRED with actual first receipt instead. This retains strict vintage rejection and costs an extra bounded official request when ALFRED returns another vintage. Next release requires real off-host build evidence, exact-tree CI and actual acceptance.
