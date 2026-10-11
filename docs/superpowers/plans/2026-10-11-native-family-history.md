# Native family history implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; one final independent review.

**Goal:** Feed existing immutable native observations into real family market training, including the actual recorded option IV, and consume the same IV contract in current reviews.

**Architecture:** A bounded read-only indexed SQL export verifies G1S feature hashes against the original passive capture, emits only private relevant inputs, and leaves production tables untouched. The runner merges native observations with the existing Yandex review archive. Option IV is a configured ETF-proxy observation, never a CFD quote or uncalibrated physical probability; actual historical target calibration supplies its market-response forecast.

**Tech Stack:** Existing Python, SQLite, Paramiko, GitHub Actions and existing market trainer.

**Spec:** docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md sections4–8 and user continuation2026-10-11: real historical training now, no invented data or temporary forecasts.

## Global constraints

- Keep existing target identity, chronological validation, common action economics/risk and automatic_execution=false.
- No new broker-fee work, sources, waits for future captures, database writes or public raw private records.
- Reuse accepted baseline82f97bd/tree6c425eb, identical production6ca004da; no repeated unchanged full baseline suite.
- A failed history read remains failed; only actual fitted heads count as trained.
- Retain original observation and original capture-record clocks, source hash and proxy identity; do not replace either with export time.

## Review focus

1. Corrupted or mismatched immutable source hash must reject that row.
2. Demo/unmarked option data or a different configured proxy must reject rather than fit.
3. Source/record clocks after prediction origin must not become historical inputs.
4. Sparse/conflicting histories must preserve missingness rather than fabricate labels.
5. Several actual family models must survive the bounded snapshot together; invalid optional history must preserve existing experts.

## Task1: private native history and shared option feature contract

Files: create seiltanzer/edge_family_native_history.py, scripts/export_native_family_history.py, tests/test_edge_family_native_history.py; modify scripts/export_family_market_history.py and .github/workflows/mathematical-edge.yml.

Interfaces: option_observation(code, metrics, source_id, observed_ts, received_ts, quality, source_sha256) returns actual option.iv feature+provenance or None; native_history(rows) returns private instrument/family matrix; merge_histories(*matrices) retains conflicts for owning trainer rejection. Export uses only approved existing sources.

- [ ] Write RED tests for actual configured IV with exact clocks/units/hash; demo/mismatch/future rejection; immutable SQL hash/eligibility; native plus archive merge without silent overwriting; SQL read-only and bounded outputs.
- [ ] Implement minimal extraction and indexed private SQL export, preserving original passive created_ts and source clocks.
- [ ] Integrate native file into existing private runner history import; print counts only and never publish raw native data.
- [ ] Run owning tests and commit.

## Task2: runtime and verified release

Files: modify seiltanzer/edge_family_market.py, tests/test_edge_family_market.py, docs/UNIFIED_EDGE_DEVELOPMENT_ROADMAP.md.

Interfaces: existing attach_market_family_context reads current in-memory feed.chain only and adds identical option.iv observation to frozen market context; no network, database scan or fit on review.

- [ ] RED regression: genuinely trained option model consumes current configured actual chain; stale/demo/wrong proxy rejected; existing session/intermarket preserved.
- [ ] Add same option_observation function to current snapshot before LLM; retain model/source hashes and Q/proxy roles.
- [ ] Run changed Python/frontend profiles; one whole-branch review; final mandatory CI once.
- [ ] Authorized push/PR/merge; actual exact-SHA deploy, readiness, smoke, public HTTP and family publication. Inspect actual model counts/coefficients and data-read reasons before claiming readiness.
