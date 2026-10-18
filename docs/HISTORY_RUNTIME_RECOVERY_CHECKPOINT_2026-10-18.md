# Runtime projection recovery checkpoint — 2026-10-18

## Integration status

This is a recovery record, not a release or runtime-capability certificate.
The execution environment disconnected during final local integration. Reading
or writing that filesystem is currently unavailable (environment_offline).
The last attempted plan append did not succeed. Preserve the existing worktree;
do not reset, clean, overwrite it from this branch, or deploy reconstructed code.

- Released main: `62a5438027c589a9bc0348e6125c60c7b3d83482`, PR488.
- Remote code checkpoint before this documentation commit:
  `aabef296912aea1cba589c4e668bb6230a4d6572`, tree
  `3d30352d223a5c619c9c16d560ccec0c40fd99bf`.
  This contains reference retention, rejected metadata and shared counter owners.
- Last known local committed HEAD:
  `a9caf4adff615eda5914641c06e78ff1582a3d5f`, tree
  `2414a789910abed22d50e6b0c0dc14a891f58785`, local branch
  `develop/history-runtime-retention-20261018`.
  This additionally binds v2 state, immutable index/rollups and full-union
  consumption. That commit has not been confirmed present in remote Git.
- Worktree:
  `/workspace/scratch/9ec76a9c16ef/trading-dbd-research-working`.
- Final reader/builder/startup integration and immediate disk-rejection handling
  were tested locally but remained uncommitted when execution disconnected.
  They are **not included in this remote checkpoint**.

## Last observed verification

These are recorded local results, not new CI results on this documentation
commit or proof that the disconnected filesystem has survived:

- History/runtime/transport/start-guard/G1/measurement/legacy profile:
  **222 passed in 8.17s**, following the strict-v1 compatibility fix.
- Research acceptance/orchestration/background-disk profile:
  **50 passed in 1.03s**, after proving immediate rejection on an observed
  disk admission pause instead of spending twelve minutes polling.
- Final integration has **not** received its independent final review, mandatory
  full CI, merge, deployment or production acceptance.
- PR488 alone passed full CI: 4390 passed / 5 existing skips / 13 subtests,
  real systemd drill, pinned SQLite and browser checks.

## Uncommitted integration to recover

Modified owned files after local a9caf4a:

- `scripts/history_service_fence.py`
- `scripts/sqlite_history_cutover.py`
- `scripts/sqlite_history_union.py`
- `scripts/production_post_research_check.py`
- `seiltanzer/g1_dataset_refinement.py`
- `seiltanzer/g1_dataset_runtime.py`
- `seiltanzer/history_runtime_projection.py`
- `seiltanzer/measurement_path_runtime.py`
- `seiltanzer/passive_learning.py`
- `seiltanzer/research_scalability.py`
- `tests/test_history_runtime_state.py`
- `tests/test_history_service_fence.py`
- `tests/test_sqlite_history_current_cutover.py`
- `tests/test_sqlite_history_cutover.py`
- `tests/test_sqlite_history_runtime_retention.py`
- `tests/test_research_acceptance_gate.py`

New owned files, last known untracked:

- `scripts/build_history_runtime_projection.py`
- `seiltanzer/history_runtime_views.py`
- `tests/test_history_runtime_readers.py`

Other untracked work is unrelated and must remain untouched.

## Implemented locally, pending review and publication

The v2 state pins both the metadata index and exact diagnostic rollups. The
index contains no forecast/features/outcome payload substitutes. Only
cold-identical IFD rows with existing hash-matching ineligible membership can
retire. Unprocessed, mutated, eligible and dependency/recent/health rows remain
full hot sources. All G1S parents and all virtual-position parents, including
resolved ones, remain hot. Recent retention checks captured_ts OR created_ts;
latest 1000 rows per instrument include boundary ties.

Actual passive/G1/refinement/exclusion/mutation/measurement readers merge exact
retired diagnostics, while eligible source/model/cut readers keep real full
rows. A real-engine pair proves equal counters, exclusions, refined cohorts,
eligible IDs and a nonempty eligible cut manifest. Source mutation detection
still hashes full hot records and verifies archived source hashes against
immutable membership. A pre-existing undefined _pl alias in light status was
fixed.

A stdlib startup guard checks actual v2 roots and literal owner versions.
Physical-only v1 markers remain refused for runtime; old strict historical
hydration retains its previous v1 manifest compatibility. Verified full union
restores all rows and consumes only the package-owned projections/guards to
avoid double counting.

Production acceptance now has a locally tested early failure for
disk_pause_active=true OR phase=disk_pressure_pause. It reports
RESEARCH_DISK_ADMISSION_BLOCKED before polling or sleeping. This changes no
reserve, evidence, risk, core-completion or source gate.

## Production observation and remaining sequence

See `docs/HISTORY_FENCE_RELEASE_OBSERVATION_2026-10-18.json`. Delivery and HTTP
startup succeeded on main62a5438, but overall acceptance failed because the
research worker remained in disk_pressure_pause. Cooperative lease release was
observed; later smoke/readiness stages were skipped. No production history
cutover, reclaimed capacity, fit, family promotion or order was performed.

1. Recover the original worktree and inspect HEAD/status/diffs before writes.
   Preserve unrelated files and reconcile this remote documentation commit.
2. Commit and publish the exact recovered integration; do not reconstruct it
   and claim byte identity. Re-run targeted tests only for recovery concerns.
3. Pin one candidate, obtain one final independent read-only review, address
   concrete findings, and run one mandatory full CI. Merge/release only green.
4. Implement and prove the separate finite production owner and authenticated
   original/hot image transport using existing Yandex Object Storage.
   The current disposable controller explicitly refuses /opt/seiltanzer;
   this recovery note does not authorize bypassing that refusal.
5. Verify the actual physical reserve and complete rollback/forward recovery
   protocol before a bounded production freeze/cutover. No standard VACUUM:
   surviving implicit rowids and all post-catalog rows must remain exact.
6. Restore headroom, then complete real exact-SHA research/AI/family acceptance.
   Do not rerun unchanged deploy/fit merely to hide disk rejection and do not
   require an impossible successful disk-gated research run before its capacity
   repair. Delivery/startup prerequisites and full release acceptance are
   separate, explicitly recorded facts.

Existing resources only: VPS94.241.171.182, local SQLite
/opt/seiltanzer/data/trades.db, service seiltanzer, bucket
trading-dbd-backups-2026 at https://storage.yandexcloud.net.
Never expose secret values or signed URLs. Keep source/risk/OOS/resource gates,
full source history, trade/ACK integrity and the frozen global scope intact.

## Workspace recovery

Execution access returned on 2026-10-18. The original local HEAD and all listed
owned changes were observed intact; the integrator resumed final verification.
