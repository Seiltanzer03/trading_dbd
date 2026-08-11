# Phase G.1E-0 + G.1E — Durable Intelligence

## Goal

Seiltanzer's accumulated trade/research history is now treated as an asset rather
than process memory. `trades.db` is the local source of truth; RAM is cache only.
The Intelligence Lab is a presentation layer over authoritative G.1A/B/B.1/C
math and does not create a second probability implementation.

## G.1E-0 durability

Contracts:

- `seiltanzer-storage-v1`
- `seiltanzer-backup-v1`
- `seiltanzer-recovery-v1`
- `seiltanzer-backup-retention-v1`

At process start an existing database is snapshotted with SQLite's online backup
API **before Engine construction/schema migrations**. Every backup is opened,
`PRAGMA integrity_check` is run, critical table counts are recorded, and SHA256 is
stored in an adjacent immutable manifest file. A file is not considered a backup
until verification succeeds.

Live trade/research writer connections are configured for WAL, foreign keys,
30-second busy timeout and `synchronous=FULL`.

A persistent startup marker distinguishes `CLEAN` and `UNCLEAN` previous shutdowns.
SQLite WAL recovery handles committed pages; a deterministic reconciliation pass
also repairs the specific cross-ledger crash gap where a trade close was committed
but its terminal position-management event was not.

Local verified snapshots default to every 15 minutes. An optional provider-neutral
off-host filesystem target is configured through `SEILTANZER_OFFHOST_BACKUP_DIR`
and defaults to hourly snapshots. This target can be a separately mounted S3/rclone/
NFS-backed path; credentials remain outside the repository. Until configured the
health state intentionally reports `LOCAL_BACKUP_ONLY` rather than pretending VPS
disaster recovery exists.

Read-only APIs:

- `/api/system/storage/status`
- `/api/system/storage/backups`
- `/api/system/storage/integrity`

Operator CLI:

```bash
python -m seiltanzer.storage_cli --data-dir /path/to/data status
python -m seiltanzer.storage_cli --data-dir /path/to/data backup
python -m seiltanzer.storage_cli verify BACKUP.sqlite3 BACKUP.manifest.json
# stop the service before restore
python -m seiltanzer.storage_cli --data-dir /path/to/data restore BACKUP.sqlite3 BACKUP.manifest.json
```

Restore verifies manifest contract, SHA256 and SQLite integrity, preserves the
previous database by default, then verifies the restored database again.

## G.1E Intelligence Lab

UI: `/intelligence`

The page answers human questions first:

- How many Q forecasts have been attempted/captured/resolved?
- How much clean/effective evidence exists?
- Why are observations rejected?
- Can Platt/Beta/Isotonic fit yet?
- Is G.1D OOS validation ready?
- Which prospective Q forecasts are still waiting for outcome?
- What did recently resolved Q forecasts actually experience?
- Is accumulated data protected?

Backend APIs:

- `/api/research/g1/intelligence/status`
- `/api/research/g1/intelligence/pipeline`
- `/api/research/g1/intelligence/forecast-quality`
- `/api/research/g1/intelligence/calibration`
- `/api/research/g1/intelligence/pending`
- `/api/research/g1/intelligence/resolved`
- `/api/research/g1/intelligence/history`

The cockpit does not calculate Q/P/Brier/PIT in JavaScript. It displays values
from the authoritative research runtimes. Immutable six-hour snapshots record the
maturation of evidence without rewriting historical state.

## Authority boundary

G.1E never changes the trading decision contract:

- `production_authority=false`
- `production_replacement_allowed=false`
- `promotion_allowed=false`
- `physical_probability_published=false`
- shadow Q→P is not used by AI Verdict or real trade management.

G.1-M remains a separate next stage.
