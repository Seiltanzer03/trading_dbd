# Noncrypto hourly event history implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; one final fresh whole-package review.

**State:** Source-capacity checkpoint only, not released. Actual wide official-calendar probe40281914988 returned0–75 usable H1 observations/target, below unchanged180 minimum. Older BLS pages unavailable; ALFRED fallback aborts the whole family at missing2025-10; older ISM pages mostly unavailable. Do not publish a code-only readiness claim. Preserve this tested pipeline and complete official-source history first.

**Goal:** Fit obtainable noncrypto event models from genuine two-year hourly observations without waiting for new captures or inventing five-minute prices.

**Architecture:** Keep existing target5m models and pretest native/archive selection. Only when the existing5m official-event contract is not pretest-eligible, select the separately declared hourly-open-to-open contract for60/120m. Real provider hourly opens avoid assigning a false full-hour close to shortened last-session bars. Native and shorter-history contracts are never switched using final-test scores.

**Spec:** Original unified-edge plan plus user's2026-10-26 instruction: pause crypto, complete configured noncrypto instruments first. Base local e095630/tree30332cf equals merged main7b0f5e5a1ab76f115b99045dbe6978b1451a28d3.

**Tech Stack:** Existing yfinance, NumPy trainer/purged validation, official release archive, frozen model runtime.

## Evidence and choices

Read-only probe40277948799 confirmed actual1h granularity/two-year history for all10 noncrypto symbols. Use original provider opens at their actual hour start timestamps and require every hour through60/120m label end; no interpolation, resampling into5m, fictitious receipts or broker-price equivalence. Keep native original4h event scope; JPY US-release overlap remains zero unless actual bars demonstrate otherwise. A future Japanese-event or overnight-response contract is a separate change, never achieved by silently extending freshness.

Alternatives: existing60day5m history has0–80 event observations; waiting contradicts user intent. Coarse bars relabeled5m invent evidence. A declared H1 target contract gives real60/120m outcomes and preserves source semantics.

## Global constraints / review focus

- Configured noncrypto Yahoo symbols only; crypto collector/model paths unchanged.
- Existing180observations and all purged validation, variation, final-test/admission gates unchanged.
- Historical bar starts are actual provider clocks; all records complete beforecapture. Reject duplicates, malformed OHLC, wrongsymbol, simulation, future bars, mutatedhashes and missing intermediatehour.
- Provider responses and series receipts retain actual later retrieval; historical first receipt unknown. No claimed historical HTTPreceipt.
- Same official release features and existing4h age; runtime exactsamefeature/time/horizon contract. Trained H1 expert only supports60/120m, never claims15/30m label verification.
- Exclude bounded large hourly sidecars from unrelated metadata walk and runtime report; independently validate perrow/hash/proof.
- One shared existing180s source deadline, max3 enrichmentworkers, max20000hourlyrows/target; optional failure cannot erase other models.
- Native vs5m vsH1 selection is frozen beforefinaltest; no fallback selected byheldout quality.

### Task1: Hourly source and label contract

Create seiltanzer/edge_family_hourly_events.py and tests/test_edge_family_hourly_events.py. Write REDtests for exact configuredprovider/source, actual60/120min OPEN-to-OPEN log returns and unchanged movement threshold. Include half-hour-aligned US index bars, shortened final-session hour (never consumeclose), missingintermediatehour, future/incompletebar, duplicate/mutated/synthetic/wrongidentity inputs. Implement strict source parser, bounded collector and pure dataset overriding the existing trainer. Run owningtests.

### Task2: Selection, proof, runtime integration

Modify edge_family_event_archive.py and edge_family_market.py only at archive fallback/provenance owner. Test5m pretest-eligible keepsitscontract evenif finaltestfails, short5m history selectsH1 beforeheldout scoring, and generic families unaffectedbylargeH1sidecar. _model_reason accepts1h interval only for exactnewnoncrypto event contract and validates its sourcehash/receipts/officialarchive; allotherfamiliesretain5m rule. Current frozen event timing remains same; hourly heads limited60/120m. Modify mathematical_edge_sources.py collector and scripts/run_mathematical_edge.py report filtering; testdenial/failureisolation/shareddeadline. Addworkflowpaths/owningtests. Run relevant suites.

### Task3: Actual acceptance

One final independent review; fixesCritical/Important viaRED→GREEN inonepass. MandatoryfullCI and actualfit, authorizedmerge/deploy/exactSHA actualconsumer. Inspecttrained/supportednoncryptomatrix. ReportremainingJPY/domesticpublication/orderflowgaps explicitly. Do not forceallfamiliesreadywheninputsabsent.
