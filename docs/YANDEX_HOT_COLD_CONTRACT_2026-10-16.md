# Yandex hot/cold history: bounded engineering contract, 2026-10-16

Design/reader inventory only; not a production migration receipt. Existing
bucket trading-dbd-backups-2026, endpoint storage.yandexcloud.net, existing
GitHub storage credentials. No new provider, server, scheduler or S3 filesystem.
Original specification and handoff unchanged.

## Capacity evidence and decision

Morning diagnostics38857059618/38857229902 measured trades.db19,071,377,408B,
WAL12,442,432B and source free945,197,056B (901MiB). Required reserve remains
1,073,741,824B; deficit128,544,768B. These are timestamped observations, not a
current live capacity claim. Existing source audit38818919626 stopped before
export for this gate. Existing13 closed-log archives already recovered138MB,
which subsequent DB growth consumed. Active runner2.337.0 and dependencies
are not disposable. Repetitive cache cleanup cannot establish capacity.

Total database size does not establish eligible cold bytes. Need the existing
immutable worker snapshot's sqlite-offhost-capacity-v1 report (collect_capacity
in scripts/restore_verified_snapshot.py), per-table allocation and page/free
counts before choosing a payload. If no retained report is available, measure
one existing verified off-host copy with the existing180s bound. Do not trigger
another live19GB reader or full restore merely to repeat known disk size.
No reclaimable-byte or retention-duration promise is justified yet.

## Why a table delete or metadata-only stub is unsafe

The source has197 literal table-reference lines for passive observation/path/bar
and G1S observations in the current code (inventory retained with this brief).
They include direct SQL joins, resolution updates, full frozen JSON readers and
source-rowid watermarks. A cloud receipt alone is not a compatible replacement.

| Owner/consumer | Required preservation before cold eligibility |
| --- | --- |
| passive_learning, measurement_path_runtime/refinement | Pending target/outcome resolution and virtual-position references; complete required path/bar interval; exact original forecast/features JSON |
| g1_dataset_runtime/refinement, g1_shadow_runtime, research_scalability | Dataset membership/cut closure, source observation joins, forecast and outcome hashes, historical counts and calibration cohorts |
| g1_short_horizon_runtime | Explicit original source_rowid and monotonically progressing watermarks; source observation identity, frozen features/forecast, hashes and resolution joins |
| G1S feature/metrics/calibration/continuous/champion/status/analog readers | T0 hashes, all labels, path windows, training cuts, predictions, dependency groups, materialized watermarks and counts |
| LLM prospective journal, G1M context/attribution/trade-link readers | Historical observation/action/outcome linkage and attribution; no inferred broker execution |
| scripts/export_native_family_history.py, EDE prospective adapters | Existing direct g1s/passive joins and raw projected JSON; same cohort/fingerprint/clocks on restored union |
| storage_runtime/refinement/schema_registry_integrity, restore/readiness | Complete logical critical-table registry/counts and both hot/cold identities, not merely smaller hot counts |

SQLite TEXT primary keys do not alias rowid here. Rebuilding tables without
explicit original rowids can silently change ingestion progress. SQL DELETE
usually frees internal pages without returning file allocation to the OS.
A full local rebuild also needs capacity and a consistent cutover; it cannot
be assumed safe on the nearly full source disk. No deletion/rebuild is part
of this design-only stage.

## Narrow archive unit and eligibility

Start with a dependency-closed cohort of resolved immutable observations and
exact frozen payloads, not a time-based blanket prune. Keep unresolved/pending
rows, current trade/management/ACK state and anything an active materializer or
open cohort still needs hot. Resolution alone is necessary, not sufficient:
measurement_path_refinement can still update outcome_json; all applicable
resolution, correction and enrichment owners must declare the candidate closed.
The selection manifest must enumerate every table/PK/original rowid, exact
SQLite value type and payload bytes, source clocks, schema/contract versions,
linked cut/model/prediction/resolution IDs, deterministic order and dependencies.
If any closure reader/writer is unknown, eligibility is false.

Preserve exact stored JSON text including whitespace (do not parse/reserialize
as archival bytes), integers/floats/nulls/blob types and original timestamps.
Use a versioned length-framed typed representation with raw and compressed
SHA256, exact byte/count totals and explicit schema; size-limited chunks.
Hash every payload and manifest. Separate captured/source/received/published
clocks from archive/restore clocks. Retention adds no evidence or authority.

Existing ede_ledger_archive.py proves opaque-file gzip/full-GET/restore patterns;
it is not yet a SQLite-row archive implementation and must not be claimed as
one. Existing verified daily SQLite backups remain recovery sources, distinct
from an independently queryable cold-history contract.

## Reader contract before retirement

Keep active SQLite local. Hot operations remain bounded and avoid synchronous
cloud reads on HTTP/AI/quote paths. Historical jobs hydrate verified needed
partitions on the existing off-host worker, then query a deterministic union
with hot input. Every applicable SQL reader above must either consume this
complete union or have a proven current hot dependency boundary. Exposing a
single resolver while direct SQL readers remain is insufficient. Duplicate
identities are accepted only with identical typed bytes/hashes; conflicts or
missing partitions refuse the historical job, never silently shrink cohorts.
Current status must distinguish hot counts from total verified history counts.

## Finite implementation gates

1. Reuse one off-host allocation report; identify one actually large eligible
   dependency-closed unit and quantify net OS-level capacity after the proposed
   cutover. Stop with explicit no-eligible-unit if none can be proved.
2. Disposable copy: archive -> private Yandex upload -> full compressed/raw
   GET hash verification -> full typed restore -> table/rowid/hash/count and
   consumer-equivalence checks. Inject partial upload, missing chunk, corrupted
   manifest/hash, process interruption and conflicting duplicate identities.
3. Integrate all affected readers/writers and backup/restore/count manifests.
   Preserve current unarchived resolution and materialization behavior; compare
   all consumer cohorts, hashes, outputs and eligibility with the original copy.
4. Disposable source-copy cutover/rollback drill with original rowids, actual
   free-space result and bounded I/O/RAM. Prove pre-swap writes are neither lost
   nor replayed, all SQLite handles switch safely, and rollback is available.
5. Only after those receipts and normal review/CI: concrete production cutover
   with existing resource/acceptance guards and measured capacity. Live row
   retirement and any service stop/swap require their separately reviewed exact
   operation; this design is not permission for an unproved destructive action.
6. Observe headroom against actual growth across ordinary collection/backup
   cycles, API/worker health and a newly admitted source preflight. Resume only
   the blocked EDE stage with valid current inputs; no gate reduction/retraining
   of unchanged data. Long-term reserve requires measured growth, not a single
   successful free-space sample.

## Outcome of this design stage

No production rows, WAL, service, runner or source clocks changed. Concrete next
input: retained off-host per-table allocation report and a closed-cohort map.
The dominant correctness risk is reader/dependency closure and rowid-preserving
cutover, not compression or obtaining Yandex credentials. Full migration is a
separate architecture package; this AI report fix does not claim it complete.
