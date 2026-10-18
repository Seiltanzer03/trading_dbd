# Production history owner implementation plan

> Execute inline with executing-plans: one integrator and one final read-only
> reviewer. User authorization covers the existing infrastructure and release;
> no new design/permission menu is required between these steps.

Goal: close the remaining finite-owner/frozen-capture prerequisite and then
perform only a proven, capacity-admitted initial hot/cold cutover.
Spec: docs/superpowers/specs/2026-10-18-production-history-owner-design.md.
Base: reviewed image helpers8c27291; runtime PR489/main4467853, tree71cc5026.

## Task1 — local durable owner and unchanged core reuse

Files: scripts/history_production_owner.py, scripts/sqlite_history_transaction.py,
scripts/history_service_fence.py, tests/test_history_production_owner.py.
The internal source-freeze phase is scripts/history_frozen_preparation.py,
tested against actual SQLite by tests/test_history_frozen_preparation.py.
The stdlib plan/resource codec is scripts/history_production_plan.py, tested by
tests/test_history_production_plan.py; it validates pins, not writer ownership.
Preserve execute_cutover(root,plan,transport,control)'s disposable-only refusal.
Private executor consumes validated canonical bytes; production wrapper accepts
only canonical VPS paths, root ownership, held maintenance lock and real fence.

- [ ] RED real subprocess cases: preparation timeout restores unchanged original;
  runner disconnect leaves finite owner alive; existing epoch/links/invalid JSON
  refuse; source/guard/writer loss cannot permit transfer; resumed deadlines
  remain original; no original restore after committed new writes.
- [ ] Implement5400second frozen preparation,10800hard unit bound, immutable
  original proof and private handoff. No production CLI may start live work until
  the disposable systemd/control proof and subsequent tasks pass.
- [ ] RED/GREEN production-plan handoff/crash boundaries; preserve existing
  transaction phases,1800+1800budgets and explicit final resource check. Native
  owner verifies whole original/hot cloud streams using reviewed helper pins.
- [ ] Commit owner/core tests; no live invocation and no changed reserve.

## Task2 — actual service control and frozen off-host driver

Files: scripts/production_history_cutover.py (worker driver),
scripts/history_production_control.py (canonical systemd/lock/writer control),
.github/workflows/production-history-cutover.yml,
tests/test_production_history_cutover.py, scripts/history_production_owner_drill.py.
Workflow remains manual, existing production-seiltanzer queue and GH secrets.
Transfer only owned bounded scripts/private input; no credentials in argv/logs.

- [ ] Inventory actual writer/start paths and bind each to persistent guard,
  canonical held maintenance lock and stopped control-group. Refuse unknown
  writers; process sampling alone is not the service exclusion proof.
- [ ] RED/GREEN fixed command/path/owner checks and real disposable systemd drill
  showing local owner survives driver death and partial images cannot start.
- [ ] Implement finite frozen capture without a second VPS DB. Worker matches
  the owner's frozen source digest, builds current v2 image, reuses verified
  catalog partitions, publishes both actual full-read images and hands off pins.
- [ ] Refuse insufficient physical reserve (including sparse source), missing
  v2 capability or stale epoch before truncation. Prove original launch on safe
  preparation abort and rollforward after actual committed new writes.

## Task3 — single final acceptance and authorized production execution

- [ ] One final independent read-only review; fix only concrete blocking defects
  with RED/GREEN regressions. One full mandatory CI on final integration tree.
- [ ] Merge/release and verify exact SHA, installed fence and HTTP prerequisite;
  record research disk rejection honestly instead of requiring impossible green
  capacity-blocked research before repair. Never relax that research gate.
- [ ] Run finite production owner only after all prior proofs pass. Record fresh
  actual source/image/cloud pins, real reserve, watermarks and trade/ACK identity.
  Failure before commit restores original only while fencing remains intact;
  failure after commit never downloads old over new writes.
- [ ] Complete actual post-cutover reserve/HTTP/backup/full-union/research/AI/EDE
  acceptance. Preserve explicit family/model/source limitations. Publish exact
  receipts and close Package B only with measured durable headroom.

Pre-flight: Task1 private core interface is consumed only by Task2 owner control;
Task2 uses existing image helper receipts/open_image(kind), not model/readiness
authority. Original v1 physical proofs, full current union and public disposable
API compatibility remain mandatory. No source migration has been performed.

Task1 foundation checkpoint: the public disposable production-path refusal is
unchanged; the existing transfer body is privately reusable under an already
owned lock. Three actual-SQLite precommit resource/fence cases were RED then
GREEN, proving exact original restoration on ordinary reserve refusal and no
rollback after fence loss. Sixteen production-pin/physical-capacity cases were
RED before the codec existed, then GREEN. Sparse sources use allocated blocks,
not logical size; post-transfer admission exactly matches1GiB+32MiB. Combined
plan/core/transport/publication profile:91passed in3.54s. This foundation has not
had final owner-package review/CI and does not provide a production invocation.

Ruling: isolate strict public production pins and filesystem measurements in a
stdlib codec — keeps the disposable validator and future real owner ownership
proof separate; no allow_production flag or admission override is added.

Observed release result: deploy39116843707/job117644251947 delivered4467853
and reached routes at572.119seconds. Research acceptance immediately refused
RESEARCH_DISK_ADMISSION_BLOCKED; its cooperative gate was released. Remaining
readiness/smoke/source stages were skipped and overall deploy failed. Main CI
39116516369 passed. HTTP startup is a delivery fact, not full release acceptance.

Frozen-source phase development: initial6cases were RED before implementation.
The source SHA/size/SQLite check, persistent epoch/guard, unchanged deadline,
safe original launch on expiry and terminal guard/source loss now pass against
actual SQLite. A scan-budget regression was RED on two full integrity scans;
the phase now performs one integrity/hash pass per invocation (and no extra
repeat on same-invocation abort). This is an internal phase, not the native
systemd owner/driver. No production freeze, source capture or transfer occurred.

Task2 reader inventory found direct SQLite openers outside the maintenance
flock. The shared stdlib history_live_read_guard now holds a real shared lock
for direct production readers, refusing active/pending/ambiguous epochs before
opening SQLite. Embedded native export releases it after SQLite close, before
compression/SSH output. Production EDE inventory retains it through its actual
read transaction and releases it even if initialization fails. That constructor
failure was reproduced RED with a real SQLite BEGIN error, then GREEN. Initial
guard RED10; integrated reader/freeze/plan/core/image profile:174passed in4.60s.
Remaining direct read paths still require closure before any production
invocation; this is not a claim of complete process exclusion.

Explicit remaining source-open inventory: export_mathematical_edge_sources,
export_unified_edge_reviews, production_ede_transition_audit, v12/v13 snapshot
source helpers, historical_conditional_audit, run_universal_outcome_audit,
standalone origin rsync and embedded network/management diagnostics. Audit or
guard each actual live-source path; immutable worker-only paths keep their role.
Do not enable the native production entry while this inventory remains open.

Latest observed public health: run39131157759/job117686432279 at13:21:11UTC,
expected main4467853, first /api/state probe returnedHTTP200. This is distinct
from the disk-blocked full research acceptance. No extra deploy was triggered.
