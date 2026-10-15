# EDE export headroom recovery, 2026-10-15

Scope: continue the existing accepted release 588edd8; restore its failed EDE
export without touching trades.db, WAL, observations, source clocks or risk gates.
No runtime code change, deployment or new scientific search configuration.

Evidence: audit38778692434/job116576225529 failed before seed restoration or any
scientific computation: free1006133248 < unchanged1073741824-byte WAL reserve.
Current read-only probes38799653441/38799760660/38799957407 confirm healthy
HTTP200/no restarts, database18918031360bytes, only24 free pages, no useful apt
cache. Other applications and their environments are outside this recovery.

Finite operation:
1. Reuse exact previously reviewed90936bf closed-log helper and existing main
   lossless Yandex archive/metadata helpers. Local focused safety checks.
2. Require green current runtime CI, no active EDE producer and exact installed
   SHA. Archive only closed journals/runner diagnostics and btmp rotation via the
   existing helper; full GET/raw+compressed hash proof before unlink.
3. Standard rename/create logrotate for only inspected auth.log and syslog;
   original inode retained, no copytruncate, existing .1[.gz] refused. Reopen
   rsyslog using its installed rotation hook; application service is untouched.
4. Existing capacity helper archives the now closed text logs with full Yandex
   readback. Repeat actual unchanged source_preflight/API/exact-SHA admission.
5. Reacquire the original acceptance owner, resume failed jobs of38778692434
   once. Successful source/macro stages are retained; no replay of a completed
   scientific result. Existing export guards and off-host continuation still
   determine admission. Failed handoff releases acceptance owner.

This is bounded recovery, not a permanent solution for an ever-growing source
database. A live 1GiB reserve remains mandatory. Archive receipts remain on the
server; all large compression and cloud verification happen on the Actions worker.
Original plan and handoff remain unchanged.
## Actual operation result

Operator38800952302/job116649522066 completed successfully20:22:47UTC.
Reviewed local2abb3fb and remotec915057d have identical tree4da44a6b98e362a3721fe58a37f432f88949bd32.
Focused safety profile passed locally and on Actions:58tests/6subtests.
Final independent read-only review found noCritical/Important/Minor findings.

Thirteen closed files,138116161bytes, were preserved using full Yandex readback
before local retirement: ten closed8MiB journals, btmp5781120bytes,
syslog23954020bytes, auth24494941bytes. Receipts remain beside original paths.

Fresh unchanged source_preflight returnedREADY: free1102479360bytes against
1073741824reserve; source18918031360bytes, WAL14729032bytes. Installed588edd8,
API checks passed, serviceactive/running,NRestarts0. Remaining margin only
28737536bytes: this operation restores current admission; it does not establish
long-term database capacity.

Original acceptance owner38771372036 reacquired. Same EDErun38778692434 resumed
as attempt2/audit116651229993, with original successful snapshot/macro logs
retained17:36–17:43UTC (confirmed through cloned job116651231213). As of20:30UTC,
current export remainsin_progress. No completed research result or new family
admission is claimed; automatic existing guards/publication/handoff remain owners.

## Subsequent access failure — supersedes the in-progress status above

The resumed attempt passed source_preflight again20:23:21UTC. The existing
Yandex historical seed completed full verification20:31:25UTC on the worker.
Then live_snapshot failed in _connect before replicate_live was entered:
SSH protocol banner/session failure after the existing bounded retries.
No new production SQLite reader or scientific computation started in attempt2.

Independent probe38802929867/job116656354333 recorded public API TimeoutError
and SSH-connect SSHException at20:33–20:34UTC. Concurrent official capture
38801890615 acquired its real release successfully but delivery timed out after
reading the exact installed SHA20:26UTC. This is not evidence of a parser/model
or Yandex failure; host-vs-network cause remains unconfirmed.

The already-failed export was cancelled via38803395352; its SSH cleanup had
stalled. Audit116651229993 is cancelled, not successful. Public probe20:37:13UTC
still returnedHTTP000. No blind export/research reruns or runtime deployments.

The registered runner seiltanzer-prod-1 was considered as a local diagnostic
path. A bounded read-only attempt38803965683 is pending; its result must be
recorded before claiming local access. No application restart or provider
console action has been performed.

Acceptance owner38771372036 was acquired20:22:43UTC with expiry
1792102963.890 (22:22:43UTC). Its remote release could not be confirmed during
the access failure. On access recovery, verify exact current installed SHA and
this owner before releasing it; never delete another owner's gate.

Next prerequisite: restore authenticated access or obtain host-console
diagnostics (memory/OOM, SSH service, network and filesystem health). Preserve
trades.db/WAL, observed histories, cloud receipts and the accepted runtime.

## Confirmed restart cause and recovered service

Access returned: bounded probe38802929867/attempt2/job116662026495 reached SSH
20:46UTC, exact588edd8, NRestarts1. Host uptime remained2026-08-31; no VPS
reboot was performed. Registered local-runner diagnostics were cancelled
without executing their payloads; finalizer38804385431 removed remaining jobs.

Diagnostic38804838573/job116662879050 identified global kernel OOM killing
the old seiltanzer Python2867105 at anonRSS1656632KiB on a2015212KiB host.
systemd automatically restarted the service20:39:58UTC. Mandatory SQLite
startup validation was allowed to finish. This operator did not restart it.
The original owner38771372036 was released successfully20:48:13UTC.

Public HTTP200 returned20:52:07UTC (877ms), localHTTP20020:52:13UTC (107ms),
verified by38802929867/attempt3/job116664754501. Initial startupWAL temporarily
reduced free space; SQLite had already reclaimed it before final maintenance.

Final acceptance38805806184: main588edd8 unchanged, stateHTTP200109ms,
worker-statusHTTP20011ms, workerstartup_grace/no error/acceptancepausefalse,
NRestarts1, active/running. Source admission passed with free1104318464bytes,
database18919333888bytes, WAL4140632bytes. Native TRUNCATE checkpoint returned
busy1 with399/399frames checkpointed; the4MiB activeWAL was retained and no
reader was forced. Source tables and observations were never pruned.

Remaining confirmed incident: the OOM cause is known, but its allocating code
path was not captured before death. A concrete unbounded live-shadow extraction
in ProspectiveFeatureAdapter._source_rows uses fetchall on the shared connection
and retains raw frozen JSON while building transformed full-cohort rows.
The next bounded correction will prove streaming plus snapshot consistency
and unchanged cohort/results before claiming this cause addressed.
