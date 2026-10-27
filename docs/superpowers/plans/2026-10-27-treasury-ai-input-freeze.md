# Treasury AI input freeze — Implementation Plan

> Execute inline with superpowers:executing-plans; one fresh final review.

**Goal:** Supply the already running official Treasury cache to existing trained carry models in the same frozen AI review, preserving original source clocks.

**Architecture:** Capture one bounded, finite copy of `engine.passive._treasury_live_runtime.context_at(original_market_capture_ts)` before the expensive builder. Attach that private copy as `treasury_context_v1` before existing family inference. Do not read the runtime again during completion or cached request reuse. Existing Treasury source/freshness, model, horizon, action and authority checks remain the consumers.

**Tech Stack:** Existing Python snapshot materializer, Treasury runtime and family model bridge.

**Spec:** `docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`, `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`; user instruction to continue noncrypto model readiness.

## Evidence and constraints

- Base local `f671626` / tree `b1ad9f3d1d537ac7401912f36d29490b6bc6cc97` equals accepted main `69328c1e32937f6dc0d238dfb714dc29c0ecefd7`.
- Actual canonical acceptance 40467105628 passed all seven core contexts. Q audit 387 ms; AI 9,094 ms; full CI 5,005 passed. Main math 40467361603 verified 76 models, including 58/80 noncrypto, and three live XAU families but no qualified action preference.
- XAU carry direction is historically supported, but current input reason is NO_ADMISSIBLE_SOURCE. Read-only diagnostic 40469046538 confirms a running Treasury cache, 81 observations, successful original fetch and no error; saved AI row 428 has no Treasury root. Code freezes Treasury only into passive captures, while the carry adapter reads the AI snapshot root.
- No new source fetch, refresh, model fit or fabricated receipt. Keep the parent research-only and child gates unchanged. No broker-carry/quote authority is inferred.
- Preserve source publication, receipt, capture and expiry clocks. Optional malformed/missing cache must not break other inputs. Keep the 60 KB public snapshot limit; private Treasury copy is bounded to 2,000 nodes and 16 KB serialized JSON.
- Macro's missing Yahoo target bars and intermarket's unavailable exact aligned window are separate source limitations. Do not borrow OANDA bars for a Yahoo-trained model or retimestamp an old related-market window.
- Preserve unrelated dirty archive; crypto-specific work stays paused. No manual EDE, large DB copy, paid extra LLM call or live order.

## Task 1: coherent cached parent to existing child

**Files:** `seiltanzer/ai_frozen_expert_inputs.py`, `tests/test_ai_treasury_input_freeze.py`; the existing Treasury validator receives the final review's nonstring-URL rejection.

**Interfaces:** Existing `capture_market_inputs(engine)` private view and `freeze_expert_inputs(engine, snapshot, market_inputs=None)`; no public API signature change.

- [x] Add a real freeze-to-family-consumer regression with a fitted fixture carry model, preserving original Treasury values, hashes and clocks. Observe failure before implementation.
- [x] Test runtime replacement during a heavy build, private-copy isolation and idempotent cached reuse. A later runtime cannot replace the original input.
- [x] Test absent, malformed, nonfinite, oversized, failed, stale and future parents: no false carry preference and unrelated valid session forecast preserved.
- [x] Capture bounded cached context at the original market-input clock, attach before family inference, preserve preexisting snapshot input if there is no captured runtime parent, and keep optional failure isolated.
- [ ] Run focused freeze/Treasury/reference/budget profile, one final review, final full Python 3.11 CI, authorized PR/merge and actual release acceptance. Never claim live preference from local fixtures.
- [ ] Inspect the exact-SHA live XAU receipt after publication: carry input/model forecast should be present when the original Treasury and target inputs are admissible; positive action preference still requires the existing supported head/horizon rules.

## Review focus

- Original snapshot cutoff and independent market capture clock cannot admit a later rate fetch.
- Parent mutation/replacement after capture cannot alter the review or cached reuse.
- Missing/broken/oversized/nonfinite optional runtime cannot erase other model inputs.
- Source staleness, conservative availability, parent authority and child model/horizon gates remain unchanged.
- Normal and pressured snapshot packaging preserve usable frozen child features under existing byte limits.

## Decisions

Reuse the existing isolated worktree and verified accepted baseline. The user already authorized inline implementation and release. Freeze existing data rather than initiating another request or replacing statistical thresholds. No design/release approval is requested again.
