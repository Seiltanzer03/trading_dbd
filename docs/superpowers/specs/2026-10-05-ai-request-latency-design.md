# Bounded optional reaction capture and AI request diagnostics

## Evidence and objective

PR397 main `4e0a5f58694bc657d7d5cb485909a6492ba17e43`, tree
`94e79bfa5002031d825d2fdaef22eb46ff8c0b8e`, passed mandatory CI but deploy
`37269012264` failed waiting for `/api/ai/verdict` HTTP response. Readiness and
isolated 12-action checks passed; public/source/math checks were skipped.
The transport limit is 14 seconds and acceptance latency remains 12 seconds.
The saved log contains no actual request stage trace. A prior background build
took 101998.4ms; the request uses cache-only materialization. Neither that build
nor the later backup recovery establishes the cause of this POST timeout.

Independent code diagnosis found optional reaction capture can wait without a
request budget for the shared executor, passive/store lock or feed lock, and
copies an oversized bar collection before checking its bound. Reproduce these
local defects before fixing them; do not claim they caused the production event.
Add sanitized stage diagnostics so the next exact-SHA acceptance identifies the
actual stalled request boundary if the timeout persists.

## Contract

- Python >=3.11; no new dependencies, network requests, paid provider calls,
  live orders, real position mutations, or new production authority.
- Optional reaction capture has a fixed 0.25 second asynchronous budget including
  default-executor queue time. It operates on a private input projection containing
  only capture time, instrument/strategy and copied existing family sources.
  It never passes the live review snapshot to a worker.
- Only a completed worker result can replace the two owned fields:
  `edge_family_sources` and `edge_family_event_reaction_audit`. A timed-out or
  cancelled worker cannot change the live snapshot, review identity or journal
  later. Existing source facts must survive every refusal and timeout.
- Timeout emits an unavailable audit with reason
  `REACTION_CAPTURE_BUDGET_EXCEEDED`, `network_calls=False`, the existing contract
  and authority role. Do not convert optional absence into source evidence.
- The received-release read acquires the passive/store RLock nonblocking for the
  whole read. Busy returns `REACTION_RELEASE_STORE_BUSY`. Preserve existing
  `latest_received(captured_ts)` callers by adding keyword-only
  `nonblocking: bool = False`; reaction capture opts into it. Historical/admissible
  reads retain their old behavior. Do not change the shared SQLite busy timeout
  or interrupt the shared connection. The asynchronous budget also covers a
  SQLite read that stalls despite acquiring the in-process lock.
- The feed producer acquires `_intraday_lock` nonblocking, returning
  `REACTION_FEED_BUSY` when occupied. Check the list/tuple and 4096-bar bound
  before copying. Preserve an atomic authority/bar/configured-identity view and
  all existing proof, clock, applicability, byte and source identity checks.
- Use no more than one outstanding optional capture per application/engine.
  A worker surviving timeout must not accumulate new jobs on later requests;
  those requests explicitly report `REACTION_CAPTURE_IN_PROGRESS` until it ends.
  Engine-local admission must release on all completion/error/cancellation paths,
  and a cancelled queued job must not strand admission. No dedicated executor.
- Sanitized monotonic stage logs must actually be emitted under production's
  warning-level logging. Use a focused logger and bounded structured events,
  not changing global log levels or logging prompt/body/headers/cookies,
  credentials, source payloads, prices, position/account data or exception text.
- One server-generated request ID links materializer preflight and API route.
  Trace ownership must start before the outer production runtime guard's
  synchronous journal/current-trade probe, not only inside materializer.
  Instrument this as `runtime_guard`; share the same trace inward and emit
  exactly one final actual HTTP status, including no-trade400 and warming503.
  Preserve direct no-argument endpoint invocation used by existing tests. Trace
  preflight entry/exit, cached snapshot, position finalization, family bundle,
  runtime context, macro context, reaction capture, regime/management selection,
  review identity, provider, ensemble, publication and final HTTP status.
  Thread stages record submission and actual worker start/end so queue wait is
  distinguishable from execution. Log sanitized exception class only.
- Diagnostic logging must not alter error codes, response content or authority,
  publish twice, swallow application errors, or change cancellation semantics.
  Existing warming503, authoritative-price503 and isolated successful review
  contracts remain unchanged.
- Keep provider guard 6s default/8s maximum, smoke acceptance12s/transport14s,
  risk/cost/geometry/position validation, immutable proofs, OOS/sample floors and
  missing-model zero votes unchanged. No broad API deadline or price fallback.

## Verification and release

Deterministic event-controlled tests reproduce held store/feed locks and queued
executor behavior. Test both queued cancellation and already-running late worker,
single outstanding admission, immutable post-timeout snapshot/review bytes,
oversized bars refused without copying, fast valid unchanged attachment and the
producer→dataset→trainer proof. Trace tests verify actual warning-level emission,
ID correlation, stage ordering, queue distinction and absence of sensitive data.
Exercise actual production middleware install order with an event-controlled
held journal: a start event must already exist while the probe is blocked, and
its unchanged no-trade400 response must complete the same trace exactly once.
Keep positive isolated API tests and negative price/warming contracts.

A fresh task reviewer checks compliance and quality, followed by a fresh most
capable whole-feature review. Root owns one broad verification and official full
exact-tree CI; merge only after mandatory green. Acceptance requires automatic
deploy/readiness/functional/public and source/math publication on the same SHA.
If production still fails, preserve the actual trace and diagnose its stage;
do not blind-rerun the failed deploy or claim the release is accepted.
