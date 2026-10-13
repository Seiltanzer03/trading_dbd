# Existing Yandex archive transport and capacity recovery

User13.10.2026 explicitly authorized the prepared read-only diagnostic and use
of the already-configured Yandex Object Storage for continuation. Baseline
fd9fe3149ba7e9ee6ae5a4fa74eec10f4538345e remains the accepted release.

Diagnostic38393263520:785MiB free, authoritative DB18226864128bytes;
old runner2.322.0 installer119302750bytes; EDE v13 candidate registry377496272bytes.
Unrelated bot environments remain outside scope. OOM killed the terminal at
11:55:36UTC; systemd restarted it. Subsequent public snapshot/status HTTP200
confirms recovery, not a proven memory fix. No speculative OOM cause is claimed.

## Bounded change

- Existing offhost EDE fetch/publish preserves the whole opaque registry byte
  sequence, identities and embedded clocks. Runner compresses/uploads to the
  existing private trading-dbd-backups-2026 bucket using existing YC_STORAGE
  Actions secrets. Whole-object gzip/raw size and SHA256 and manifest GET are
  checked before any production publication or byte retirement.
- VPS keeps a compact gzip and receipt; failed/interrupted local cache can
  restore the authoritative receipt's immutable cloud object. Legacy plain
  JSONL remains readable. Newer installed history must be an exact byte prefix;
  mismatch is an explicit conflict, never a stale/empty replacement.
- Workflow concurrency owns snapshot/fetch/research/publication together.
  Explicit archive operations use the same concurrency group. Ordinary worker
  and VPS staging preserves the unchanged1GiB reserve. Initial capacity recovery
  uses zero large VPS staging writes: a known retired public installer and3closed
  rotated log files can be moved to fully verified private objects; active logs,
  DB/WAL, observations, broker state and unrelated applications are excluded.
- Installer/log retirement has fixed identities, original hash/size/unchanged
  checks, and closed-file proof for logs. Receipts and private objects retain the
  complete original bytes. No research authority or source/model gate changes.
- Registry migration is permitted only after compatible code is installed.
  Cloud retention uses a separate research/archive prefix, never overwrites
  existing seven SQLite backup slots. No new cloud resource or credentials.

## Review / verification

One independent reviewer found4Important risks: newer retained plaintext hidden
by receipt switch, interrupted gzip/receipt pair, staging without reserve,
and audit-only concurrency. One fix pass adds prefix checks before receipt
publication, verified cloud cache fallback, bounded reserve checks/zero-VPS-stage
initial recovery, and whole-workflow serialization. Regressions cover all four.
Minor deferred: filesystem mtimes are not preserved; embedded source/event
timestamps remain exact bytes. This metadata is not used to invent forecast age.
Owning local profile and exact final CI/release/operations receipts must be
recorded before claiming production recovery. No repeated fitting/provider calls.

## Actual acceptance and continuation receipts — 2026-10-13

PR463 final22d8da7b/tree8dbc01df matched mergecandidate7db3e4e; PR CI38397415904
and mainCI38398238486 passed3932tests, pinnedSQLite/frontend/WebKit. Accepted
production b96cca445056297f43fd8978d2f4751d6cbff44c, deploy38398804577:
delivery/readiness/functional/public/orchestration success, all7contexts green.

Capacity operation38397489166 preserved303435330original bytes in private
Yandex Storage. Registry migration38399091768 preserved377496272bytes;
SHA256 d1503e1f438a9e186306255af9ae11412932dd220106267073f4e8ef18290b15.
Every object was fully restored/verified before retiring its local original.
Combined680931602bytes preserved. Measured free1468628992bytes after migration.
DB/WAL/active logs and embedded source/event timestamps were untouched.

Exact-SHA math38400999540 published385800bytes:25fitted models (21current and
4retained unchanged option fits),14historically supported family combinations.
Native export1957rows/1053option; EURUSD/USDCAD/XAG/XAU remain explicitly
read-budget-incomplete. Sources38401003724 published550529bytes for13instruments,
models_produced=0. FOMC38401007534 published270507bytes; this is one delivery,
not proof of continuous600s cadence or newly trained event models.

Inventory38401107320 succeeded:83956observations/23725resolutions,96features,
65DATA_READY/27insufficient independent evidence/2G1M-only/2quality-only. This is
coverage, not forecast performance. Automatically continued EDE38404251655;
its snapshot/research/publication remains in progress at this checkpoint.

Read-only diagnostic38404485915 confirms exact installedSHA, service running,
NRestarts0 and no new OOM since deploy. Runner/local/public-self api/state200;
localhost0.004763s. Separate FOMC latest timed out15s while macro/status0.079s:
recorded for targeted investigation, not a claimed fix or terminal outage.
No full-eight-family, live-LLM, empirical profit or new complete-backup claim.
