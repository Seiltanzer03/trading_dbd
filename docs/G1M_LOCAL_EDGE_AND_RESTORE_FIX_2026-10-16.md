# Local-edge HTTP and verified-copy repair — 16.10.2026

Prepared continuation of the original terminal specification and handoff.
This checkpoint is not a production acceptance receipt or a completed migration.

## Confirmed failures and owning fixes

1. Accepted49174b deploy38897278150 passed readiness, functional/AI smoke and
   public HTTP. Its separate downstream local-edge38899642288 failed:3760.59ms
   versus the unchanged3000ms GET gate. The existing v3 read optimization still
   recomputed complete local management evidence on every request.
2. PR478 merged asf90cb4a after green mandatory PR CI4187tests/WebKit/pinnedSQLite.
   Automatic mainCI38939154343 then failed the mutation-during-capacity regression
   (4186other tests passed). Its deploy was correctly skipped; backup recovery
   also refused non-green SHA and did not change production. This is not hidden
   as a successful release or treated by blind retries.

### Nonblocking complete local-edge presentation

An instance-local cache installs without SQLite work at startup. The existing
local worker materializes windows, resolves outcomes and builds the unchanged
complete report, including active-edge attribution. Generation/active-operation
guards refuse old out-of-order publication. HTTP returns preencoded bytes without
SQLite, report recomputation, recursive encoding or a research/report lock.

Report capture time/age, dirty/building state and sanitized error class accompany
the cached evidence. Before the first report, unavailable counts are omitted,
not fabricated as zero. Failed refresh preserves previous evidence as dirty and
remains a worker error. UI shows previous-snapshot age and update/failure state.
Statistics, full prospective/descriptive cohort, maturity, decision authority,
automatic activation and execution contracts are not changed. No new scheduler,
source, model, order or3000ms budget adjustment is introduced.

### Actual byte binding after restore verification

Same-size changes can occur within one filesystem clock tick. Final verification
now rereads bounded actual restored bytes and compares full SHA256/size before
issuing the local continuation token, retaining fd/stat checks around it. Both
SQLite UPDATE and raw payload changes with equal mocked timestamps reproduced
RED before this fix. This is one extra off-host read per restore, not one whole
source hash per archive fragment. No19GiB restore was repeated for this diagnosis.

The local worker copy must remain isolated/quiescent. This check is not a claim
of protection against a hostile writer racing during/after the hash read, nor a
portable production retirement authority. Full backup, snapshot drill and union
proofs remain separate contracts with their original false authority flags.

## Verification and review

- New local-edge tests8RED→GREEN; UI states4RED→GREEN; coarse-clock byte
  mutations2RED→GREEN. Integrated focused profile118passed/5.57s, one existing
  Starlette/httpx deprecation warning. Actual report math equality checked on
  both prospective and older descriptive windows; no cohort limit added.
- One independent read-only review: no Critical/Important findings; its focused
  rerun67passed. Production latency, full mandatory CI, real cloud migration and
  hostile concurrent mutation explicitly remain outside that evidence.
- Deferred minor: first cold-cache report failure has explicit API error/state,
  but the UI uses the generic not-yet-built message instead of a distinct error.
  It does not display fabricated metrics; no minor-polish loop was opened.

Ruling: repair the observed local-edge downstream blocker before extending the
archive producer; it prevents the existing inventory chain from continuing.
This is the original handoff's performance task, not an additional feature.

## Remaining terminal work

Yandex remains the existing private archive. The64-row typed/SQL proof is real
but partial: full catalog production, eligibility/dependency closure, all
historical readers/writers and physical rowid-preserving cutover/rollback remain
unproven. No source row deletion or disk-space recovery is claimed. EDE is not
restarted before actual source/WAL reserve returns. Current observed family
forecasts/LLM contribution cannot be inferred from these infrastructure tests.
