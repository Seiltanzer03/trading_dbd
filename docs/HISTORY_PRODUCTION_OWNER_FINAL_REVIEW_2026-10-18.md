# Production history owner final review and fix pass

Reviewed base4f0910f238f77e0f9940ac837c877efe555a5f65 through90e257ccd993afb8999e20cd90b61a7d998a613c.
Independent read-only reviewer production_history_owner_final_review_20261018
reported pass complete and98passed/1skipped. Its formal final response hit the
model usage limit after the findings/pass-complete messages; the substantive
messages are preserved here. No native systemd/proc/CI success was claimed.
One consolidated author fix pass follows that review; no repeated review loop.

## Important findings and regressions

1. Pending accepted journal recovery retained capture-mode control and allowed
   read-only descriptors. Every nonterminal core entry now calls strict transfer
   admission. Actual open-reader recovery test RED then GREEN; no truncate.
2. Origin parent death released SH while its child lived. The same lock FD is
   explicitly inherited by nice/ionice/sqlite3_rsync. Real subprocess parent-kill
   test RED then GREEN; EX stays blocked until child closes.
3. Accepted pre-journal resumes reused stale capacity admission. Fresh physical
   reserve is checked before transfer on every such entry; refusal verifies and
   starts the unchanged original. Interrupted accepted-plan regression RED/GREEN.
4. Production accepted foreign-owned files/0755 data. Under EX, narrow only the
   already root-owned data directory to0700; validate source/sidecars/private
   state UID/mode at entry and every fence. Ownership boundary5RED/GREEN tests;
   metadata injection is explicit because local chown is unsupported.
5. Worker resource budget omitted actual full-union admission. Pinned catalog is
   read before freeze. Both cloud images are fully published before releasing
   only the exact owned worker original. Existing full-union reserve is checked
   before handoff, unchanged. Production-scale boundary and changed-copy tests
   RED/GREEN; source/rollback authority remains intact on VPS/Yandex.
6. v12/v13 destination-open failure leaked source after SH release. Acquisition
   is now inside ExitStack immediately after source connect. Actual SQLite plus
   disabled-GC destination-failure regressions RED/GREEN for both helpers.
7. Completed transient unit could unload before resumed union. Durable completed
   result is handled before any unit command; unfinished unloaded units are
   recreated with original pins. Actual complete/committed-resume integration
   and unloaded-unit regression RED/GREEN.
8. Failed EX/install preflight had no result and driver polled10800seconds.
   Polls now inspect actual unit state and refuse promptly. Failed-unit test
   RED/GREEN. Ordinary pre-handoff worker failures request same-epoch safe abort
   instead of waiting5400seconds; abort request test RED/GREEN.
9. Safe original outcomes could never start a new attempt. Explicit new runs
   archive only terminal original metadata under EX with a durable move intent;
   database bytes and legitimate intervening writes are preserved. Pending,
   committed, marked or fence-lost epochs cannot be reset. Actual SQLite new
   writes/new epoch regression RED/GREEN, pending refusal and rename-death
   continuation GREEN.

Final focused profile:274passed/4skipped in9.71seconds. Skips remain native
namespace/pinned-binary proofs required in mandatory CI. Full CI and actual
production cutover have not yet been performed for this fix tree.

## Rulings and deferred minor

- Explicit statuses:read covers the workflow's existing commit-status preflight;
  contents and statuses stay read-only. Reviewer declined to infer actual API
  permission behavior without a live request. Cost if unsuitable: preflight
  refuses before any service/data mutation; no status gate is disabled.
- Existing automatic diagnostic SH scope may contend with deploy's30second EX
  wait. Reviewer graded Minor; deferred as requested, preserving exclusion.
  A real lock refusal can be retried after that diagnostic exits.
- New epoch permission is limited to verified terminal original outcomes. The
  durable reset intent resumes metadata moves after process death; it never
  renews an unfinished epoch. Cost if predicates fail: guarded refusal, no data
  overwrite or original-clock reset.
