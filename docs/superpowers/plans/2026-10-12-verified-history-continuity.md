# Verified price history continuity implementation plan

> **For agentic workers:** Execute inline with one root writer and one final read-only reviewer. The user requests continuing the next global task while prior CI/audit runs.

**Goal:** Use already retained verified Yahoo historical bars now, rather than discard older same-series history on refresh.
**Architecture:** Extend the existing read-only export across at most8 immutable snapshots with identical provider/ticker/interval/semantics, then preserve same configured Yahoo history when the runner refreshes. Verify original bytes before normalization; overlapping OHLC conflicts reject the older snapshot. No price interpolation, new provider, crypto quote reinterpretation or prospective-receipt claim.
**Tech Stack:** Existing Python stdlib/SQLite exporter, bounded runner source collector and existing family trainer.
**Spec:** docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md §§6–8 and docs/LOCKED_GLOBAL_PLAN_2026-10-07.md; user authorizes autonomous efficient historical training now.

## Global constraints

- Base441d3f7/tree d2f7ecc4 is macro PR451 tested locally135pass; its remote headab7d03af remains frozen during CI. One isolated root writer; no concurrent product edits.
- Existing25s indexed SQL budget,8MB decompressed snapshot bound,20,000 final bars and64MB transport bound remain. At most8 candidate snapshots/instrument, retention120days matches the existing official archive window.
- Only genuine hashed existing bars; preserve gaps, original source identities/receipts, exact target ticker and current fresh-source clocks. An old receipt is never backdated or reissued as a fresh provider observation.
- Crypto retained source rules and configured Binance/USDT admission remain unchanged. No new feeds, cloud resources, broker-cost work, fitting on VPS or live orders.
- Existing model validation, purging, finite search and working admission stay unchanged. More rows do not guarantee a positive macro edge.

## Review focus

- Different provider/ticker/interval or source semantics cannot be spliced.
- Rehashed malformed, duplicate, nonfinite, partial or future rows cannot enter accumulated history.
- Overlap revisions must be explicit and exclude the conflicting older snapshot, preserving current series.
- Repeated snapshots/cache must deduplicate and enforce final120day/20,000bar limits without changing current source clocks.
- Budget exhaustion or bad optional older rows must preserve the verified latest snapshot and disclose incomplete history.

## Task1: preserve verified runner history on Yahoo refresh

Files: seiltanzer/mathematical_edge_sources.py, tests/test_mathematical_edge_sources.py.
Interface: retain_yahoo_history(current, archives, captured) -> source. Current fresh Yahoo bars are authoritative for this bounded operation; verified cached/exported same-series bars extend only older history. Parent source IDs/hashes/actual clocks are retained under history_retention.
- [x] RED: existing older seed/cache survives a fresh short series; mismatched identity/hash/future/overlap rejects; bounded retention and clocks unchanged.
- [x] Implement fixed120day same-provider OHLC retention, no gaps filled; hook only existing noncrypto Yahoo branch.
- [x] Run owning tests, ledger actual result and commit.

## Task2: read already-existing immutable historical snapshots

Files: scripts/export_mathematical_edge_sources.py, tests/test_mathematical_edge_sources.py.
Interface: same REMOTE_EXPORT JSON shape plus historical_snapshot_history with accepted/excluded source IDs/hashes/counts and read-only lineage. Verify original compressed-byte digest before canonical OHLC comparison; latest baseline remains intact on bad/discordant older input.
- [x] RED: two immutable matching snapshots extend history without DB changes; conflicting/other-identity/corrupt snapshot does not enter; limit8 and120days/20,000 rows.
- [x] Indexed bounded newest-first reads and explicit rejection/incomplete summary; all current retained-provider separation preserved.
- [x] Run owning profile; commit source/workflow/roadmap records.

## Task3: review and exact-revision release

Files: .github/workflows/mathematical-edge.yml, roadmap and this plan.
- [x] Owning workflow includes source/retention tests and records actual fresh source count/coverage without hiding failed refresh or conflicting snapshots.
- [ ] One final independent reviewer; one concrete fix pass if needed; full mandatory CI and real trusted-PR fit on the final tree.
- [ ] Merge after required green checks; exact-SHA deploy/functional/publication receipts and actual model counts. Do not wait for previous native EDE audit to start this work.

Implementation checkpoint:45source tests, integrated owning201passed10.74s. The optional multi-snapshot portion stops after8seconds within the existing25second total SQL budget; remaining instruments keep their latest baseline with incomplete-history diagnostics. Old uncompleted bars are excluded only after verifying original immutable source bytes, retaining original identity. Actual production source extension/model effect remains to be measured.
