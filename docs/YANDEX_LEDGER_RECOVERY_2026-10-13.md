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
