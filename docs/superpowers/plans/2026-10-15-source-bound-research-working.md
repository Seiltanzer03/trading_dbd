# Source-bound research working package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Finish usable manual decision inputs from bounded published rules and existing historical/current data.
**Architecture:** Preserve existing pools, verifier and action path. Add optional fingerprinted prediction transforms and one bounded preliminary context with explicit estimation semantics.
**Tech Stack:** Existing Python/numpy/pytest; existing off-host workflows and Yandex.
**Spec:** docs/RESEARCH_WORKING_PACKAGE_SPEC_2026-10-15.md

## Global Constraints
No new providers or live orders. Existing risk/cost/source clocks unchanged.
Three variants only; selection never reads final-test gains. Approximation never
becomes observed order book, market consensus or verified broker cost.
One final full CI and scoped independent review. Original user files preserved.

## Review Focus
Hash-valid unknown transform must reject; inverse chosen only from early labels;
future/stale/wrong configured series cannot produce context; estimated costs
cannot grant execution authority; preliminary source duplicates count once.

### Task 1: Bounded probability variant selection
Files: seiltanzer/forecast_variants.py; seiltanzer/edge_family_market.py;
tests/test_forecast_variants.py; tests/test_edge_family_market.py.
Interface: variant_predictions(probabilities, baseline, variant); VARIANTS tuple.
- [x] Add failing inverse/finite baseline-shrink, unknown variant and purged selection regression.
- [x] Observe RED, add variants reusing each original fit, select validation mean only.
- [x] Fingerprint selected transform and apply it consistently during final test/runtime.
- [x] Run owning market tests and commit.

### Task 2: Frozen proxy context and preliminary preferences
Files: seiltanzer/research_working_context.py; seiltanzer/ai_frozen_expert_inputs.py;
seiltanzer/unified_edge_ensemble.py; seiltanzer/operational_edge_compaction.py;
tests/test_research_working_context.py.
Interface: attach_research_working_context(engine, snapshot);
research_working_component(snapshot).
- [x] RED for configured causal bars -> labelled pressure/momentum, invalid clocks,
  reported release-change reference, financing assumptions without cost authority.
- [x] Implement bounded existing inputs only; .25 fallback factor in existing math
  pool, preserving available trained profiles and shared source lineage.
- [x] Verify source exclusion and frozen common-bank risk/economics invariants.

### Task 3: Prompt/audit delivery and finite release
Files: seiltanzer/llm_decision_shadow.py; seiltanzer/ai_runtime_report_v20.py;
seiltanzer/unified_edge_audit.py; tests owning prompt/audit/context path;
docs/RESEARCH_WORKING_PACKAGE_RECEIPT_2026-10-15.md.
- [x] RED for labelled context arriving in independent LLM and final audit.
- [x] Preserve context through budget/prompt; render concise roles and assumptions.
- [x] Run owning profile, one whole-branch scoped review; fix Important findings.
- [ ] Publish final PR/CI. Complete predecessor handoff, merge/deploy/exact checks;
  run one normal bounded off-host model evaluation, record real results/limits.
