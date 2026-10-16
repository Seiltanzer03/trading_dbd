# Preserve serialized production queue — finite release blocker

Original terminal specification/locked plan unchanged. Autonomous inline implementation; one fresh final review and final-tree CI.

Baseline main4a62a1c/tree6be502f6 (PR484). Actual deploy38982537150 and stage38982536995 for this green main were cancelled while older011b deploy38981470787 held the shared production lease. Recovery38982543923 is queued but cannot deploy an undelivered SHA. No manual cancellation or recovery mutation was issued.

Root cause: GitHub default concurrency permits one active and only one pending request. Shared deploy/stage/recovery/metadata repair replace one another's pending jobs. Official supported `queue: max` preserves up to100pending requests without parallel production mutation. Source: https://docs.github.com/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs . FIFO follows queue entry, not dispatch order; reject superseded main before any deploy/stage mutation.

## Scope

- Four tracked shared production-seiltanzer participants: deploy, SHA-stage job, single-slot recovery job, network-diagnostics compact job. Add queue:max, retain cancel-in-progress:false. Also preserve the stage workflow's own queue.
- Shared bounded `scripts/require_current_main.sh`: compare full expected SHA with authenticated current main; fail closed on stale/malformed/network response. Called after checkout before deploy SSH or stage bundle/transfer. No service, history, source/model freshness, reserve or acceptance gate change.
- Existing untracked ops-ede-headroom blueprint is outside main and must remain untouched.

## Steps

- [x] RED: six cases failed for default pending replacement/missing guard. Execute real main guard with isolated fake HTTP for current/superseded/network/malformed input; assert before mutation order.
- [x] Minimal queue + shared shell guard implementation;39focused existing deployment/orchestration/recovery tests passed0.63s; bash syntax/diff clean.
- [x] One fresh read-only whole-diff review: no Important blockers, no edits/remote writes. No weakening of source/risk/acceptance gates.
- [ ] Final CI and authorized merge.
- [ ] Observe new queued release and current service; do not cancel old owner or mislabel disk-paused research as passed. No duplicate archive restores or fits.

Separate actual startup trace38982603984: storage.prepare334.158s; app.create356.158s; schema.backup610.951s; uvicorn644.378s. Existing watchdog points to storage `_sqlite_integrity` from degraded prestart. Startup is slow but completed; do not extend deadlines or skip integrity to hide it.
