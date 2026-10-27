# AI review consistency implementation plan

> **For agentic workers:** Use superpowers:executing-plans; root implements inline in the existing isolated worktree, with one final independent review.

**Goal:** Remove contradictory LLM ranking, causal input clock rejection and rounding failures in historical management feedback reported by the user.

**Architecture:** Keep existing expert budgets, models and replay engine. Reject an inconsistent current LLM opinion without manufacturing replacement scores. Validate explicitly frozen market inputs at their own original capture boundary, bounded by final snapshot capture; keep quote freshness on the original quote audit. Replay an actually observed capture R instead of its four-decimal serialization when the discrepancy is only rounding.

**Tech Stack:** Python, SQLite, pytest, existing GitHub CI and exact-SHA release.

**Spec:** User's 2026-10-27 XAU report and assessment; AGENTS.md and LOCKED_GLOBAL_PLAN_2026-10-07.md. User already authorized inline implementation/release and opted out of repeated planning questions.

## Global constraints

- Crypto-specific development paused; no new instruments/models/providers.
- Preserve all source identity/hash, original receipt, TTL, OOS/horizon, hard-risk and cost gates.
- No paid manual LLM calls, broker orders/ACKs, DB transfers or manual EDE reruns.
- A trained model, current forecast, qualified preference and observed profit remain separate.
- Preserve unrelated dirty research archive and sibling ledgers.

## Review focus

- Conflicting global scores cannot vote via family blends or be resurrected by sparse fallback.
- A higher-scored hard-blocked action must not invalidate a consistent admissible choice.
- Frozen market clocks cannot advance with the slow builder or quote-only repricing.
- Later receipts and stale/wrong-identity references remain rejected.
- Rounded capture R cannot overwrite observed path points; a genuine mismatch must remain an error.

## Evidence

Accepted fc6c5ae: main CI40472149541, deploy40472715530 and all seven core statuses green. Q audit753ms first attempt; AI6934ms. Math40478285461:76 loaded,41 historically supported; XAU4 forecasts including qualified carry at capture. Later read-only40499620068 confirms periodic0 forecasts, an old quote audit cutoff, saved HOLD scores=-1 vs every other action=+.1, and local feedback ValueError:path must start at ExecutionSpec.current_r. The source SHA mismatch in the supplied earlier review predates the exact-SHA source publication; it is absent in the newer saved review. No authority check should be relaxed for it.

### Task 1: Guard current LLM consistency

- [x] Add a regression using the actual contradictory score vector; require zero current-LLM weight/contribution and a visible rejection, including supported family interpretations.
- [x] In collect_components(snapshot,candidates,current_llm), reject a provided choice scored below another hard-admissible candidate; preserve sparse choices, ties and consistent comparisons against hard-blocked actions. Do not let the rejected opinion fall back to synthetic choice scores.
- [x] Check ensemble, family working and report/audit contracts; commit owning change.

### Task 2: Use the original frozen market clock

- [x] Reproduce a cached quote tick from the prior bucket plus a current reference already received before capture_market_inputs.
- [x] attach_market_family_context uses the explicit original frozen capture bounded by snapshot capture, retaining legacy audit cutoff for callers without a frozen view. Never retime a source or use builder completion as input capture.
- [x] Check current reference inference and negative future/stale/hash/identity plus slow-build/cache tests.

### Task 3: Repair observed replay rounding

- [x] Reproduce counterfactual replay at exact capture with rounded r0=.1507 and observed R=.1506666667, including a real local-window resolution.
- [x] Bind execution spec to the existing observed initial point for serialization discrepancy <=.0001R; do not alter the path. Reject larger same-capture mismatch; retain existing legacy missing-initial behavior.
- [x] Align original-plan baseline with the same measured capture value; verify independent literal close-policy economics and local immutable outcome insertion.

### Task 4: Review and release

- [x] Run one focused owning profile, diff check, one fresh final reviewer; fix actionable findings with regressions, no repeated review.
- [ ] Final mandatory PR CI, exact-tree merge, main CI, automatic deployment, seven core statuses and actual family inference receipt.
- [ ] Read-only verify local feedback resumes; report actual limitations without calling pending actions executed or drawdown realized policy PnL.
