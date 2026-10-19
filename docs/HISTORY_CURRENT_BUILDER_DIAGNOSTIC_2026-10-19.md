# Current history builder: measured diagnostic boundary

Production baseline `bcb22bb5acbc33ecd3965cae620be693d9c172ba`,
tree `63af609a0a23c5599955c3e4bdf8c7f65b180955`.

## Observed, not inferred

Cutover39207831313 captured verified current source19,320,905,728bytes,
SHA`a00a54b5f71ce53673d69808e38bc915a8d1404f01c8f22bfdface7977e9e2f4`.
It failed in `build_hot` after approximately881s with `OperationalError`.
No accepted plan, image publication, handoff or local retirement occurred.
The worker's generic failure record discarded SQLite code and owned frames;
the exact production failure cannot be reconstructed from that record.

Read-only39206973385 attempt5/job118026604702 at11:09:19UTC verified the
original safely returned: exact applicationSHA, `ABORTED_ORIGINAL`,
service_startedtrue, HTTP200, free593,723,392bytes (below admission reserve).
Read-only backup inventory39240925407 checked seven existing daily-slot
manifests; none identifies the failed current capture. No current database
was transferred, no cloud object was written and no production epoch started.

## Historical full-scale current-builder proof

Cloud-only39240101525/job118018568884 executed exact baseline code against
the existing pinned Yandex source:
`d6e8c423f21585f68cd28b0d837eb08c944d2b273bbba59ad963927e33434535`.
Catalog pin:
`7b78d25d13f249f1ab967828675419a6656fe6b69787e5b78644076a54411a63`.

- Source verified10:57:47UTC:18,784,505,856bytes.
- All439 catalog partitions processed, last began11:11:07UTC.
- `BUILT`11:24:45UTC:17,293,832,192-byte hot image,24,001 retired rows.
- Measured reduction1,490,673,664bytes, not the older approximately5.25GB
  estimate. Current dependency/runtime retention stays mandatory.
- Full job elapsed2085.837s includes source restore; builder itself remained
  within its unchanged1800s budget. Per-partition30s limits unchanged.
- No production connection, cloud writes, handoff or retirement. Public
  diagnostic artifact12183225600 contains only redacted structural evidence.

Initial diagnostic39237776723 was rejected before jobs for invalid job-level
runner context. First executed39237996965 restored the source but refused
before partitions because its local receipt lacked a catalog binding. Both
were diagnostic setup errors, not reproductions of the production exception.
The corrected binding was RED/GREEN checked against a real small builder
fixture before the successful full-scale invocation.

The historical pass does **not** reproduce or fix the current production
failure, prove current reader equivalence/full union, establish current
reclaimable bytes, or authorize a blind retry. No second production cutover
was started after the failed39207831313 epoch.

## Narrow correction: preserve safe failure evidence

The worker now retains up to four exception records, each with at most twelve
owned-code frames (basename/function/line only), plus canonical SQLite integer
code/name when the exception really is `sqlite3.Error`. Traversal is bounded,
including cycles. Fake provider SQLite attributes and malformed SQLite names
are ignored. Messages, SQL, locals, absolute paths and provider URLs are absent.
Evidence collection/write failures cannot suppress the existing safe abort.

The original fixed public failure code and exception type remain; no timeout,
source pin, admission, retirement, ACK, risk or execution rule changes.
This corrects lost diagnostics, **not** the unproven builder root cause.

Two real-SQLite complete-driver regressions first failed on missing
`error_chain`; direct and wrapped SQLite errors now preserve code1/SQLITE_ERROR
without the secret table/message, and still request safe abort without handoff.
Two bounds/redaction regressions first failed on the missing collector.
Initial capture/current-builder profile:45passed in6.55s.

One independent final review found one Important: identifier-shaped arbitrary
SQLite names and inconsistent name/code pairs could pass the first filter.
Three regressions observed RED; corrected by trusted SQLite base/extended
result-name/code validation. Genuine SQLITE_CONSTRAINT_UNIQUE/2067 retained.
Final focused profile:49passed in5.30s. No second review/fix loop.

Deferred minor: committed tests do not directly inject collector failure or
the twelve-frame cap. Reviewer independently injected collector failure with
and without simultaneous receipt-write failure (2passed), confirming abort
remains intact. Existing receipt-write, redaction and bounded-chain regressions
remain. This is a coverage limitation, not a demonstrated operational failure.

Review rulings: current production root cause remains outside this narrow
diagnostic correction and unproven; external run receipts/prior RED outputs
were verified by the integrator; the final focused profile was run by the
integrator. Mandatory final-tree CI/deployment/retry eligibility are not
inferred from the review. No blind production retry follows this correction.
# Approved current-source capture (2026-10-19)

The user approved a temporary diagnostic maintenance window. The optional
`--diagnostic` mode of the existing production history driver requires a fresh
epoch and the same deployed, reviewed green revision. It uses the unchanged
native owner, fence, clocks, exact byte capture and SQLite verification.

After capture it submits only the canonical preparation abort. The native owner
reverifies and restarts the original; the worker requires the matching epoch,
source identity, `ABORTED_ORIGINAL`, `service_started=true`, no active guard,
the expected installed revision and a healthy HTTP API before proceeding.
It never submits a cutover request, plan, transport or retirement journal.

Only after that return does it publish the captured original into the existing
private Yandex image namespace, verify full cloud readback, and persist public
byte pins in `capture-proof.json`. The current-source builder then runs off-host.
A builder failure leaves the cloud receipt available for investigation without
another capture window. `failure.json` retains bounded structural SQLite evidence;
no SQL, exception messages, raw database or signed URL enters public artifacts.

This diagnostic does not restore disk headroom or prove a production cutover.
Its successful terminal outcome is deliberately `ABORTED_ORIGINAL`; the normal
cutover mode still requires `COMMITTED` and the independent full-union proof.
Independent final review found no Critical or Important issues. Its optional
coverage suggestion was implemented: an existing same-run diagnostic epoch is
rejected without writing an abort request. Final focused local verification:
63 tests passed in 5.63 seconds. Production health at 12:16:38 UTC remained
HTTP 200 on the baseline SHA, original returned, no new cutover started.

## Concurrent AI report correction

The uploaded GER40 report at 12:12:09 UTC shows `provider_timeout`; that alone
does not identify provider latency versus local preparation time. Inspection
did reproduce one separate request-path defect: quote-only repricing advances
the review timestamp and invalidated the unchanged frozen-input marker. This
caused mutable source/model recapture during a materialized review.

The marker now recognizes a successful versioned deterministic quote refresh
whose original capture timestamp matches. Original facts, clocks and missing
reasons remain frozen; no timeout, risk/source gate or weight is relaxed.
The real repricing regression observed RED then GREEN. One final independent
review approved this two-file correction (34 focused tests, 0.91 seconds).
Provider availability, missing current source history and current family
predictions remain separate acceptance items; this patch does not claim them.
