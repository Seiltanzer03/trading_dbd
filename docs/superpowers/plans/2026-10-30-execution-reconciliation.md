# Execution reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline.

**Goal:** Block new AI commands until prior execution is reconciled and complete actual runtime recovery.
**Architecture:** Read actual ledger at entry/publication/delivery;409 structured context; existing factual ACK UI.
**Tech Stack:** Existing Python/FastAPI/SQLite/browser JS, no new dependency.
**Spec:** docs/superpowers/specs/2026-10-30-execution-reconciliation-design.md

## Global Constraints
- No order/ACK by agent, invented broker state, weight or risk/source gate changes.
- Preserve old ledgers and pending commands; ignore only current review during delivery.
- Existing5400/10800 storage owner clocks unchanged.

## Review Focus
- Pending command created while provider work runs must block publication.
- Self-created command must not fail its own delivery check.
- Superseded unresolved stop must remain actionable for factual non-execution.
- UI must not offer replay of an old crossed stop or fabricated fill price.
- Correct source/model/transport faults without widening admission.

### Task1: Execution boundary and reconciliation UI
**Files:** position_state.py, app.py, ai_review_jobs.py, scripts/production_functional_smoke.py, web/js/safe_fetch.js, web/js/management_ui.js, web/js/app.js; tests/test_execution_reconciliation.py and tests/web/unified_edge_audit_smoke.mjs.
**Interfaces:** PositionLedger.execution_reconciliation(trade, ignore_review_id=None) returns unresolved saved commands orNone; BrokerReconciliationRequired carries context; API409 error.reconciliation; UI factual receipts call existing ACK.
- [x] RED: pending EXIT/CLOSE/extended and superseded stop block without changing old rows; declined/executed clear; ignored current review excludes only itself.
- [x] RED: real API409 before snapshot/provider; no new ledger/verdict row; publication/delivery recheck catches late pending work.
- [x] RED: browser panel shows reconciliation/old IDs and no new trade command; explicit non-execution sends executed=false; no ACK on mount.
- [x] Implement owning ledger read, API entry/publication/delivery checks and structured critical UI.
- [ ] GREEN: owning execution/report/API and browser profiles, one final read-only review, one correction pass; one final mandatory native/WebKit CI per final diff.

### Task2: Existing release and recovery
- [ ] Use actual route diagnostic to correct provider fault only if proved; retain failed probes honestly.
- [ ] Publish final PR548 tree, green mandatoryCI thenauthorizedmerge; verify installedSHA/guard.
- [ ] One fresh-current lossless JSON migration through existing Yandex, unchanged clocks/source/fences/fullGETs; verify physicalCOMMITTED/start.
- [ ] ExactSHA source/model publication and canonical acceptance; verify reconciliation409 rather than issuing a new action on unresolved trade; report actual limits.
