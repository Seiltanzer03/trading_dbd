# FOMC policy-sentence lexical novelty implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze and independently validate a native received linked FOMC decision-sentence lexical distance before review identity.

**Architecture:** A bounded native pair reader validates original normalized texts. A focused versioned proof module projects exact sentences, recomputes unsigned lexical distance and participates in event adapter/runtime/trainer admission. Both event producers share the existing private 0.25s capture owner and commit rights.

**Tech Stack:** Python, SQLite, hashlib/re/json, existing asyncio capture and pytest; no dependencies.

**Spec:** `docs/superpowers/specs/2026-10-05-fomc-policy-sentence-novelty-design.md`.

## Global Constraints

- Contract `edge-family-fomc-policy-sentence-novelty-v1`; feature exactly `event.fomc.policy_sentence_lexical_distance`; units `DIMENSIONLESS_JACCARD_DISTANCE`.
- Text <=65,536 UTF-8 bytes each; sentence <=768 bytes each; <=128 unique tokens each; extension/root <=4,096 bytes; normalized metadata <=8,000; depth <=8; selected sources <=8,000; <=128 records/family; current publication age <=14,400 seconds.
- One total existing 0.25s capture budget; no acquisition/provider/LLM/training or second capture worker/wait. Preserve old facts, source/risk/cost/model/OOS/sample gates and missing-model zero votes.
- Existing user authority/waived approval pauses apply. One coupled writer; root owns remotes. Preserve all worktrees and original untracked lockfile.

## Review Focus

- Offhost fetch before capture but local import after capture must reject either support.
- Newest invalid or changed old vintage must not fall back or renew publication freshness.
- Frozen proof with plausible aggregate but changed quote/hash/value/namespace must reject.
- Novelty-only success with unavailable price reaction must commit; late worker must never mutate live sources.
- Repeated current-release evidence cannot acquire conflicting provenance or independent authority.

### Task 1: Native bounded text proof through live capture and frozen admission

**Files:**
- Create: `seiltanzer/edge_family_event_novelty.py`, `tests/test_edge_family_event_novelty.py`.
- Modify: `seiltanzer/macro_fomc_deterministic_bootstrap.py`, `seiltanzer/edge_family_event_reaction.py`, `seiltanzer/edge_family_adapters.py`, `seiltanzer/edge_family_source_runtime.py`, `seiltanzer/edge_family_training.py`.
- Modify only if required for frozen retention: `seiltanzer/edge_family_dataset.py`; existing app bounded capture hook remains entry point.
- Test: existing bootstrap, event reaction, capture latency, adapters, source runtime, dataset/training tests; update `docs/UNIFIED_EDGE_DEVELOPMENT_ROADMAP.md` with factual scope/residuals.

**Interfaces:**
- Store `latest_received_text_pair(captured_ts: float, *, nonblocking: bool = False) -> dict` returns AVAILABLE/current/previous or UNAVAILABLE/reason; record schema is pinned by focused module/store tests.
- Module `build_received_event_novelty_source(pair: dict, cutoff: float) -> dict` returns source or None plus rejections.
- Module `event_novelty_features(source: dict, cutoff: float, target_instrument: str) -> dict` returns features/feature_provenance/rejections; absence is empty, any present invalid extension rejects.
- Module `novelty_provenance_reason(feature: str, value: float, meta: dict, captured: float, horizon: float, instrument: str) -> str | None` reconstructs frozen proof and exact expected normalized metadata/value; reserved unknown names/explicit metadata reject.
- Module `attach_observed_event_novelty(engine, snapshot: dict) -> None` appends bounded distinct source and separate audit; existing private bounded capture calls both producers with independent outcomes.

- [ ] **Step 1: Write deterministic failing pair-reader tests.** Pin first fetch/import clocks, exact link, malformed hash/ID/URL, old current publication, invalid latest/no fallback, full-read busy lock and SQL text bounds. Mock ingest clock explicitly; no retroactive production-data claims.
- [ ] **Step 2: Run those tests before implementation and save actual RED output.** `../trading_quotes/.venv/bin/python -m pytest tests/test_edge_family_event_novelty.py -q` must fail due to missing new reader/proof behavior, not missing dependencies.
- [ ] **Step 3: Implement bounded native pair reader and fixed sentence/token/distance projection.** Preserve native historical/received interfaces; follow spec exact selection, bounds, versions and truthful distinct digests. Return precise unavailable reasons.
- [ ] **Step 4: Write and run failing frozen admission regressions.** Known/equal metric, decimal boundaries, ambiguity/oversize, both receipts/materialization clocks, all-or-none extension, renamed/unknown feature, tampered value/quote/digest/normalized metadata, conflicting same-release supports, surprise without consensus and zero votes. Exercise source runtime plus immutable dataset replay without store access.
- [ ] **Step 5: Implement strict module normalization and adapter/runtime/trainer integration.** Validate all explicit extensions before feature insertion. Preserve generic/legacy contracts and exact training floors; compare original identity across reaction/novelty, not unequal projection/local clock schemas.
- [ ] **Step 6: Write and run failing capture regressions.** Novelty-only success without market support, reaction-only success when novelty refuses, preserving prior facts on size refusal, timeout/queued cancellation/running overlap/late result isolation, retained sentence through bounded private input, one deadline/admission owner and truthful separate audits.
- [ ] **Step 7: Integrate both producers in the existing bounded private owner.** Commit sources when either valid outcome changed them, only on timely completion. Preserve synchronous reaction calls and old trace/guard behavior.
- [ ] **Step 8: Run focused GREEN profile and save output.** Include new novelty tests and existing bootstrap/reaction/capture latency/adapters/source runtime/dataset/training tests; select exact existing filenames via `rg --files tests`. No unrelated full suite or frontend work by writer.
- [ ] **Step 9: Update roadmap and commit coupled deliverable.** Record actual test counts, absent real pair/cadence and no predictive efficacy; root later adds exact release acceptance. Commit product/tests/docs, save task report with SHA and RED/GREEN evidence. No remotes.

## Root release steps

Fresh independent task review then Astra whole-feature review; scoped fixes only for findings with rulings persisted. Root one broad Python/frontend profile; known unchanged Python3.12 WAL baseline failure is not hidden or treated as full green. Official required final-tree CI must all pass before ready/merge. Verify exact main/head/synthetic tree, then authorized merge, auto deploy, readiness, functional/public/orchestration and source/math publications for exact resulting SHA. Keep residuals factual.
