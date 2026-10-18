# Durable history cutover implementation plan

> Execute inline with `superpowers:executing-plans`: one implementer, one final
> read-only reviewer. The user supplied the execution method and authorized
> autonomous continuation. No additional approval menu is introduced.

**Goal:** Prove that interruption cannot open a partial single-file SQLite image
or roll back real writes after the cutover commit point.

**Architecture:** A small stdlib transaction controller owns private durable
state and a persistent service guard. Transport and writer/service fencing are
explicit adapters; the core is first exercised only on disposable databases.
Production entry remains unavailable until actual fencing and consumer closure.

**Tech stack:** Python3.11 stdlib, SQLite, Linux flock/fsync, existing systemd,
GitHub Actions and Yandex bucket `trading-dbd-backups-2026`.

**Spec:** `docs/superpowers/specs/2026-10-18-durable-history-cutover-design.md`.

## Constraints and review focus

- Preserve all original scope, risk/OOS/resource gates and ACK/trade records.
- No in-place transfer without durable guard, closed writers and verified
  content-addressed originals; no release of partial files or unrelated cleanup.
- Runner loss is not a recovery mechanism. Local journal and finite local owner
  determine recovery; process restart before commit restores the original.
- After COMMITTED, new writes prohibit all automatic original restoration.
- Reject ambiguous state/plan/ownership/symlinks/sidecars and concurrent owners.
- Receipts contain phases, hashes and sizes only; never signed URLs/raw records.

## Task1 — disposable transaction controller

**Files:** create `scripts/sqlite_history_transaction.py`,
`tests/test_sqlite_history_transaction.py`.

**Interface:** `execute_cutover(root:Path,plan:dict,*,transport,control)->dict`.
The root contains only its owned `trades.db`, `cutover.active`,
`cutover.journal.json` and `cutover.owner.lock`. Transport supplies
`open_image(kind)` context-managed byte streams; control supplies
`assert_fenced()`, `assert_writers_closed()`, `start_verified_database()`.
Plan pins `run_id`, exact original/hot byte identities, hot storage manifest and
verified immutable artifact receipts. It never contains transport URLs.

- [x] RED real SQLite cases: successful hot launch, interrupted hot transfer
  restoring exact original, failed recovery staying guarded, process restart
  resuming restoration, and no original reads after committed new writes.
- [x] Implement bounded canonical plan/journal, exclusive flock/guard,
  fsync state publication, exact download/SQLite/pin checks, durable phases and
  recovery. Refuse the canonical production path in this disposable-only stage.
- [x] Add wrong size/hash/extra bytes, stale owner/plan, sidecar/symlink,
  unsupported journal, launch failure and interrupted recovery regressions.
- [x] Run focused profile and commit only owned source/spec/plan/tests.

Initial disposable checks cover a real subprocess killed after truncation,
restart at PREPARED/APPLYING_HOT/HOT_VERIFIED/COMMITTED and both recovery
phases, partial/wrong/excess streams, unrecoverable original, post-commit new
writes and launch failure. Additional REDs caught numeric pseudo-booleans and
unbound manifest pins in preparation receipts, plus caller mutation of the
already verified plan. The plan is now frozen before adapters run. Lost writer
fencing is permanently guarded for separate reconciliation, not treated as a
retryable download failure. This remains a disposable-only core with no
production CLI and an explicit refusal of every `/opt/seiltanzer` root.
Focused core profile:29passed in1.51s, including the actual killed subprocess.
Hot timeout has a separate finite recovery budget; a commit-publication/fsync
error refuses rollback once COMMITTED is visible. No production mutation or
extra full cloud restore was used.

## Task2 — actual deployment/service fence

**Owners:** existing `deploy.yml`, `production-single-slot-backup-recovery.yml`,
`production-health-recovery.yml`, `emergency-production-disk-recovery.yml`,
existing deployment filesystem lock and finite systemd task patterns.

- [x] Inventory every production writer/start/restore path; reuse shared
  `production-seiltanzer` queue and deployment filesystem flock.
- [ ] Implement canonical active guard plus persistent service-start condition;
  ensure queued recovery/deploy refuses it before any mutation. Prove service
  refusal with a disposable actual systemd unit; do not stop production for a test.
- [ ] Integrate finite local owner independently of SSH runner, exact closed
  writer verification and bounded private transport. Retain guard on every
  failure. No live invocation until Task3 and runtime-reader acceptance pass.

Runtime-start fence implemented as a stdlib-only external ExecCondition plus a
negated active-path condition. It executes before existing ExecStartPre backup
hooks (systemd249 service contract), rejects unfinished journals even without
the active file, and refuses marked data when the checkout lacks the future
runtime-reader capability. The capability is deliberately not declared yet.
The actual hot state/retained identity is checked without importing the app.

Deploy and single-slot recovery refuse guarded mutations after the shared host
lock; recovery also checks before its read-only healthy shortcut. Emergency
cleanup now uses the same queue/lock and is manual-only, so releasing its guard
cannot itself stop the service. Health diagnostics suppress recovery dispatch
for a guarded epoch. Real Bash preambles are exercised with active and pending
journals, including the actual read-only checker. Latest combined guard/core/
existing recovery profile:92passed in2.26s. Actual systemd runner proof remains
pending before this stage can be considered operationally verified.

PR487 is merged at919d524; full CI4316passed and pinned SQLite/WebKit passed.
Deploy39085864690 timed out before acceptance: pre-engine integrity429.267s,
post-schema backup/integrity253.199s and total startup732.713s. The app became
available at01:29:49UTC; exact-SHA recovery39086708682 observed it healthy and
did not stop/redeploy it. Public health39098609566 later returned200. Full
release acceptance is not claimed. Scheduled EDE39102091467 again refused the
unchanged WAL headroom gate with644,825,088free bytes; no full restore/refit ran.

## Task3 — authenticated current image and final physical acceptance

**Owners:** existing immutable snapshot/cloud upload/restore/cutover tools.

- [ ] Prove frozen source capture below the live snapshot reserve without
  relaxing live gates or allowing WAL growth, and verify current source bytes.
- [ ] Prepare original and current hot images off-host once; verify full cloud
  GET/identity/SQLite receipts under run/content-addressed keys in the existing
  bucket. Check actual reclaimed bytes/reserve before any destructive step.
- [ ] Independent final review and full CI; authorized release and exact-SHA
  capability checks precede finite live invocation.
- [ ] Only after runtime-reader closure: run the proven protocol, verify actual
  durable headroom, next rowids/outcomes, backup/full research readers and HTTP.
  Post-commit failures preserve new writes and use rollforward recovery.
