# AI Request Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Bound optional reaction work and expose the actual AI request stages.

**Architecture:** One coupled writer owns the optional-capture lifecycle and API diagnostics. A private worker result can commit only before the fixed budget; one outstanding admission prevents surviving timed-out work accumulating. Focused trace helpers correlate middleware, route and actual thread execution.

**Tech Stack:** Python >=3.11, asyncio, threading, SQLite, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-ai-request-latency-design.md`

## Global Constraints

- Fixed optional budget0.25s, unchanged smoke12s/transport14s/provider6s default8s max.
- No dependencies/network/paid calls/live orders/real position mutations or authority promotion.
- Preserve all source bounds/proofs, risk/cost/geometry, OOS/sample floors and zero missing-model votes.
- Only completed private results commit; one outstanding optional worker per engine; preserve all old sources.
- Root owns remotes, broad tests and release; preserve worktrees/evidence and user uv.lock.
- Existing authorization waives repeated design/plan pauses, not review/exact-CI/acceptance gates.

## Review Focus

1. Timed-out running worker finishes after identity freeze: no snapshot/journal mutation.
2. Queued task cancellation: admission frees without allowing simultaneous surviving workers.
3. Shared SQLite contention: no connection-wide timeout changes or interrupts.
4. Middleware short-circuit/direct endpoint calls: correlated actual logs and unchanged503/429 contracts.
5. Malformed or oversized data/logging failures: bounded refusal and no leaked content or changed responses.

### Task 1: Bound capture and trace the AI request

**Files:**
- Modify `seiltanzer/edge_family_event_reaction.py`, `seiltanzer/macro_fomc_deterministic_bootstrap.py`, `seiltanzer/app.py`, `seiltanzer/ai_snapshot_materializer.py`.
- Final-review integration fix also modifies `seiltanzer/ai_snapshot_runtime_guard.py`: establish trace before its synchronous journal probe, share inward and finalize once.
- Create focused `seiltanzer/ai_request_trace.py` and `tests/test_ai_request_latency.py`.
- Extend `tests/test_edge_family_event_reaction.py` and relevant existing API/materializer fixtures.
- Update scoped roadmap and report, preserving previous acceptance facts.

**Interfaces:**
- Preserve `attach_observed_event_reaction(engine, snapshot: dict) -> None` synchronous producer contract.
- Add `async attach_observed_event_reaction_bounded(engine, snapshot: dict) -> None` for the API, fixed constant `REACTION_CAPTURE_BUDGET_SEC = 0.25`.
- Extend `FOMCDeterministicReleaseStore.latest_received(self, captured_ts: float, *, nonblocking: bool = False) -> dict` with whole-read nonblocking admission; synchronous attachment opts in.
- Trace helper owns server request ID, monotonic start, safe stage span, actual-thread submission/start/end; materializer and route share trace through request state. Trace owns no financial data.

- [ ] Step 1: Write deterministic RED regressions. Held real store lock must return unavailable `REACTION_RELEASE_STORE_BUSY`; held feed lock returns `REACTION_FEED_BUSY`; oversized bars cannot trigger deepcopy. Executor saturation and a blocked running capture return budget audit within a generous scheduling tolerance (under1s), preserve prior sources, discard late writes and refuse overlap as `REACTION_CAPTURE_IN_PROGRESS`. Cancel before/after worker start and confirm admission can recover after actual completion. Freeze canonical review bytes before releasing a late worker and assert equality afterward. Use events and isolated DBs, no real engines/accounts/network/provider calls.
- [ ] Step 2: Run RED with `../trading_quotes/.venv/bin/python -m pytest tests/test_ai_request_latency.py tests/test_edge_family_event_reaction.py -q`; record actual failures before product edits.
- [ ] Step 3: Implement private-result budget/single-outstanding lifecycle, whole-read store and feed try-locks and pre-copy bound. Preserve old received/admissible paths; no global SQLite setting changes. Preserve exact proof/value output on a fast valid capture.
- [ ] Step 4: Add trace RED tests: warning logger emits safe structured events; materializer preflight and route use same ID, actual worker-start occurs after submission, early503 includes response status, direct no-argument invocation remains compatible, injected secret-like payload/exception text never logs. A failing trace sink cannot change API success/error. Run RED, then implement minimal diagnostic spans for every spec boundary without changing business logic.
- [ ] Step 5: Run focused GREEN including reaction, materializer, AI API/unified integration, producer/dataset/trainer proof suites; report exact commands/results and test isolation. Retain positive isolated12-action and missing-price503 regressions. Run `git diff --check` and self-review all five Review Focus cases.
- [ ] Step 6: Update roadmap factually, commit product/tests/docs locally, write `.superpowers/sdd/2026-10-05-ai-request-latency/task-1-report.md` with status, commits, RED/GREEN commands/results, covered requirements and remaining production uncertainty. No subagents, remote operations or full broad suite. Return only status/commits/test summary/concerns.

Root subsequently runs fresh task review, fixes/scoped re-review if needed, fresh whole-feature review, one broad run/frontend checks, official exact-tree CI and authorized release/acceptance. Production cause is not established by local lock regressions.
