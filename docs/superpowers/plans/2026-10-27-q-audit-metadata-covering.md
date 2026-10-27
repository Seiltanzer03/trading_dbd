# Q audit metadata covering read — Implementation Plan

> Execute inline with superpowers:executing-plans; one fresh final reviewer.

**Goal:** Keep the unchanged 5,000-attempt Q audit/readiness cohort within its existing 3,000 ms gate by avoiding wide observation-row reads.

**Architecture:** Add one covering index for observation identity, target, resolution state and instrument. Bind the existing left join to that index. Keep the attempt timestamp index and ordering unchanged so tied attempts retain their cohort. Read current committed resolution state from the same read-only transaction; no response cache or new maturity policy.

**Tech Stack:** Existing Python/SQLite runtime and GitHub release pipeline.

**Spec:** `docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`, `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`; the user's instruction to continue fixing the terminal.

## Evidence and constraints

- Baseline local `15a61ac` / tree `551518b893e354a3fce78b7dd06037e023b089a6` equals deployed main `ad7158cbe33b0babdc06a18feb7295e3277fe09a`.
- Deploy 40457586332 attempt 3 delivered the exact SHA and completed its required core in 179 s. Readiness failed after two Q audit timeouts and a 3,681 ms response against the unchanged 3,000 ms gate.
- Read-only profile 40462709716: attempt-only read 32.7 ms; joined read 1,074.2 ms. The plan uses the observation PK and fetches wide table rows. Warm HTTP 362 ms is not proof of cold-read readiness. The joined cohort had 1,582 created observations and no overdue pending predecessor requests.
- Read-only count 40462899318: 117,540 observation rows and 45,122 attempts. A four-column metadata index is bounded to that existing table, without copying historical payloads.
- Keep limit 5,000, existing successful-capture maturity counts, expiry rules, direct-evidence candidate rules, read-only snapshots and risk/authority unchanged. No new data, model fitting, TTL increase or larger latency budget.
- Preserve unrelated dirty archive. Crypto work remains paused. No manual EDE, large DB transfer, paid LLM probe or live order.

## Task 1: index-only current observation metadata

**Files:** `seiltanzer/g1_q_audit_scalability.py`, `tests/test_g1_q_audit_scalability.py`.

**Interfaces:** Existing `q_audit_bounded(now=None, limit=500, include_items=True) -> dict`; no signature/output change.

- [x] Add a real file-backed audit regression that captures the executed join and requires a covering observation lookup in its SQLite plan; literal maturity counts remain correct. Run it and observe the existing PK/table lookup fail.
- [x] Add a tied-attempt boundary/current-resolution regression. Confirm full audit parity and literal item order before and after the optimization.
- [x] Install `ix_passive_q_audit_metadata` on `(observation_id,target_ts,resolution_status,instrument)` and use it for the existing left join. Leave the attempt index, cohort order and all classification logic unchanged.
- [x] Run the owning Q audit, request-dedup and readiness profile. Mandatory Python 3.11 full CI remains the whole-suite gate; do not invoke the known broken native yfinance extension on this local Python 3.12 host.
- [ ] Commit, obtain one independent whole-package review, fix any Important findings with RED→GREEN once, and publish final CI/PR.
- [ ] Authorized exact-head merge, automatic delivery/readiness/smoke, seven core contexts, and actual current family forecast receipt. Do not claim complete acceptance from model loading.

## Review focus

- Equal attempt timestamps at the LIMIT boundary preserve the old cohort/order.
- A committed resolution update is immediately visible through the index; no stale cached classification.
- Missing observation rows retain their attempts and existing CONTRACT_REJECTED classification; the left join cannot silently drop them.
- Both full and summary forms use the same metadata/cohort and retain their existing candidate diagnostic differences.
- File-backed initialization installs the index before the independently opened request connection uses it; in-memory fallback remains unchanged.

## Decisions

Reusing the existing isolated worktree and verified baseline avoids repeating setup or full baseline CI. A second covering index on attempts is intentionally excluded: it could change the implicit ordering of equal timestamps and does not address the measured slow join. This is an indexing fix, not a claim that all cold-host contention has been eliminated; exact production acceptance remains required.
