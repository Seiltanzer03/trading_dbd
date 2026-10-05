# Task 1 report — bounded capture and request trace

Status: DONE (scoped implementation; production cause/acceptance remains open).

Base: `d3998c9`.
Commit: `ae3ce68107f498772512bf3b1489c9020c57944c` — Bound optional event reaction capture and trace AI request stages.
Branch: `feat/edge-reaction-capture-latency`.
Sole product writer; no subagents, remote operations, other worktree edits or uv.lock changes. Root owns the ledger, independent reviews, broad verification and release. Worktree clean after commit.

## Actual RED/GREEN evidence

1. Capture RED, before any product edits:
   `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py tests/test_edge_family_event_reaction.py -q`
   → exit1, **8 failed, 82 passed in1.85s**.
   Held real store lacked the nonblocking keyword; held feed waited and returned a source; oversized bars invoked deepcopy; bounded lifecycle/fast API missing in five cases.
   Saved actual-result summary: `capture-red.log` (summary from the original tool output, not a claimed full stdout capture).
2. Initial capture GREEN, same command → exit0, **90 passed in1.70s**.
3. Trace RED, before trace module/API instrumentation:
   `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py -q`
   → exit1, **5 failed, 8 passed, 1 known warning in1.52s**; all five failed because request trace was missing.
   Saved actual-result summary: `trace-red.log`.
4. Coupled initial GREEN:
   `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py tests/test_edge_family_event_reaction.py tests/test_ai_snapshot_materializer.py tests/test_ai_verdict_api.py -q`
   → exit0, **112 passed, 1 known warning in6.77s**. Summary: `focused-green.log`.
5. Self-review malformed/oversized projection RED:
   `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py -k private_projection -q`
   → pytest exit1, **4 failed, 13 deselected in0.86s**. Oversized/malformed/deep input tried copying; malformed strategy raised AttributeError. Full stdout: `projection-red.log`.
   Fixed with a depth/byte bounded primitive JSON copy before submission, using existing `MAX_SELECTED_BYTES=8000`, and explicit optional refusal; no reliance on the production loader alone.
6. Projection/capture GREEN:
   `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py tests/test_edge_family_event_reaction.py -q`
   → pytest exit0, **99 passed, 1 known warning in3.16s**. Full stdout: `capture-projection-green.log`.
7. Final latency isolation tests:
   `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py -q`
   → pytest exit0, **20 passed, 1 known warning in2.93s**. Full stdout: `latency-green.log`.
8. Final focused verification (no broad suite):

```bash
../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py tests/test_edge_family_event_reaction.py tests/test_edge_family_source_runtime.py tests/test_edge_family_adapters.py tests/test_edge_family_history.py tests/test_edge_family_dataset.py tests/test_edge_family_training.py tests/test_macro_fomc_deterministic_bootstrap.py tests/test_macro_fomc_extraction_refinement.py tests/test_ai_snapshot_materializer.py tests/test_ai_verdict_api.py tests/test_ai_management_api.py tests/test_ai_authoritative_price_preflight.py tests/test_ai_verdict_late_enrichment_budget.py tests/test_ai_provider_guard.py tests/test_unified_edge_ensemble.py tests/test_unified_edge_runtime_context.py tests/test_unified_candidate_economics.py tests/test_unified_edge_audit.py tests/test_unified_edge_comparison.py tests/test_active_management.py tests/test_active_edge_ai_integration.py -q
```

→ pytest exit0, **781 passed, 1 known warning in69.63s**. Full stdout: `final-focused-green.log`.
The warning is installed Starlette's httpx TestClient deprecation; no dependency changes or other warnings/failures. Positive isolated 12-action and missing-authoritative-price503 regressions are included.

`../trading_quotes/.venv/bin/python -m compileall -q seiltanzer/ai_request_trace.py seiltanzer/edge_family_event_reaction.py seiltanzer/macro_fomc_deterministic_bootstrap.py seiltanzer/app.py seiltanzer/ai_snapshot_materializer.py` → exit0.
`git diff --check` → exit0, no whitespace errors.

## Requirements and isolation

- Fixed0.25s budget includes default-executor queue time; no dedicated executor or new dependencies. Only captured timestamp, instrument/strategy instrument and bounded copied existing family sources reach optional workers.
- Only timely completed results commit source/audit fields. Unavailable results retain original prior sources. Timeouts emit `REACTION_CAPTURE_BUDGET_EXCEEDED`; overlapping surviving work emits `REACTION_CAPTURE_IN_PROGRESS`; both keep contract, network_calls=False and configured-context/non-broker role.
- Engine-local lock plus lifecycle handoff releases unstarted cancelled admission; abandoned queued work cannot enter capture after admission was released. Started work retains admission until real worker finally, even if caller cancels/times out. Unexpected worker errors retain their original exception and free admission.
- Store try-lock covers table discovery, query and decode. Default received/admissible contracts unchanged. Real shared SQLite exclusive-lock contention test covers a read that stalls despite acquiring the in-process lock; shared busy_timeout remains unchanged, no connection interrupt.
- Feed try-lock returns `REACTION_FEED_BUSY`; bar list/tuple and4096 checks precede deepcopy while authority/bars/feed identity are captured under the same lock. Exact proof/value output matches synchronous fast attachment.
- Focused warning-level logger emits only server request ID, allowlisted stage/event, monotonic elapsed/duration, final status and exception class. Old route exception traceback logging was replaced by sanitized trace errors to prevent exception-text leakage. No prompt/body/header/cookie/source/price/position/account content is passed to logs.
- Materializer request.state and route share ID. Preflight short-circuit503 and race-backstop status are observed. Direct no-argument route calls work. Diagnostic handler failures leave API success/error unchanged; spans re-raise application errors and cancellation.
- Stages cover preflight, cached snapshot, position finalization, family bundle, runtime/macro context, bounded reaction, regime/management, review identity, provider, ensemble, publication, route and final response. Actual worker submission/start/end expose queue wait separately from execution.
- Tests use events, in-memory or tmp_path SQLite, fake capture engines and isolated demo API fixtures. Providers are pure local replacements; no accounts/network/paid calls/live orders/real-position mutations. No production source authority is fabricated.
- Risk/cost/geometry/position validation, immutable proofs, producer→dataset→trainer validation, provider guard6s/8s, smoke12s/transport14s, OOS/sample floors and zero missing-model votes were not changed.

## Self-review against all five Review Focus cases

1. **Late worker after identity freeze:** worker sees no live snapshot or journal; the canonical review object and serialized snapshot remain exactly equal after late completion. Both timed-out and cancelled-running cases tested.
2. **Queued cancellation:** cancellation/timeout abandons under a lifecycle lock and cancels the executor future; a racing old stub skips work. The running branch alone owns actual-completion release. Admission recovery tested before/after actual start.
3. **Shared SQLite contention:** lock wrapper keeps shared RLock and connection behavior; no SQLite timeout/interrupt change. Actual exclusive DB lock demonstrates bounded caller while underlying work survives and rejects overlap.
4. **Preflight/direct calls:** one request.state trace survives middleware→route, short-circuit is logged503, API IDs correlate with logs, and direct no-argument endpoint invocation returns existing success/error contracts. Existing rate-limit/price/warming tests pass.
5. **Malformed/oversized/logging failure:** existing8KB cap bounds private primitive copy before submission; depth/string/unsupported-object refusals preserve prior facts and recover admission. Oversized bars cannot call deepcopy. Exception text/body/headers injected in tests never appear; failed warning sinks cannot change success/error.

## Limitations / handoff

Production POST timeout causality remains unestablished. PR397's saved deployment log lacked actual stage evidence; these deterministic local lock/queue defects do not identify its cause. Python cannot forcibly terminate running threads: a timed-out optional read may continue privately and holds its single admission until completion. Subsequent captures explicitly refuse, preventing surviving-job accumulation. No production acceptance claimed.

Root must obtain fresh task review, fresh whole-feature review, broad/frontend verification and full official exact-tree CI, then authorized release and same-SHA production/source/math acceptance. If timeout persists, preserve and diagnose the new actual request stage trace; no blind deploy rerun.
