# Observed Event Reaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Freeze and measure already observed postpublication price reaction, independently of consensus.
**Architecture:** One coupled optional bounded proof across prospective stored-release/feed producer, snapshot, adapter, capture-time runtime and trainer admission. Legacy surprise stays strict; no historical enrichment.
**Tech Stack:** Existing Python/pytest/SQLite; no new dependency or network call.
**Spec:** `docs/superpowers/specs/2026-10-05-observed-event-reaction-design.md`.

## Global Constraints

- Contract `edge-family-event-reaction-v1`; projection≤4096 UTF-8 compact JSON bytes, depth≤8; one series,2–6 consecutive completed minute samples.
- Unchanged bundle≤1,000,000 bytes and selected facts≤8,000 bytes. No GET, LLM/extraction, archive rewrite, synthetic data or receipt backdating.
- start=ceil(published_at/60)*60; baseline is first fully postpublication bar close. Return intervals60/300sec; window_seconds=supporting endpoint-publication, always positive.
- Only exact configured direct Binance crypto/Yahoo EURUSD=X/CAD=X context; preserve actual quote/symbol/authority. No broker equivalence, offset/proxy or Coinbase substitution.
- Source IDs/hashes/clocks/applicability/recomputed values bind through capture, review, frozen dataset and trainer. All same-release transforms share one release dependency group.
- Legacy surprise still requires matching strictly prepublication consensus; no risk/cost/geometry/OOS/purge/sample-floor changes.

## Review Focus

1. Actual stored fetch or feed batch receipt after snapshot but before later review must reject.
2. Global release admission cannot bypass configured target price identity/basis/quote gates.
3. Recognized reaction feature without proof must not bypass trainer consensus via a name or flag.
4. First close is not instantaneous release price; incomplete/gapped windows cannot produce 5m return.
5. New prospective snapshot attachment must preserve existing sources, frozen bytes and source caps without request-path network work.

## Task 1: Coupled observed-reaction producer and admission

**Files:** Create `seiltanzer/edge_family_event_reaction.py` (split small engine producer if needed), `tests/test_edge_family_event_reaction.py`; modify `macro_fomc_deterministic_bootstrap.py` separate actual-receipt reader, `app.py` snapshot attachment, `edge_family_adapters.py`, `edge_family_source_runtime.py`, `edge_family_training.py`, affected macro/app/runtime/dataset/training/pipeline tests. Update spec/plan actual-details and roadmap with accepted PR396 evidence/residuals. No unrelated feed/archive schema change.
**Interfaces:** Pure `event_reaction_features(source:dict,cutoff:float,target_instrument:str)->dict`; value-aware `reaction_provenance_reason(feature:str,value:float,meta:dict,captured:float,horizon:float,instrument:str)->str|None`. Results features/feature_provenance/rejections. Producer consumes existing received store/feed only and freezes source packet before snapshot identity.

Producer signatures: `reaction_observation_sha256(series:dict)->str`,
`build_received_event_reaction_source(release:dict,feed,cutoff:float,instrument:str)->dict`
returns source/rejections; `attach_observed_event_reaction(engine,snapshot:dict)->None`.
Store: `FOMCDeterministicReleaseStore.latest_received(captured_ts:float)->dict`.

- [x] Add deterministic producer/admission tests before product: publication1000 gives first start1020/end1080; six closes `[100,101,102,103,104,105]` yield log(101)-log(100),log(105)-log(100),delay20 and windows140/380/80sec. Two samples have1m/delay but no5m. Original source unchanged.
- [x] Run `../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_event_reaction.py`; preserve actual RED log before implementing.
- [x] Implement closed bounded proof/hash projection and exact configured identity/clock/applicability validation. Test wrong provider/USDT relabel/global bypass, partial/unknown/null/deep/oversized, future/straddling/gapped/duplicate/nonfinite/bool/zero and child flags/horizons.
- [x] Add integration REDs: actual stored fetched_at vs reconstructed available_at; locked already-fetched feed with no refresh; append/no replacement before snapshot identity; bundle capture vs review; real compact source under8k reaches adapter/dataset; future path changes leave features/provenance unchanged.
- [x] Wire separate reaction and surprise and trainer value-aware proof admission. Add actual producer→dataset→trainer valid path and recomputed-checksum tampered value/support/hash/clocks/scope/window refusals; legacy surprise with/without correct consensus remains unchanged.
- [x] Run changed-scope reaction/adapters/source-runtime/dataset/training/pipeline plus changed macro/app tests; record exact command/count/log, self-review and scoped commit/report. No broad worker run; root owns broad/official CI.
- [ ] Fresh task review, fix covered findings with scoped RED/GREEN/re-review; then fresh most-capable whole-feature review and one final fix wave if required.
- [ ] Root broad Python/frontend, mandatory full official exact-tree CI/WebKit, authorized draft PR/ready/merge only after green; exact main deploy/readiness/functional/public/source/math acceptance and residual evidence.

Execution authority and single-writer/review separation follow existing user record.
Preserve all worktrees and evidence. Review package uses recorded task BASE, not HEAD~1.

Actual details: one coupled writer, no remote or broad-worker run. Initial pure
RED42→GREEN42; integration RED18/50 plus boundary RED2→GREEN70. Hardening
confirmed RED3/69→GREEN72, then bounded-string RED2/72→GREEN74. Combined selected
facts stay within the unchanged 8KB cap or old facts are preserved with explicit
new-attachment refusal. The final scoped report records exact commands and counts;
the remaining unchecked review/release steps belong to root.
Final changed-scope proof: 692 passed, one existing Starlette/httpx deprecation
warning, in 61.81 seconds. No broad-suite or release completion is claimed.
