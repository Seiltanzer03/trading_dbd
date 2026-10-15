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
