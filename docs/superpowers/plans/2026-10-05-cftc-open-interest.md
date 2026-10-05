# CFTC open interest implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, one final fresh reviewer.

**Goal:** Preserve actual CFTC OI level/change without replacing net-position facts.
**Architecture:** Existing GET body → independent optional parser → unchanged history proof → frozen adapter/runtime/trainer admission.
**Tech Stack:** Python, existing pytest; no new dependency.
**Spec:** `docs/superpowers/specs/2026-10-05-cftc-open-interest-design.md`.

## Global constraints

No additional HTTP requests, no backdated receipt, no mapping promotion, no
model/vote/risk/cost/geometry/OOS changes. Preserve existing work and evidence.

## Review focus

- Missing newest/immediate prior OI never becomes zero or a longer-period change.
- Negative/boolean/nonfinite/fractional counts refuse optional OI alone.
- Mixed records preserve net and OI independently in either order.
- Body/receipt/series/category proof remains bound at runtime and trainer.
- Same source dependency and unvalidated proxy cannot grant independent votes.

## Task 1: Producer and frozen admission

**Files:** `seiltanzer/edge_family_sources.py`, `edge_family_adapters.py`,
`edge_family_history.py`; `tests/test_cftc_open_interest.py`.
**Interfaces:** `parse_cot_open_interest(body: bytes, *, contract: str, receipt: float, source_id: str) -> dict`;
existing source bundle and history contracts stay unchanged.

- [ ] Write producer/adapter/bundle/replay-proof cases. Hand-derived OI 200→240 = +40; net 40→70 = +30. Missing/invalid OI retains original net. Wrong mapping/future receipt refuses facts.
- [ ] Run new tests; expected RED missing producer, then meaningful behavioral failures.
- [ ] Implement parser of exact latest pair from existing body; append optional proxy record through existing parse boundary. Namespace OI base features and reject negative OI at admission.
- [ ] Run new tests plus source/history/adapters/runtime/training/pipeline profiles, expected all pass.
- [ ] Commit product/evidence, one fresh read-only whole-diff review; fix only Important/Critical findings test-first.
- [ ] Publish draft PR; official exact-tree CI, ready/merge when mandatory green, then exact-SHA deploy/readiness/smoke/source/math.
