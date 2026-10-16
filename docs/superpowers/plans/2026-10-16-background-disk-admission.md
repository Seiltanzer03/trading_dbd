# Background disk admission Implementation Plan

> **For agentic workers:** superpowers:executing-plans inline; one final review. Existing autonomous implementation/release authorization applies.

**Goal:** Stop starting optional bulk SQLite writes when the production data volume lacks its existing1GiB reserve plus32MiB cycle headroom.
**Architecture:** One statvfs-backed admission function, called before the entire passive-step wrapper chain and before each research core/maintenance phase. Explicit degraded/paused state, automatic recheck, no fabricated successful capture. No quote/decision/manual-order path is gated here.
**Tech Stack:** Existing Python/SQLite/async worker.
**Spec:** Original unified-edge specification; locked global plan. Actual deploy38949677187 refused pre-fetch at521564KiB<524288KiB. Current resource guard only checks memory; passive collection continues writing below the required reserve.

## Global Constraints

- Threshold remains1GiB+32MiB; no reduced deploy/source gates and no live deletion/checkpoint.
- Pause optional bulk research only; real manual management, current quote ingestion and stored evidence remain unchanged.
- Skip is explicitly DISK_HEADROOM_UNAVAILABLE, never a new successful observation. Unknown disk state fails closed in production. Demo/test settings retain existing behavior.
- No claim this alone frees space or installs code on the blocked VPS.

## Task 1

Files: new `seiltanzer/background_disk_admission.py`, `tests/test_background_disk_admission.py`; modify `seiltanzer/app.py`, `seiltanzer/g1_research_worker.py`.
Interfaces: `disk_state(engine)->dict`, `passive_step(engine)->dict`, `apply_research_pause(state,engine)->bool`.
- [ ] RED: low/unknown space prevents real SQLite insert callback and does not advance collector cursor/success times; healthy space resumes it. Research phase states expose pause; demo bypass remains.
- [ ] Implement1GiB+32MiB admission at the outer passive call and at core/maintenance boundaries. Never hold SQLite while checking the filesystem.
- [ ] Profile new tests plus existing resource/worker/passive API tests; one review and one Important fix pass if required.
- [ ] Green final CI before merge; deployment remains honestly blocked until actual capacity is recovered.

## Review Focus

Capture wrappers must not run after a skipped inner function. Pressure must be rechecked between core components and before maintenance. Missing settings/path/stat failures must not silently permit production writes. Status must expose an actual pause. Finishing an in-flight transaction is allowed; this is admission at phase boundaries, not process cancellation.
