# LLM provenance input size implementation plan

> **For agentic workers:** Use superpowers:executing-plans; one final independent review.

**Goal:** Remove repeated technical provenance from the independent LLM input without removing any fact, source clock, numerical feature, or decision constraint.

**Architecture:** Factor exact common feature metadata into per-family defaults and encode repeated nested metadata by references. Decode references recursively, then overlay each feature's metadata on its family's defaults. Keep the original snapshot unchanged. Only use the encoded wire if its complete JSON is smaller. The provider prompt describes reconstruction.

**Tech Stack:** Python 3.11, JSON, pytest, existing GitHub Actions.

**Spec:** docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md; docs/LOCKED_GLOBAL_PLAN_2026-10-07.md; user's urgent fully functional noncrypto review request.

## Constraints
Risk/cost gates, evidence clocks, winner masking, 60KB ceiling, provider model, timeout and HTTP SLA remain unchanged. No fabricated observations, forecast votes, or historical profit. No live orders. Local exec is unavailable: isolated remote branch and bounded runner tests replace local worktree commands.

## Review focus
Different/missing/null metadata must reconstruct exactly; nested future clocks must remain visible to the existing validator; references must be acyclic; caller inputs must not mutate; small inputs must not grow or be re-encoded.

## Task 1: Correct the provenance wire owner
Files: seiltanzer/ai_provider_provenance_wire.py; seiltanzer/ai_runtime_report_v20.py; tests/test_ai_provider_provenance_wire.py.
- [x] Write exact roundtrip, heterogeneous source/receipt, missing/null, idempotence, future-clock and realistic nested-byte regression tests.
- [x] Runner40549933953: 1 failed/4 passed, expected size assertion25546 <25546*.45 fails on accepted code; no production access.
- [x] Implement share_provenance(projected: dict) -> dict with exact defaults and recursive metadata sharing; update the prompt's reconstruction rule.
- [x] Runner40550148836 on9edb876:94passed/one upstream Starlette deprecation warning3.42s. Readonly saved-review40550372843:45509B→39776B, source metadata/numeric equality and future-clock validationPASS; no provider call. Initial diagnostic40550286025 patched an unused report-module attribute, so it was a no-op and is not size evidence. Corrected probe patches the actual codec import owner.
- [x] One final independent reviewer:0Critical/Important,1Minor. Reviewer checked exact candidate checkout and94PASS log. Minor deferred: helper absent/null edge_family_facts robustness; actual _shadow_projection always produces a dict.
- [ ] Final applicable PR CI and candidate-tree full WebKit; authorized merge and automatic exact-SHA release.
- [ ] Inspect actual canonical AI smoke: require current structured LLM availability and report the real latency/token counts. Timeout must remain an unresolved blocker if still present.

## Ledger
Baseline main da68f5cb701cc02cd4111bbee1eeff9578b9c094.
Actual trace ai-adf825c80d684cc898f3: provider input45079B/prompt15477tokens; provider6988ms against about5500ms remaining. Read-only saved-review profile40515024549: intermarket features1197B, provenance25898B. Whole-dictionary sharing alone is insufficient.
Pre-flight: codec output and provider prompt are one interface and ship together.

Final review rulings: cyclic/non-JSON Python objects and scalar whole metadata entries remain outside the finite JSON adapter contract; null/missing values inside metadata are preserved and covered. Actual live latency/token count and complete release readiness are REQUIRED next checks, not excluded scope. Cost if byte savings prove insufficient: LLM still unavailable, and package cannot claim the timeout fixed.
Task1 implementation complete at9edb876; release acceptance pending. Main remainsda68f5c. No second review or repeated owning run for this documentation-only evidence update.
