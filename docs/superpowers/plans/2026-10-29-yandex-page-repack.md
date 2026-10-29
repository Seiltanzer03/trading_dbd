# Yandex-backed storage recovery without expanding the VPS

User instruction: use the existing Yandex storage more actively; no budget for
larger Timeweb storage; continue autonomously and avoid redundant checks.

Baseline main: 4d35f82110007d78b41635f5da3f55e422a081d0. The separately reviewed
macro transport PR544 stays intact and unmerged until physical admission.

## Diagnosis and boundary

Read-only runs40852449551/40852903850 measured a19,586,531,328-byte live DB,
zero freelist pages,954MB free,120,049 passive/94,244 G1S rows. Existing v2
cold state already retired24,752 rows. Ordinary live snapshot admission correctly
refuses below its unchanged1GiB WAL reserve. Re-running initial unmarked cutover
on a marked/committed source is forbidden. Swaps and other applications stay.

Page samples show4.6–5.6% unused bytes; this is a hypothesis about reclaimable
space, not a proven saving. Never substitute an old cloud backup for current DB.

## Finite execution

1. Implement an OFF-HOST ONLY row-preserving rebuild for explicit1024-byte
   pages. Preserve every table row and SQLite storage class, explicit hidden
   rowids, schemas/indexes/triggers/views, sqlite_sequence, application_id,
   user_version, encoding and existing history pins. Compare ordered typed roots
   and actual FK/integrity on source and output. Refuse unsupported virtual/
   generated/WITHOUT ROWID schemas, missing proof, source change, sidecars,
   capacity/time limits and non-shrinking output. Exclusive output publication;
   failure cleans only owned temporary files. Source remains byte-identical.
2. Test corruption/missing evidence, real source rowid holes, typed cells,
   sequence high-water marks, schema objects, logical-equivalence rejection,
   unchanged source, output collision, reserved capacity and deadline.
3. One final read-only review and mandatory final-tree CI. Then ONE off-host
   measurement on an existing verified private Yandex seed. This is a new
   physical-page question, not a repeated fit or unneeded full-history union.
   No service stop, live DB copy/write, model/provider call, order or ACK.
4. Continue to a separately journaled native repack handoff only if measured
   savings justify it. Use verified current frozen source and cloud original/
   replacement full GETs; independent finite local owner, service fence, all
   writer closure, original reserve and recovery protocol are mandatory.
   No production replacement is authorized by an off-host measurement alone.
5. Preserve headroom by bounding optional background research capture before
   storage exhaustion; retain trade/decision audit and existing exact bars.
   Any capture pause is explicit, never invented observations or ready models.

Ruling: measure a byte/logically lossless page rebuild before implementing a
second destructive protocol. Changing source storage encoding or moving active
SQLite onto S3 would require broader reader/restore changes and is not justified.
This package alone does not prove sustained storage headroom or restored LLM.

## Progress

- Planning and current-source read-only evidence complete.
- Rebuild implemented; initial RED absent module and actual URI ATTACH failure;
  owning64PASS3.08s before final review. Source bytes remain unchanged.
- One independent final review:0Critical/1Important/1Minor. Reproduced Important
  directory-fsync failure leaving an unreceipted destination, then fixed with
  open-inode ownership proof and bounded cleanup. Competing destination survives.
  Added pure-stdlib real v2 projection/state/pin regression from the reviewer.
- Existing cloud metadata run40854562325: all seven slots are STANDARD.
  Newest verified seed is28Oct18:52UTC,19,496,972,288B; gzip3,582,272,853B;
  SHA4b9a8ee4c81b0abc624e1ae8c3ee8a11aea5dfb9178583c3e1fe43bb50f37388,
  slot6. It has the existing verified v2 cold state. Old seed cannot replace
  current live DB. One measurement is a justified new physical question.
- Mandatory fullCI and off-host measurement pending. No production DB changed.
