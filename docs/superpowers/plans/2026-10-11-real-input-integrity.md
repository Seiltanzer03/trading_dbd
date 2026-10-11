# Real input integrity — finite implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. One final independent review and exact-tree release.

**Goal:** Prevent simulated evidence and receipt-time rejuvenation from becoming real expert input.

**Architecture:** Keep the existing decision and source contracts. Apply one bounded simulated-evidence guard at input/model boundaries. Preserve Yahoo's provider quote time through StreamHub and feeds, so receipt time cannot masquerade as market freshness.

**Tech Stack:** Python, pytest, existing yfinance protobuf schema, GitHub CI/deploy.

**Spec:** Original `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, especially sections4–6 and12; user's explicit request to audit and fix imitated data/results.

## Constraints and release boundary

- Preserve genuine source facts, original budgets, manual execution and all risk gates.
- No new source, model search, broker-cost collection or fake votes. Missing sources/models remain explicit.
- Production audit reads cached HTTP and existing exact-SHA publications; no new paid verdict or trade.
- Reuse accepted baseline36aa3f1, same tree as released maindb1cc3c; no unchanged baseline/full CI rerun.
- Finish after fixed regressions, one final review, mandatory final-tree CI and actual server/publication acceptance.

## Review focus

- Nested demo markers in source constituents cannot be stripped by an adapter into valid evidence.
- One invalid optional source/model cannot erase unrelated genuine inputs.
- Real quote time is preserved for proxy and instrument; missing/future/stale time cannot become fresh.
- Replayed/out-of-order frames cannot overwrite a newer valid quote.
- Tests and delivered source bundles never establish all-family readiness or observed profit.

## Task1: Reject simulated expert evidence

Files: new `seiltanzer/real_input_integrity.py`; existing edge adapters/source runtime/runtime context/math/ensemble; `tests/test_edge_real_input_integrity.py`.
Interface: `simulated_evidence(value) -> bool`, bounded dict/list traversal of existing synthetic/demo/is_demo/synthetic_demo/contract_fixture markers.

- [x] Add regressions: each demo flag on source/model/snapshot; nested flags; valid unrelated family survives; loaded bundle/context and math demo cannot vote.
- [x] Run them and observe failures before changing product code.
- [x] Guard each owning boundary, preserving existing rejection codes and genuine data.
- [x] Run affected source/model/runtime/ensemble tests; integrated release commit.

## Task2: Preserve actual Yahoo quote time

Files: `seiltanzer/data/stream.py`, `seiltanzer/data/feeds.py`, `tests/test_stream.py`.
Interface: parse field3 of the existing PricingData schema (sint64 milliseconds); `latest[ticker]=(price,provider_seconds)` retains current interface.

- [x] Add regressions encoded with actual PricingData: old frames, missing/future time, nonfinite price, out-of-order frames and feed/proxy timestamp preservation.
- [x] Observe failures, then decode actual provider time and preserve it through feeds. Keep bounded clock skew and existing fallback roles; no source switch.
- [x] Verify affected real quote/Bybit/crypto/canonical contracts; integrated release commit.

## Task3: Record actual readiness and release

- [x] Record code/runtime audit: explicit demo mode is separate; mathematical publication is real; extra family bundle has facts but0admitted models; modeled scenarios remain modeled; confirmed broker events remain manual.
- [x] One final read-only review. Fix Important/Critical once with regressions; no repeated review. 5Important →14RED→GREEN; final owning profile839passed7.84s.
- [ ] Final-tree full CI → merge → actual deploy/readiness/smoke/public HTTP → exact-SHA math/source/FOMC receipts. Record real limits and close this package.
