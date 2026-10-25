# Global recorded history and event windows implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline, with one final independent review.

**Goal:** Close remaining recoverable history gaps using observations already recorded and primary public archives, without waiting for new samples.

**Architecture:** A recorded U.S. Treasury curve is a global factor, so reuse its original receipt across all configured conditional targets. Fetch exact Binance historical target bars only around all validated official release times, preserving a separate calendar-selected event dataset. The ordinary recent-price model continues to use its existing target history.

**Tech Stack:** Existing Python/NumPy frozen family trainer, checksum-verified Binance archive parser and immutable official macro archive.

**Spec:** Original unified edge specification and user's instruction to finish historical training now. Prior package PR523, main2f6578f39e2433ace8ea71550023a537508f1a43, tree24db7bc292e7cadcb9281899ef8054f2f2a0db04;65fitted/32historically supported in its actual PR math run.

## Global constraints

- No fake original receipts, consensus, broker carry costs, source venue or successful forecast.
- Keep existing180-observation minimum,4h event freshness,1d rate-factor freshness and held-out admission unchanged.
- Preserve original capture instrument/hash/receipt; global rate reuse asserts no target quote equivalence.
- Event-window selection uses every validated publication time, never price outcomes or final-test scores.
- Bounded HTTP files/bytes/deadline, stop denial without rerouting, no raw DB transfer.
- Runtime uses the same observed factor definitions and frozen models.

## Review focus

- Conflicting curve values under the same published/fetched clocks must not silently win.
- Cross-instrument global factors must retain original availability and shared dependency groups.
- Sparse event targets must not alter the unconditional recent-price dataset.
- Current archive retrieval must not become a historical first receipt.
- Missing pages or labels must leave explicit insufficiency, without thresholds lowered.

### Task 1: Shared original Treasury history

**Files:** `edge_family_native_history.py`, native/treasury tests, native export audit.
**Interfaces:** `native_history(rows, captured_ts=...)` retains its instrument/family matrix; global facts retain original capture lineage and are attributed to each model target explicitly.

- [x] Test RED: a valid curve captured under NAS100 supplies identical clocks/values to BTCUSD and JPY100; duplicates collapse and conflicting same-clock curves are excluded.
- [x] Implement earliest actual capture per unambiguous global curve; preserve other family histories and run owning tests.
- [ ] Verify actual off-host fit counts after final integration.

### Task 2: Older event target windows

**Files:** dedicated archive event-window collector and tests, official archive release-time accessor, source collector, runner wiring and family event fitter/provenance.
**Interfaces:** event source sidecar contains its own exact Binance bars/hash/file receipts and explicit calendar sampling policy; only the event fitter consumes it.

- [x] Test RED: all validated release windows are selected, including older months, independent of outcomes; checksums/clocks/row/byte bounds remain enforced.
- [x] Use the existing public archive parser and deadline; keep recent target bars independent and retain actual retrieval metadata.
- [x] Test historical fit and frozen runtime contracts; preserve missing-history reasons.

### Task 3: Final verification and delivery

- [ ] One independent review, owning tests and mandatory final CI; authorized merge/deploy/exact-SHA model publication.
- [ ] Report actual trained/supported matrix and remaining source gaps. All models working is an empirical acceptance condition, not a test fixture.

## Release acceptance correction

The actual65-model publication is619,593bytes; the legacy500k runtime cap rejects it. A read-only exact-SHA HTTP observer confirmed CURRENT_WORKING_MODEL_UNAVAILABLE despite successful delivery. This package also aligns producer/consumer at a finite2MB loader bound, reads at most MAX+1 bytes, reserves4096bytes for publication metadata and rejects oversized output before replacing the prior artifact. Offline loading and model validation of all65 actual fitted models passed15.72ms; empirical gates are unchanged.

One independent review found one same-host denial retry issue;403/451/429 regressions were reproduced and fixed.907owning tests passed22.02s. Full mandatory CI and actual new fit/delivery remain pending.
