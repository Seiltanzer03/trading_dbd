# Final review I1 fix report

Status: DONE (scoped fix; independent scoped re-review and release obligations remain root-owned).

Base: `c669690545fd5d724aa3e4209f8f5743266c1561`.
Fix commit: `6f1b55b37506a4f86f3ff6ef6c4f5e8f5c36399f` — Trace outer AI runtime guard before journal preflight.
Branch: `feat/edge-reaction-capture-latency`. Clean worktree after commit.
Root's supplied spec/plan amendments are included. No capture lifecycle, API route business logic, other worktree, uv.lock, dependencies, remote, subagents or broad suite changes/operations.

## Accepted finding and change

I1 reproduced with the actual materializer and runtime-guard installers in production order and an event-controlled fake journal: while the outer synchronous journal probe was blocked, no trace existed. The no-trade400 path bypassed tracing entirely.

The runtime guard now creates/reuses request.state.ai_request_trace before that probe and emits runtime_guard start/end around it. Materializer reuses the inherited trace instead of creating a second ID. Only the boundary that creates the trace emits final status, so production's outer guard records the eventual materializer race-converted status, rather than an inner intermediate500. Standalone materializer ownership and direct no-argument API route ownership retain their prior behavior. The trace allowlist adds only runtime_guard.

No-active-trade400 JSON is unchanged. Warming503 body/headers and race conversion are unchanged. Non-AI routes bypass the trace and journal probe. Exception/cancellation objects are re-raised, and diagnostic warning sink failures do not alter responses. No new deadline or offloading of the synchronous journal read was introduced.

## Actual TDD and verification

Raw stdout/stderr is saved beside this report.

RED before product edits:

```bash
../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py -k production_guard -q
```

Exit1: **5 failed, 1 passed, 20 deselected, 1 known warning in0.73s**.
Raw output: `final-fix-red.log`.
- Held journal: expected runtime_guard start was absent while request pending.
- Active/warming onward requests and error/cancellation: first stage was preflight instead of runtime_guard.
- Non-AI behavior already passed.

GREEN after minimal fix, same command:
Exit0: **6 passed, 20 deselected, 1 known warning in0.59s**.
Raw output: `final-fix-focused-green.log`.

Added explicit characterization coverage for actual materializer500→503 race conversion, failing warning sinks preserving exact early400/503 responses, and the actual API route reusing the outer ID and publishing once. Final prescribed focused scope:

```bash
../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py tests/test_ai_snapshot_runtime_guard.py tests/test_ai_snapshot_materializer.py tests/test_ai_verdict_api.py tests/test_ai_management_api.py tests/test_ai_authoritative_price_preflight.py -q
```

Exit0: **95 passed, 1 known warning in53.34s**.
Raw output: `final-fix-green.log`.
Existing warning: installed Starlette TestClient httpx deprecation. No other warnings/failures; no dependency change.

`git diff --check` exit0 before commit.

## Self-review

- Real installer order, fake isolated journal, held threading events: runtime_guard start is emitted before active_trade enters its event wait. After release the exact old400 JSON returns and one response event completes the same ID.
- Active request: guard, materializer preflight, actual API route and publication share one server-generated ID. Provider test replacement invoked once. Exactly one final200 event.
- Warming and race-backstop paths: unchanged503 body; outer status matches final converted503, exactly once. Standalone materializer/direct route tests remain green.
- Cancellation/errors: direct execution of actual installed dispatch functions verifies original exception identity is re-raised; one owner records500 or existing cancellation diagnostic499. Cancellation499 is logging only, not a new response or swallowed cancellation.
- Warning logger failure: injected RuntimeError is isolated; exact early400/503 bodies survive. Secret-like error/cancellation text is absent from trace logs. Stage addition contains no request/source/financial payload.
- Non-AI request: unchanged response, no journal probe, no trace. Heavy-build backoff/status behavior remains covered by existing runtime-guard tests.
- Capture semantics and financial/risk/cost/proof/provider/acceptance limits are untouched by this fix.

## Isolation and remaining limits

Held-journal/early-response/error fixtures use fake engines/journals, no app lifespan, accounts, providers, DB or network. The actual API test uses the existing isolated tmp_path demo DB and a pure local provider replacement. No paid calls/orders/real-position changes or source authority were introduced.

The synchronous journal probe can still stall; it is now visible from a warning-level start event and timed from the owning outer request trace. This local finding does not establish PR397 timeout causality or deployed logging/latency acceptance. Root owns fresh scoped re-review, broad/frontend verification, official exact-tree CI and authorized same-SHA production/source/math acceptance. Pre-existing other executor lifecycle redesign remains outside this narrow fix.
