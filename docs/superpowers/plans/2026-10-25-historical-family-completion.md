# Historical family completion implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execute inline under the user's standing authorization.

**Goal:** Fit usable models now from existing historical observations and official public archives, preserving actual source identity and honest model admission.

**Architecture:** Collect exact configured Binance historical bars from checksum-verified official public archives. Preserve existing Coinbase intermarket drivers separately. Export already recorded Treasury factors with their actual clocks and shared lineage, then fit using the existing frozen/purged trainer. Complete the remaining event, flow and positioning paths only from genuine supported inputs; missing inputs remain explicit.

**Tech Stack:** Python 3.11, httpx, stdlib CSV/ZIP/SHA256, existing NumPy model trainer and GitHub Actions.

**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`; user instruction 2026-10-25 explicitly reopens historical model training.

## Global constraints

- All 13 configured instruments and eight existing families remain in scope.
- No synthetic prices, receipts, consensus, option history, venue mappings or profit.
- No new broker-cost subsystem, live orders or weakened held-out/G.1 gates.
- Existing official archive is a separate documented public product; no retries/rerouting after denial.
- Heavy fitting stays off-host; runtime consumes frozen models.
- Preserve unrelated dirty research archive part and avoid another full database transfer.
- Standing authorization covers PR, green mandatory CI, merge and exact-SHA deployment checks.

## Review focus

- Microsecond versus millisecond public timestamps; reject wrong period, filename, checksum, duplicate/revised overlapping rows.
- Later archive receipt must never be presented as an original historical receipt.
- Exact Binance target history must not remove the existing three Coinbase intermarket drivers.
- Recorded Treasury factors must preserve point-in-time clocks and source dependency; no broker-swap interpretation.
- Incomplete capture export or insufficient samples must not become a completed model claim.

### Task 1: Exact public crypto archive

**Files:** create `seiltanzer/binance_public_archive.py`, `tests/test_binance_public_archive.py`; modify `seiltanzer/mathematical_edge_sources.py`, `scripts/run_mathematical_edge.py` and their tests.

**Interfaces:** `fetch_public_archive(code, captured, client, budget, *, days=60) -> dict` returns the existing canonical exact Binance source. Collection retains typed Coinbase diagnostics for `related_series` without splicing providers.

- [x] Write failing parser/collector tests for checksum, ZIP identity/size, time units, real completed rows, bounded requests, denial and receipt semantics.
- [x] Watch the tests fail; implement the bounded archive parser and collector.
- [x] Test collector integration and preservation of existing intermarket drivers before modifying those paths.
- [x] Run owning tests and commit.

### Task 2: Recorded Treasury factor history

**Files:** new focused Treasury factor helper and tests; modify `native_family_capture_projection.py`, `edge_family_native_history.py`, adapter/runtime hook and owning tests.

**Interfaces:** one shared helper consumes the original `treasury_context_v1` and produces validated `carry.*` interest-rate features with original source clocks and one shared Treasury dependency group. Native and runtime paths call the same helper.

- [x] Write failing tests for frozen projection retention, actual receipt clocks, stale/future/simulated/research authority and rate units.
- [x] Implement deliberate market-response feature derivation; preserve original context-only role and no broker carry/quote authority.
- [x] Verify complete native-to-fit and frozen-runtime contracts, then commit.

### Task 3: Finite remaining family completion

**Files:** existing native export/history, official macro event and flow/positioning modules plus owning tests; add focused modules only where a distinct source contract requires them.

**Interfaces:** use existing `family_history[code][family]` and `datasets_override` paths with explicit public-archive versus captured-receipt semantics. Do not forge provenance to satisfy the generic receipt validator.

- [x] Remove measured export incompleteness through independently bounded guarded batches or verified off-host history reuse.
- [x] Reuse validated official historical releases for event features without invented consensus.
- [x] Fit only genuine trade-flow/observed-interest inputs with an identical available runtime feature path; retain explicit unavailability where no such input exists.
- [ ] Record actual trained/supported counts by family/instrument after real off-host fitting.

### Task 4: Review, release and factual acceptance

- [ ] Run owning tests, one fresh whole-branch review, then mandatory full CI on final revision.
- [ ] Merge only green revision under standing authority; check deploy, readiness and exact-SHA model delivery.
- [ ] Update roadmap with source → sample → fitted model → runtime forecast → contribution, including remaining missing inputs and unsupported results.

## Execution rulings

- User explicitly requested autonomous continuation: implement inline without another plan-approval pause.
- Use the existing isolated worktree and installed venv; accepted base tree already has green CI. Preserve its unrelated dirty file.
- Historical training is reopened; existing source integrity and admission thresholds remain binding.

## Verified implementation checkpoint

318 owning checks passed (15.56s). One independent review found three Important defects; all received reproduced RED→GREEN regressions: realistic flow depth, frozen-runtime flow transport and native/archive selection independent of held-out labels (both event and macro). No deferred minors. Native capture sampling adapts from15-minute UTC buckets to at most1500 actual observations per instrument, with original hashes/clocks and explicit partial/cap status.

Full local suite encountered a native Python3.12 curl_cffi import Bus error in the existing SOCKS transport test; no full-suite success is claimed. Mandatory Python3.11 CI is still required before merge. Actual historical fitting and exact-SHA runtime delivery remain acceptance steps.
