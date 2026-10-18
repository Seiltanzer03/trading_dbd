# Finite production history owner

Continue the existing durable-history and runtime-reader plans. User-authorized
autonomous execution uses only the existing VPS, GitHub and Yandex bucket.
This specification is a prerequisite, not evidence that production was frozen
or that an image/capacity proof has already succeeded.

## Fixed architecture and authority

Production root25GB cannot stage original19GB plus hot image. Use the reviewed
in-place transaction and content-addressed cloud originals, not an atomic-swap
claim. A finite local systemd process survives SSH/GitHub runner loss, holds
/opt/seiltanzer/.deployment-git-maintenance.lock, and owns the persistent start
guard for the entire frozen epoch. Existing shared workflow queue remains
production-seiltanzer. No new cloud resource, recurring scheduler or S3 mount.

Reader PR489/main4467853 has green mandatory CI. Real delivery/startup, installed
external fence and exact checked-out SHA are prerequisites. Existing research
acceptance is explicitly disk-blocked; this cannot require an impossible green
disk-gated research run before its capacity repair, nor claim full acceptance
from startup alone. Real research/readiness/smoke must pass after capacity repair.

Every known SQLite writer/start path must be covered by the held host lock,
workflow queue and persistent unit fence. Verify the actual systemd condition,
stopped service/control-group and no existing DB handles/sidecars. Process scans
are supplementary; they cannot alone establish continuous writer exclusion.
Refuse live operation if the inventory finds an uncovered writer. No order,
trade, ACK, original source clock, threshold or model authority may be fabricated.

## Frozen preparation before any destructive write

Use a canonical0700-owned data root and private0600 regular bounded files. Reject
links, ambiguous JSON, unknown fields, existing unfinished epochs and changed
ownership. Refuse a second migration of an already marked database in this
initial owner; a recurring catalog/retention policy is a separate follow-up.

Install the active guard before stopping service, then stop the whole service
control-group. Only after actual writers close, checkpoint WAL with busy=0 and
verify clean SQLite closure. Do not force-remove any WAL/SHM/journal. Failed
checkpoint/fencing retains the guard for reconciliation; no copy/truncation.

Persist cutover.preparation.json with frozen run id, expected app SHA, source
inode/size/physical allocation and full source SHA/quick-check proof. It has no
transport URL. The active guard blocks all prestart steps; the transaction
journal does not yet exist because no hot plan has been accepted.

Preparation has5400seconds from the original frozen epoch, never renewed by a
heartbeat or resumed call. A hard local RuntimeMaxSec10800 bounds the whole unit.
The owner owns timeouts independently of the runner. While preparing, only read
the original. On timeout/ordinary preparation failure, verify the unchanged
original/fence, release the guard and launch the original. Lost writer/guard
exclusion is terminal FENCE_LOST_GUARDED and requires data reconciliation.

The worker checks its actual available capacity, reads this frozen source once
without creating another VPS database, and matches the owner's whole byte hash.
This is a separate frozen/offline capture, not a bypass of live snapshot WAL
headroom. Restore no unchanged19GB cloud snapshot merely as busy work. Existing
439verified catalog partitions may be reused; current builder keeps post-catalog
rows and all proven runtime dependencies and validates exact cold equality.

Build current v2 hot image off-host once. Publish exact original and hot using
publish_history_image; full HEAD+GET proofs are required, not boundary sampling.
Keys are research/sqlite-cutover/v1/<run>/<kind>/<raw-sha>/<gzip-sha>.sqlite3.gz.
Neither original rollback authority nor the plan points at a rotating daily slot.
Hold no unbounded read transaction on the live source; it remains stopped here.

## Immutable plan and private transport handoff

The public canonical production plan binds run, app SHA, original/hot byte pins,
both compressed/object pins, full-read receipts and exact v2 hot storage manifest.
Signed URLs are separate0600 input and never part of plan/journal/report/hash, so
fresh URLs can resume the same immutable plan. Parse at most64KiB, no duplicate
members. Match the frozen original pins before accepting any request.

The local owner independently reads both complete cloud streams against the
plan before allowing truncation. A claimed verified=true field alone is not
authority. Validate actual hot runtime capability and manifest offline first;
verify it again against the received hot SQLite before commit. No credential
enters argv, unit descriptions, public receipts, logs or provider exception text.

Before handoff, estimate available reserve using actual source allocated blocks
(capped at logical size), hot rounded logical size and current filesystem free
blocks. Do not assume the original was nonsparse. Require the unchanged existing
1GiB+32MiB admission reserve; reject a physically insufficient hot image before
truncating the original. Recheck actual free bytes after verified hot transfer,
before COMMITTED; a capacity failure with intact fencing restores the original.

## Reuse the transaction core without opening its disposable API

Keep execute_cutover's current production-root refusal and disposable validator.
Extract only its existing guarded transaction body into a private executor
consuming already validated canonical plan bytes and an explicitly owned lock.
The production owner supplies the separate strict production plan validator and
canonical root/maintenance-lock/fence proof. No allow_production boolean or
automatic root fallback is added to the disposable entry point.

Keep existing journal contract/phase semantics and canonical epoch pins. The
preparation guard transitions to the final plan epoch only while the source is
unchanged, lock/fence are held and no destructive journal phase exists. Crash
before the first transaction journal leaves the original intact; restart reads
preparation and final plan proofs and refuses ambiguous handoff. Once a final
transaction journal exists, only that exact plan may recover or finish it.

Hot transfer and original recovery each retain their existing1800second budget;
they do not consume the same exhausted timeout. Persistent guards survive hard
kill/reboot; same-epoch recovery is explicit, finite and refuses changed pins.
No retry overwrites data after FENCE_LOST_GUARDED. COMMITTED is fsynced before
guard release; after that no old original download/restoration is permitted.
Launch failure is rollforward. Reserve up to900seconds for actual service/HTTP
startup; do not infer health from process spawn or status alone.

## Acceptance and finite stopping point

Real subprocess/systemd disposable proof must kill the SSH/runner-facing process
while local owner continues; cover preparation expiry, failed source capture,
malformed private handoff, precommit transfer interruption, failed restoration,
guard/writer loss, process death at handoff, capacity refusal and postcommit new
writes. Source bytes are exact on safe abort; partial DB never starts. Preserve
existing real-systemd fence and strict v1/v2/full-union tests.

One final independent review and full mandatory CI precede production invocation.
The actual run must record exact source/hot/cloud identities, reclaimed physical
bytes, durable reserve, rowid watermarks, original trade/ACK integrity and HTTP.
After commit, complete backup/full research reader and exact-SHA AI/EDE/source
acceptance without a second unchanged fit. Missing scientific evidence remains
explicit. No production operation is considered complete from this spec alone.
