# Off-host input preflight and package-cache headroom — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. One implementer,
> one final scoped reviewer; autonomous execution already authorized by user.

**Goal:** Restore the existing safe research export if reproducible OS cache
can provide the required reserve; otherwise fail before downloading the seed.
**Architecture:** Existing offhost_sqlite_snapshot guards remain authoritative.
Expose a preflight CLI before Yandex restore; reuse the same guard again at
actual export. Opt-in cleanup touches only apt metadata/cache under package
and production locks, with strict path/type checks and measured free space.
**Tech Stack:** Existing Python, SQLite, SSH, GitHub Actions, Yandex bucket.
**Spec:** Original unified-edge plan §§7/11/12, effective plan and version boundary.

## Constraints and measured trigger

- Main9f8eb5a8/tree54df65c2 is accepted; no reopening A/C/D or unchanged model search.
- Runs38009715662 and38033277697 failed before replication: MIN_FREE_BYTES=1GiB,
  actualfree973320192B. Each first downloaded/verified the large cloud seed.
- Diagnostic38035582796 confirms live17803968512B, free929MiB, HTTP200/NRestarts0;
  apt cache113MiB, Git38MiB, no inactive runner version. Other projects untouched.
- Preserve source/worker free-space gates, SHA/API/seed/integrity guards and history.
- Delete only reconstructible package indexes/binaries; preserve apt locks and
  partial transactions, installed packages, runner, logs, data and backup files.
- No new cloud resource/source/permissions; no fabricated new evidence/profit.

## Task: finite safe input-chain repair

Files: scripts/offhost_sqlite_snapshot.py, scripts/production_ede_offload.py,
scripts/reclaim_apt_cache.py, .github/workflows/production-ede-v12-audit.yml,
tests/test_offhost_sqlite_snapshot.py, tests/test_reclaim_apt_cache.py,
tests/test_production_ede_offload_probe.py.

- [x] RED: low source space refuses before seed bytes/SQL are read; real temporary
  apt tree cleanup preserves unrelated files and refuses symlinks/hardlinks/locks.
- [x] GREEN: shared read-only source/worker preflight; CLI before restore with
  opt-in measured apt-cache reclaim only below1GiB+256MiB target.
- [x] Retain a fresh gate immediately before replication; preflight is not a
  source snapshot, backup, or authority. Source deterioration still fails closed.
- [x] Focused exporter/cache/restore tests; workflow Bash/YAML check; one review.
- [ ] Final exact-tree mandatory CI, authorized PR/merge and real server acceptance.
- [ ] Read the actual next automatic export/publication receipt once; successful
  dispatch does not prove export, and an old seed never proves a fresh backup.

## Review focus and finish

Locked apt or concurrent production mutation => no deletion. Symlinked parent,
cache entry or hardlink => fail closed before any deletion. Unknown file remains.
Source still below1GiB after reclaim => no restore/reader. Snapshot may become
unavailable after preflight => actual export rechecks rather than reusing admission.

Stop when this bounded repair and actual receipt are recorded. If measured cache
is insufficient, record capacity as an external dependency, without lowering
the reserve or opening another cleanup/model-training cycle.

## Implementation checkpoint before publication

RED7: capacity-before-seed regression and absent cache helper. RED4: absent CLI
and workflow ordering. GREEN74 passed/1 pinned-SQLite skip in0.70s, including
real filesystem/locks, insufficient post-cleanup reserve, and executing actual
workflow Bash to prove cloud restore is skipped on preflight failure. Existing
seed/restore/source-space/owned-reader guards remain green. Final review,
mandatory exact-tree CI and production/export receipts remain pending.

One final read-only review: no Critical/Minor; one Important finding, unknown
files in apt lists were selected. Reproduced RED(6 removals instead of3), fixed
with positive apt index filename allowlist, GREEN76 passed/1 skip in0.67s.
Review's production cache contents/receipt accuracy/acceptance exclusions are
external measurements: baseline logs were read; fresh deploy/export remain
explicit acceptance conditions, not inferred from tests. No second reviewer.
