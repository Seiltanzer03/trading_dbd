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
- [ ] Write exact roundtrip, heterogeneous source/receipt, missing/null, idempotence, future-clock and realistic nested-byte regression tests.
- [ ] Run owning tests on a Python3.11 runner; confirm the size regression fails on the accepted implementation.
- [ ] Implement share_provenance(projected: dict) -> dict with exact defaults and recursive metadata sharing; update the prompt's reconstruction rule.
- [ ] Run the focused provider/telemetry/projection/shadow profile. Record actual saved-review byte reduction without another provider call.
- [ ] One final independent review; fix reproduced Important issues with regressions.
- [ ] Final applicable PR CI and candidate-tree full WebKit; authorized merge and automatic exact-SHA release.
- [ ] Inspect actual canonical AI smoke: require current structured LLM availability and report the real latency/token counts. Timeout must remain an unresolved blocker if still present.

## Ledger
Baseline main da68f5cb701cc02cd4111bbee1eeff9578b9c094.
Actual trace ai-adf825c80d684cc898f3: provider input45079B/prompt15477tokens; provider6988ms against about5500ms remaining. Read-only saved-review profile40515024549: intermarket features1197B, provenance25898B. Whole-dictionary sharing alone is insufficient.
Pre-flight: codec output and provider prompt are one interface and ship together.
