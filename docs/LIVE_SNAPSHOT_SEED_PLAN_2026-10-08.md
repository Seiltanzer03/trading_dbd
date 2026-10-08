# Next finite package: verified worker seed → current immutable research input

Prepared while PR421 production acceptance ran; implementation now follows in
the isolated fix/live-snapshot-seed branch. Production export adequacy is not
claimed before this branch's final CI/release and automatic research receipts.
Parent localcb5f8f6/tree7da64e82c917069a9139e1da0e22393d85ba2fef;
PR421 merged remote3a7e0400031442b85c8fa588b2b739c48f4d2a4a.

## Reproduced production failure

Audit37736484124/job113179209866 began export06:21:56Z, aborted06:29:00Z at
unchanged1GiB production reserve. Worker wrote5116362752bytes by375s. This
proves reserve exhaustion, not WAL as sole consumer. Discovery/transition and
publication never ran. Previous Python allocation fix is not exercised here.
Current source holds a consistent origin snapshot while writers continue;
replicate_live rejects any existing destination, workflow always starts new.

## Tasks and boundaries

1. Reuse one existing verified private Object Storage snapshot as worker seed.
   Resolve through owned storage manifests; validate source DB, manifest digest,
   database SHA256/size and integrity before using it. Retain seed source SHA and
   old cutoff separately; it is not current research input. Missing/corrupt seed
   fails closed explicitly. No new bucket, privileges, source or manual export.
2. Allow only explicitly verified seeded replication to a worker file. Existing
   pinned SQLite executable refreshes against current production under current
   exact-SHA/API/acceptance gate checks. Successful replication/checkpoint/quick
   check produces a NEW manifest and new snapshot clock; failed/cancelled sync
   cannot publish seed as fresh research. Keep original empty-destination path
   and worker-space/headroom/idle/total bounds; account for seeded resident bytes
   without counting overwritten storage twice. No evidence/table truncation.
3. Log initial/current filesystem free space and WAL bytes/delta separately.
   Preserve1GiB refusal. Verify run-owned origin reader termination on failure,
   as local SSH process kill alone is not proof of remote exit. Cleanup only
   run-owned executable/wrapper/process; never delete live WAL/database. A
   further attempt requires actual cleanup evidence.

## Verification

First regressions for corrupt/mismatched/sidecar seed refusal, actual SQLite
seeded refresh retaining all rows and current values, unchanged source epoch
on seed-only/failure, headroom abort and run-owned origin cleanup. Use the pinned
real sqlite3_rsync executable for at least one local consistent seeded refresh;
mock network/process boundaries only where necessary. Existing exact-SHA/gate/
backup contracts remain green. One combined review/finalCI/release. No duplicate
unchanged exports or full discovery dispatch; existing automatic chain provides
real adequacy evidence after release. Shorter replication is a hypothesis until
measured; arbitrary write pressure still may trigger protective refusal.

## Integration findings and acceptance boundaries

- The current full-restore receipt verifies complete compressed/raw hashes,
  size and immutable quick_check, but does not carry the backup source Git SHA
  or source snapshot clocks. Extend that owned receipt to retain those fields
  from its verified storage manifest before using it as a seed. Bind the receipt
  to the actual destination bytes again at the replication entry point; an
  arbitrary caller-authored dictionary is not sufficient seed verification.
- `production_ede_offload.live_snapshot` currently unlinks the output before
  replication. Explicit seed mode must preserve only the verified seed DB and
  invalidate any prior research manifest/selection before refresh. Successful
  refresh remains the only path that writes fresh research manifest/selection.
- Storage credentials exist in the subsequent upload step, not in the current
  export step. Seed restore needs the same existing bounded private storage
  credentials in its own step; do not add a new credential, bucket or permission.
- Restore a seed before the live origin reader is started. Its download and
  local integrity checks must not consume the production snapshot-reader
  interval. Preserve exact acceptance-marker validation before origin access.
- A restored seed is read-only for verification, but the worker copy must be
  mutable during SQLite refresh. No immutable connection may remain open during
  refresh; checkpoint/quick_check/hash run only after the pinned copier exits.
- Verify main DB, `-wal`, `-shm` and temporary disk requirements separately.
  A seed may reduce transfer duration but cannot be assumed to cap source WAL
  growth or guarantee that arbitrary writer load fits the unchanged reserve.

Read-only integration review found a concrete additional blocker: full restore
currently requires backup_id and nonempty critical_table_counts, whereas the
LIVE_SQLITE_RSYNC manifest supplies neither and upload preserves that manifest.
Do not assume the existing restore helper accepts the live snapshot slots.
Resolve an explicit verified live-seed contract using their actual existing
hash/size/source/table-integrity evidence; preserve stricter full-backup restore
rules. Unsupported or unverifiable existing objects remain refused. Add a
fixture produced by the real live-export/upload manifest path before integration.

Seed git_commit may differ from current expected_sha only as replication input.
Preserve old clocks without reconstructing missing ones; fresh selection cutoff
is the successful copier's new started_ts, never restore/upload/end time.

Origin cleanup is required on success, abort and cancellation. Record run-owned
PID with executable identity and process start time to reject PID reuse; require
confirmed exit before fresh manifest/publication or another attempt. Cleanup
failure retains the primary error and existing acceptance-gate release contract.

## Implementation checkpoint

Explicit `--live-seed` mode retains verified storage manifest/hash and complete
compressed/raw checks, but emits full_restore_verified=false. The original full
retirement restore still refuses live manifests without its recovery evidence.
Seed entry revalidates actual DB bytes and immutable integrity; sidecars refuse.
The workflow restores before origin access using existing storage credentials.
Only successful current refresh produces a new research manifest/selection.

The run-owned wrapper records PID/birth ticks before exec. Cleanup sets a stop
marker, matches run-owned executable/transition arguments, rechecks birth ticks
before signals and confirms reader exit before removing run-owned tools. It
preserves primary errors and blocks fresh research when cleanup is unconfirmed.
SIGTERM initiates bounded cleanup; forced SIGKILL/runner loss cannot guarantee
Python finally execution and is not claimed as confirmed cleanup evidence.

Filesystem free bytes and WAL bytes/deltas are logged separately at start,
periodic progress, low-reserve refusal and completion. The original1GiB origin
guard and2GiB worker margin remain; free worker space still reserves current DB
size for worst-case rewritten WAL/journal pages, beyond resident seed bytes.
Idle600s/total2700s and exact-SHA/API/acceptance gates remain unchanged.

Real pinned3530400 integration uses its actual remote-side protocol and new
origin wrapper with a local transport shim, updates a multi-page seed while
retaining600 unaffected rows and sends fewer pages than the full database.
Local sandbox PID/proc namespaces differ: actual process-termination tests are
required on native final CI, and production cleanup refuses namespace mismatch.
No extra export, model search, LLM call or production mutation was dispatched.
