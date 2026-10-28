# Bounded background AI review implementation plan

> Inline implementation with one final independent review. User explicitly asks root to finish rapidly without further planning questions.

**Goal:** Admit a real provider response beyond a gateway-sized request without publishing stale management advice.

**Architecture:** Production POST starts one process-local task and returns202. GET polls that same opaque job; no polling request invokes the provider. The existing complete review, frozen input, ensemble and atomic publication remain the only decision path. Detached inference has a45s total transport/wait bound; synchronous clients retain their8s provider and12s request budgets. The browser and canonical release smoke follow202 with GET. Completed responses are checked again against the actual position, fresh quote, existing.15R movement bound, barriers and expert expiries. Jobs expire after120s and disappear on restart. No queues, automatic retries or orders.

**Baseline:** accepted main73a32688a90fcbf9b8334b92c583955bb74061aa;5079 tests/nativeSQLite/WebKit green. Production receipt437 proves report comparison/coverage/21audit rows fixed but actual8053ms provider timeout. Repeated codec/deadline tuning exhausted; this changes lifecycle instead.

**Alternatives:** Further packet pruning loses useful facts without bounding inference. Larger synchronous deadlines violate gateway protection. Selected by integrator: one bounded detached review with current-state rejection; same model/provider and evidence gates.

## Constraints and review focus

- Crypto paused; no training reruns, new provider/model, DB copies, execution or fabricated observations.
- One admitted task, repeated clicks/polls cannot multiply paid calls; cancellation cannot release an actually running worker.
- No shared request object or mutable trace state across polls; one independent job trace.
- Restart/unknown/expired jobs have explicit errors; provider failure remains visible deterministic fallback, never fake success.
- Closed/edited/filled position, price drift/barrier or expired expert cannot deliver cached actionable advice.
- Preserve exact frozen input/archive clocks, all risk/cost gates and original capture; no retiming evidence.

## Task1: lifecycle and inference

- [ ] Add failing regressions for quick202, one provider execution, GET-only polling, failure/unknown/expiry, late response beyond synchronous allowance and second guarded delivery.
- [ ] Add ai_review_jobs.py controller and detached inference context, production install; retain existing standalone synchronous create_app behavior.
- [ ] Use separate bounded45s detached transport; synchronous budget unchanged. Shutdown cancels task and preserves actual worker admission.
- [ ] Check existing API/worker/provider/source-clock contracts.

## Task2: clients and sequence

- [ ] Browser follows202/relative same-origin opaque job GET with shared original promise; no second POST on pending.
- [ ] Canonical production smoke follows same protocol and retains per-request12s ceiling.
- [ ] Regression for actual repeated pending CLOSE10 decision id retaining continue_same_pending_decision instead of replacement label; preserve executed-action suppression.
- [ ] Focused owning profile; one independent final review; only reproduced important fixes.

## Task3: release

- [ ] Exact-tree PR mandatory fullCI, expected-head merge, automatic mainCI/deploy/all7 core statuses.
- [ ] Inspect actual canonical LLM completion/timing/nonzero contribution or explicit admission reason, unchanged archive proof, CVaR/coverage/continuity. No success inferred from tests or dispatch.
- [ ] Record limitations: models/forecasts/preferences/profit are separate, licensed missing inputs remain unavailable.
