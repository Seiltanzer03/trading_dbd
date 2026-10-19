# Frozen history capture: measured transport correction

Baseline mainb3d1687e6654d13c1246092254da0cdd6d626b66/treeb52c998a7446295c17d2ecca73eb482d3edd0c6b.

## Actual overnight outcome

Cutover39170527310/job117805166185 failed22:53:26UTC after37minutes, before any
hot-build/accepted-plan/full-union artifact. Diagnostic39202257215/job117897542975
at05:00:07UTC verified ABORTED_ORIGINAL/service_started=true,
WORKER_REQUESTED_SAFE_ABORT, no guard/journal/accepted plan/request/capacity
receipt, HTTP200, root free604913664bytes. Frozen source19315957760bytes,
SHAfe2d3300ce31c0cf96ec31df1c3d4ad254ba12133db4b99825d351e9412e650f.
Current source19320233984bytes proves live writes resumed. No retirement success.
The failed worker discarded its original exception; timing matches preparation
plus1800s capture, but the exact old exception remains unproven.

Automatic backup recovery39171395121 refused mutation while the native owner
was recovering. Deploy39170408478 failed its HTTP startup allowance: initial
storage preparation450.005s, schema-backup begin474.785s. Subsequent health was
HTTP200. Delivery is distinct from full release acceptance.

## Reproduction and measured decision

A real SSH/SFTP test reproduced serialized32KiB reads, peak outstanding1.
The first bounded4MiB/32request candidate passed120focused tests and independent
review; its final candidate CI39203447274 passed4565tests/5skips/13subtests,
WebKit/acceptance, real systemd owner and pinnedSQLite checks. It was not merged.

Read-only worker-to-VPS measurements used only the public8,020,928byte Python
executable, never the database or a new VPS staging file:

| Run | Transport | Short-probe raw-equivalent MiB/s |
| --- | --- | ---: |
|39203741156|SFTP32requests|2.075|
|39203741156|SFTP64requests|3.593|
|39204060404|rawSSH16MiB window|5.923|
|39204350461|SFTP32/64requests|2.104 /3.827|
|39204350461|rawSSH16MiB window|5.533|
|39204350461|gzip1SSHstream|8.922|

Short probes include startup/roundtrip overhead; they do not prove sustained
19GB database throughput or its compression ratio. They demonstrated that the
first candidate did not establish adequate capture performance. Choose a single
compressed stream over the existing SSH transport rather than releasing that
candidate or extending the1800s limit. Actual capture must still meet that limit.

## Final capture contract

The native owner still freezes/checkpoints/pins the original and owns the
existing exclusion,5400s preparation and10800s hard lifetime. The worker opens
one16MiB SSH receive window on its existing authenticated connection and runs
only `/bin/gzip -1 -c -- /opt/seiltanzer/data/trades.db`. This command reads the
frozen source and writes stdout; it creates no second VPS database/archive.
Control metadata remains on the existing SFTP channel.

The worker incrementally decompresses into its exclusive0600 local file.
Compressed reads check the original deadline and a bounded wire-size envelope;
this also bounds streams/members that yield no uncompressed output. The
unchanged verifier checks exact uncompressed size/SHA and actual EOF; gzip
checks CRC/trailer and the remote command must exit0 within the same deadline.
Local SQLite integrity and identity checks remain. No fabricated cloud receipt,
new provider/resource, source/risk gate change or old-copy overwrite.

The prefetch path was removed, including its cancelled-daemon lifecycle issue.
Real SSH/SFTP tests prove the one fixed gzip command,16MiB window, zero SFTP
source opens, exact raw bytes, rejected CRC/truncation/extra data/nonzero exit,
a deadline on empty gzip members, and a still-usable control SFTP session.
An independent review found one Important issue: Paramiko's command-ack wait
and partial receive loop can outlive socket timeouts. A daemon deadline timer
now closes only the owned capture channel at the absolute capture deadline;
every exit cancels it and allows at most one second to join its close callback.
This does not close the control SFTP session. Two real-SSH regressions first
failed against the old implementation, then verified that blocked exec ACK and
trickled compressed input return within0.5s for a0.1s deadline, delete the owned
partial, preserve source bytes/control SFTP, and leave no deadline timer alive.
The native owner's independent lifetime and rollback remain authoritative.
Missing remote exit status is bounded; each failure removes only its owned
worker output. Existing complete driver/owner/publication/full-union and safe
abort tests remain, including diagnostic-write failure and provider redaction.
Focused validation:129passed/1skipped in11.15s. Native/proc proof remains
mandatory CI.

Public stage/byte progress and redacted failure artifacts retain stage and fixed
codes, never raw provider exceptions/signed URLs. Preflight errors before the
guarded run block still have generic diagnostics (deferred minor). Diagnostic
write failure cannot suppress safe abort.

CI explicitly installs Paramiko only in the mandatory test job; keeping
pyproject unchanged avoids triggering unrelated ML retraining. Local developer
runs without that operational dependency explicitly skip the SSH proof module.
An attempted cancellation of the old-head ML run39203077006 was rejected by
automatic approval review; it was left running, without a bypass/retry.

## Release boundary

The compressed transport delta received an independent scoped review: one
Important deadline issue corrected as described above; no other blockers.
Run final CI on this corrected tree before merge; earlier green CI covered
the superseded prefetch candidate. Production compression throughput, hot retirement,
headroom, full union and terminal/research/AI acceptance remain unverified.
