# Durable single-file history cutover

The user authorized autonomous completion using the existing VPS, GitHub and
private Yandex storage. No trading action, fabricated observation, relaxed
reserve or new cloud resource is permitted. This design closes the interruption
blocker identified in `HOT_COLD_HISTORY_REVIEW_2026-10-18.md`; it does not by
itself close the separate runtime-reader blocker or authorize live retirement.

## Choice and boundary

The 25 GB root cannot hold both the original ~19 GB database and a ~15 GB hot
image. A same-filesystem staged atomic swap therefore cannot pass admission.
Mounting object storage as live SQLite is outside the accepted architecture.
Use a frozen writer interval, content-addressed verified cloud originals and a
durable guarded in-place transfer owned by a finite local process. This is not
called an atomic filesystem swap.

Implement the transaction core first against disposable SQLite files and an
injected service/transport adapter. Production invocation stays unavailable
until the real writer/service fence, workflow exclusion, consumer closure and
off-host artifact preparation are integrated and independently verified.

## Durable safety facts

- One canonical root and one existing deployment-maintenance flock owner.
  An active guard survives SSH/runner/process loss. All state files are private,
  regular, bounded, exclusively owned and fsynced with their parent directory.
  Reject symlinks, duplicate JSON members, unknown fields/versions, owner changes
  and concurrent owners. The persistent journal is authoritative after restart.
- Before any destructive write: source has no WAL/SHM/journal, writers are
  stopped, service start is fenced by an independently checked condition, actual
  original file SHA/size/quick-check match the plan, and both original and hot
  content-addressed cloud artifacts have verified full-GET receipts.
- A bounded canonical plan pins run id, original and hot SHA/size, exact allowed
  catalog pins for the hot image, and verified cloud receipts. Signed transport
  URLs are separate private transport input, never journal/report/log content.
  The core receives a stream, not arbitrary filesystem paths from a provider.
- The original may have the two documented pre-tombstone ACK parent holes;
  restoration means the exact verified original bytes, not a claim of clean
  original FK. The hot image requires zero FK violations and valid embedded
  history state/manifest pins in addition to full SHA/size/quick-check.
- Existing live snapshot gates remain unchanged. The frozen/offline path is a
  separate operation and cannot claim a live low-disk snapshot was admitted.

## Transaction and restart

`PREPARED` means ownership/fence/source proof and artifact receipts are verified.
Persist `APPLYING_HOT` before truncating the sole database. Stream with exact
size/whole-file SHA, bounded chunks/deadline, then fsync. Verify SQLite and pins;
persist `HOT_VERIFIED`. Recheck the service fence and closed writers. Persist
`COMMITTED` **before** releasing the guard or allowing the service to start.

Any exception or restart before `COMMITTED` enters durable `RECOVERING` and
streams the pinned original back into the same path. Verify its exact identity
and quick-check before persisting `ORIGINAL_RESTORED`; only then may the guard
be released and service started. Interrupted recovery retries the original.
If either restore or verification fails, retain the guard and journal in
`FAILED_GUARDED`; no service start or cleanup of the only partial file follows.
A later invocation with the same immutable plan/run may resume recovery using
fresh transport credentials. A different owner/plan cannot steal that epoch.

Loss of verified writer/service fencing has a separate terminal
`FENCE_LOST_GUARDED` phase. An unexpected writer may have produced actual new
records; merely stopping it later cannot authorize overwriting them. Refuse
automatic retry/restoration after that phase until a separate explicit data
reconciliation establishes a new safe epoch. Ordinary transport failure with
continuous writer exclusion remains resumable.

The hot operation and original recovery each have at most1800seconds, with a
3600second overall invocation bound. Hot timeout must leave a separate bounded
recovery opportunity. Failure of that recovery retains the guard for an explicit
same-epoch resume; there is no unbounded retry loop.

After `COMMITTED`, never download the old original again. The service may have
new real writes. A restart with guard still present can verify the unchanged hot
image and finish launch. A restart with guard absent reports committed without
comparing the live file to its pre-launch hash. Failure to launch is a rollforward
operational problem, not permission to overwrite new records with old history.

The controller checks the fence immediately before every destructive write,
before commit and before guard release. A newly observed WAL/SHM/journal, opened
writer, missing guard or unsupported state blocks progress. It must not invoke
automatic recovery against a file after the guard was released.

## Acceptance

Use real disposable SQLite files with distinguishable original/hot rows.
Inject truncation, excess bytes, wrong hash, stream failure, verification error,
failure during original restoration, process death and journal restart at each
pre/post-commit phase. Check exact original bytes on recovery, no launch of
partial data, no rollback after new committed writes, ownership/plan locks,
source sidecars/symlinks, and no signed URL or raw row leakage in receipts.
Finite focused tests precede one final read-only review and mandatory CI.
Production readiness additionally requires the actual systemd/workflow fence
and the independent runtime-reader acceptance; this core cannot report it.
