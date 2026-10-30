# JSON publication budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the current lossless JSON migration within the existing
finite native-owner contract, then finish exact-SHA production acceptance.

**Architecture:** Run exactly two independent off-host publications concurrently
after combined staging admission. Preserve every existing full-GET/source/fence
gate and expose only a whitelisted cause on a successful terminal abort.

**Tech Stack:** Existing Python >=3.11, stdlib concurrent.futures, existing boto3.

**Spec:** `docs/superpowers/specs/2026-10-30-json-publication-budget-design.md`.

## Global Constraints

- No new dependency, provider, paid disk, data retirement, order or ACK.
- Preparation 5400s; native owner 10800s; existing recovery/start clocks unchanged.
- Both worker and both independent native full GETs remain mandatory.
- Preserve every original row/rowid/type/schema/header/history pin and source clock.
- One final scoped review/correction, one mandatory final CI, authorized merge.

## Review Focus

- Aggregate gzip staging overcommit despite each publisher's individual check.
- SFTP use from publisher threads or a newly renewed per-image deadline.
- Abort/capture release while a failed publisher's sibling is still running.
- Native fencing errors accidentally converted into safe original aborts.
- Private URLs/provider messages entering a public terminal failure field.

### Task 1: Correct publication scheduling and terminal diagnostics

**Files:** modify `scripts/production_json_history_rebuild.py` and
`scripts/history_production_owner.py`; create
`tests/test_json_history_parallel_publication.py`; extend
`tests/test_history_production_owner.py`.

**Interfaces:** `run(expected_sha, run_id, directory, resume=False)` and its
public plan/receipt contracts remain. Add a private conservative staging-bound
helper. Both `publish_image` calls receive the same deadline and separate
clients constructed on the parent thread. A terminal refused-handoff result may
add `handoff_failure_code` from an explicit safe allowlist, otherwise a fixed
`UNCLASSIFIED_VERIFICATION_FAILURE` value.

- [x] RED: real gzip/full-GET fixture with a two-reader barrier fails under the
  serial worker; both receipts must finish before handoff and only parent-thread
  SFTP access is allowed.
- [x] RED: insufficient total worker reserve refuses before launching the owner;
  insufficient post-build staging refuses before either cloud upload.
- [x] RED: corrupt one image, finish/join its sibling, then emit the existing
  frozen-epoch abort; original bytes remain unchanged and no request is written.
- [x] RED: safe abort retains IMAGE_TRANSFER_DEADLINE/TRANSACTION_DEADLINE;
  a private-message exception yields only the fixed fallback.
- [x] Implement exactly two publication jobs, conservative preflight/post-build
  reserve and shared deadline; no remote actions occur in publisher threads.
- [x] Implement optional terminal public reason without changing fencing paths.
- [ ] GREEN: run the changed tests and owning storage/owner/publisher profile;
  full Python/WebKit/native checks run once on the final PR revision.
- [ ] One independent read-only final review, one correction pass if necessary.
- [ ] Commit, publish PR, merge only after mandatory green exact-tree CI.

### Task 2: Actual release and closure

- [ ] Verify installed new SHA/guard and actual HTTP before a fresh current epoch.
- [ ] Run one new native operation using new current source and existing Yandex;
  preserve all old terminal metadata and the committed cold journal.
- [ ] Verify actual COMMITTED/service_started=true, physical size/free space and
  unchanged installed SHA; forward recovery only after commit.
- [ ] Finish canonical production/source/model acceptance and one ordinary saved
  AI receipt; report actual readiness/remaining evidence limits without forcing
  weights or asserting profitable policies from storage tests.
