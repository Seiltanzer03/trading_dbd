# Frozen history capture failure and bounded transport correction

Production baseline: main b3d1687e6654d13c1246092254da0cdd6d626b66,
tree b52c998a7446295c17d2ecca73eb482d3edd0c6b, PR490.

## Actual overnight result

Manual cutover39170527310/job117805166185 ran22:16:33–22:53:26UTC and
failed before producing any hot-build/accepted-plan/full-union artifact.
Read-only diagnostic39202257215/job117897542975 verified at05:00:07UTC:
ABORTED_ORIGINAL, service_started=true, WORKER_REQUESTED_SAFE_ABORT;
no active guard, journal, accepted plan, request or capacity receipt.
Original frozen source19315957760bytes/SHAfe2d3300ce31c0cf96ec31df1c3d4ad254ba12133db4b99825d351e9412e650f.
Current source19320233984bytes; live writes have resumed. HTTP200, root free
604913664bytes. No retirement or disk-reclamation success is claimed.

Automatic backup recovery39171395121 refused HISTORY_CUTOVER_ACTIVE_MUTATION_REFUSED
while the finite owner was still recovering; it did not race the transfer.
Scheduled health39184957334 subsequently returnedHTTP200. Automatic deploy39170408478
had timed out waiting for HTTP; its first storage preparation took450.005s and
its schema-backup stage began474.785s into startup. That is not release acceptance.

## Reproduced cause and bounded fix

The failed worker discarded its original exception. Its elapsed time matches
frozen preparation followed by the1800s capture limit; exact exception evidence
cannot be recovered from that runner. Independently, a real Paramiko SSH/SFTP
regression reproduced serialized32KiB reads (peak outstanding=1), which make a
19GB capture latency-bound. The test failed before this correction.

Capture now prefetches only4MiB windows with at most32 outstanding requests,
using the already pinned file size. No whole-file read-ahead allocation. Exact
wire size/SHA, actual EOF, SQLite integrity, private file ownership, original
1800s capture limit and finite native-owner clocks remain unchanged. Real SFTP
capture crosses multiple windows and preserves exact bytes with bounded parallel
requests. The mandatory CI test job explicitly installs the protocol proof dependency.

Public stage/byte progress and a redacted failure artifact replace the previous
opaque worker failure. Raw provider exception messages and signed URLs are never
published. A failed diagnostic write cannot prevent the existing safe abort;
that regression failed before its correction. Provider-message redaction and
complete driver/owner/restore integration are also covered.

Focused profile:120passed/1skipped in10.70s. Native process inventory remains a
mandatory CI proof. One independent review, final CI, release and a fresh admitted
production attempt remain pending. No new production attempt was requested.

## Independent scoped final review

Reviewer frozen_capture_fix_final_review_20261019: no Critical/Important findings
for1c80e9d..8d1a051. Focused15passed2.62s; additional real-SFTP probes rejected
extra bytes, premature EOF, wrong SHA and mid-transfer deadline, removed each
owned failed output, and observed peak32 requests. No production work performed.

Deferred minors (no extra polishing pass):
1. Paramiko prefetch cancellation can leave its bounded daemon thread alive until
   this one-shot worker exits, and can emit Socket-is-closed. The same SFTP
   session remains usable and the safe-abort test passes. No source mutation,
   recovery or integrity failure was found. A future dependency lifecycle fix
   needs explicit cancellation coverage.
2. Preflight failures before the guarded run block (connection/SHA/catalog/SFTP
   initialization) still have generic diagnostics. Frozen capture/build/handoff
   failures have the new redacted evidence; no preflight safety gate is relaxed.

Review limited to local Paramiko5.1.0; mandatory CI covers the final tree and
native proofs. Actual production throughput/retirement remains unverified.

CI scope correction: adding the optional SSH library to global dev dependencies
triggered an unrelated fresh ML audit through pyproject's path filter. Keep
pyproject unchanged and install Paramiko explicitly in the mandatory test job;
local runs without this operational dependency explicitly skip the real-SFTP
module. CI installs it and must exercise the protocol proof. No research gate
or runtime dependency was changed. The superseded CI tree is not release evidence.
