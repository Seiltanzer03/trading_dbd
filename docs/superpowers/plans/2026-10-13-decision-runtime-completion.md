# Decision runtime completion — Implementation Plan

> Execute inline with superpowers:executing-plans. User explicitly authorized A→B→C→D autonomously; the attached handoff is the reviewed spec and execution brief.

Goal: restore actual manual comparison and independent current LLM participation, connect usable existing forecasts, and ship an exact-SHA verified release.
Architecture: retain the existing materializer, common bank, ensemble and manual execution path. Preserve input clocks through compaction; admission uses the observation boundary, publication uses the final causal cutoff. Existing mandatory risk and live confirmation guards remain.
Tech stack: Python/SQLite, JavaScript, GitHub CI/deploy.
Spec: attached trading_dbd_handoff_2026-10-13 (2).md §§6,17–19; docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md.
Baseline: main26c9e437385cd1d59909e26a892dd58b5cf44297.

## Constraints / rulings
- Existing source timestamps, quote30s and option120s limits, common pricing, CVaR and confirmed BE semantics are unchanged.
- No live orders, position mutations, invented forecasts, new broker-cost subsystem or repeated training without new inputs.
- Newly cloned isolated branch is the working copy; no concurrent writers.
- Input audit compaction currently removes provider timestamp and numeric snapshot_ts. Both must survive all compaction passes. Final captured_ts deliberately includes post-calculation writes; do not backdate it.
- Production currently has no active trade. Live active-position/provider influence cannot be proven by opening a fake trade; release mechanics and saved/isolated behavior are separate evidence.

## A: input clocks and independent LLM
Files: ai_verdict_v18.py, unified_edge_ensemble.py, ai_verdict.py, corresponding behavioral tests.
- [ ] RED: compressed native quote retains provider time/derived and tick cutoff;210s build latency does not reject a quote fresh at input. Stale input/wrong source/future input/mismatched price remain rejected.
- [ ] GREEN: preserve audit clocks/provenance; validate native quote against audit.snapshot_ts (<=captured_ts), with legacy captured_ts fallback only when numeric input clock absent.
- [ ] RED: primary deterministic fallback and primary provider failure cannot suppress a valid independent opinion; programming failures remain visible.
- [ ] GREEN: attempt existing structured provider independently, retain normalized primary failure and genuine provider identity, never invent scores.
- [ ] Run owning clock/geometry/ensemble/provider/API tests. Commit and preserve results here.

## B: existing model continuity and eight families
- [ ] Read publication/model status and current registry once; inspect model hashes, matching and feature freeze/compaction. Reuse actual fitted artifacts rather than rerun search.
- [ ] Reproduce and fix only actual repairable runtime blockers; run owning tests.
- [ ] Produce instrument×family matrix from available genuine receipts, separating source/fitted/runtime/contribution and concrete missing histories.

## C: audit and finite comparison
- [ ] Check existing100% budget, candidate abstention, lineage and frozen ablations; use one saved matched comparison, no new paid calls per scheme.
- [ ] Verify management UI reads structured decisions independently of prose and saved reports. Fix reproduced barriers only.

## D: final verification and release
- [ ] One final reviewer of whole branch, one bounded Important/Critical fix pass.
- [ ] Owning regression and frontend checks; one mandatory final-tree CI; merge only after required green checks.
- [ ] Automatic deploy/readiness/functional/public checks and exact-SHA publication receipts. Report actual installed revision and unsatisfied acceptance limits.

Review focus: compaction preserving negative provenance; input cutoff later than final capture; price/geometry mismatch; real independent provider under prose rejection; absent histories remaining null.

## Progress
Restored current main; bounded production read reports no active trade and last build209981ms. Baseline89tests passed2.31s. Diagnosis: timestamp/snapshot_ts removed by v18 compaction AND preflight compares native clock against late final captured_ts. Existing primary prose fallback unconditionally skips independent provider. A in progress.

A/B/C implementation checkpoint: provider timestamps and numeric input boundary retained in normal/strict compaction. Existing independent structured provider survives primary prose fallback/provider failure; API/UI label prose fallback separately. Original family forecast origin retained; shorter direction heads stay bounded preferences, time/target movement still matches horizon. Actual artifact inventory:25fitted/17supported groups;104cells, all25runtime model hashes/contracts valid. Five family historical inputs remain unresolved, crypto USD/USDT transfer is not promoted.
Final reviewer: two Important findings, no Critical/minors. One fix pass adds bounded current-quote/common-bank repricing before provider (no heavy legacy search), actual native provider-time/source checks, publication recheck of live barriers/price movement/selected parameters, and original forecast expiry separate from source TTL. Ruling: short direction forecasts are current soft stances inside a longer MC economic comparison; they do not become terminal forecasts, and movement time/target actions keep exact horizon. Absolute expiration of merged opinions is preserved; clocks are not retimestamped.
Verification: initial owning138passed, expanded124passed; geometry/provider/API68passed; ensemble/extended/comparison161passed; changed final198passed. Actual final reviewer fixes have separate profile results. Three frontend smokes PASS (archive callback, decision, unified audit). Local full suite on Python3.12/SQLite3.53.1:3898passed/3skipped/7subtests, one unchanged sparse WAL backup test fails because source file signature changes. Isolated same test fails unchanged; CI targets Python3.11/pinnedSQLite refresh and is required before merge. No storage guard is weakened to hide the environmental result.
No active production trade at current read; real current provider influence cannot be asserted from fixtures. Existing history comparison contains32reviews/4paired distinct trades; retrospective immutable snapshots are preserved rather than backfilling missing original provider timestamps.

Final fix profile173passed7.36s and frontend3smokes green. Exact code treeeb61d3c4 uploaded as940d30eb (GitHub connector; direct push lacks credentials). A single new32-review comparison on that code SHA:0rejected,0paidcalls,0productionwrites; docs/UNIFIED_EDGE_COMPARISON_2026-10-13.json contains paired/scenario summaries. The older local full suite loaded the pre-fix expiry implementation and reported that regression plus unchanged SQLite3.53 sparse WAL failure; the expiry regression and final owning profile are now green. Required final-tree CI remains mandatory. No second reviewer/re-training/search was launched.

Release correction: PR461 merged5895c817/treec8ac5f64, but mainCI38371803141 rejected a boundary case:54017bytes versus54000target in test_snapshot_contains_quant_policy_and_all_recent_metric_families (3912others passed). No deploy started. The late-enrichment bridge measured size before inserting its compaction marker and converging final_bytes, then accepted only the60000hard limit. A deterministic near-target regression reproduced54023bytes. Small fix measures final metadata first and tests the unchanged soft target; decision/quote/evidence fields are preserved. New exact-tree CI/release required, no blind retry.
Byte-bound owning profile55passed1.95s; unified audit/archive frontend smokes PASS. Original source clocks and hard60000limit/soft54000target unchanged. This is a reproduced final-CI fix verified by RED→GREEN, not a new package or repeat research.
