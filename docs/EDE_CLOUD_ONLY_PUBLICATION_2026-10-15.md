# EDE cloud-only ledger publication, 2026-10-15

Bounded continuation of the user's autonomous Yandex-storage instruction.
Original unified-edge plan/handoff and the accepted 724ce14 application remain
unchanged while its current EDE producer owns the installed ledger generation.

## Reproduced blocker

Run 38682439464 completed snapshot and scientific audit, but publication failed
at the unchanged 1 GiB + incoming-bytes headroom check. Read-only diagnostic
38694508251 verified the already-uploaded private Yandex receipt and measured
67,792,871 incoming bytes, requiring 1,141,534,695 free bytes. Actual root free
was 1,089,687,552 bytes at 06:47 UTC. Package-index caches are effectively empty.
Existing reviewed capacity recovery 38694984744 found no eligible retired
installer/closed fixed logs; it removed nothing and left the service active.

## Selected bounded change

The candidate registry body is already losslessly archived in private Yandex.
Existing fetch handles a valid receipt without a local gzip. For this migrated
layout, publish only the reports, frozen evidence and authoritative receipt.
Do not stage another gzip on the VPS. Legacy plaintext migration keeps its
existing gzip/prefix retirement path.

Before releasing an existing optional gzip cache: validate the installed
receipt and its identity against the installed byte prefix; perform a complete
fresh cloud restore off-host; then check the local cache's exact hash/size,
regular single-link identity and closed-file status. Recheck receipt identity,
cache identity and absence of plaintext immediately before unlink. Leave the
receipt untouched. Unknown/orphan/changed/open cache or a new plaintext
generation refuses release. Missing cache is idempotent.

New cloud upload/full readback still precedes any publication. The old history
must remain an exact byte prefix of the new opaque ledger. Receipt publication
stays atomic and last, and the identity is checked again at that boundary.
The 1 GiB staging reserve is unchanged. No DB/WAL/observations, user positions,
source clocks, model/weight gates, scheduler or provider changes are included.

This reduces the measured incoming set by the 44,140,909-byte gzip and can also
release the 44,061,003-byte old cache. It does not solve unbounded growth of the
authoritative SQLite database; that remains a separate physical-capacity limit.

## Verification / release boundary

12 cache-safety cases observed RED before the helper existed; seven transport
cases observed RED before cloud-only wiring. Owning integrated profile:
65 passed, 6 subtests. A real local-subprocess/SFTP-filesystem integration covers
the emitted remote helper, lsof check, atomic receipt and subsequent full cloud
fetch. Scientific computations are not repeated for this storage correction.

One final scoped review and mandatory final-tree CI precede production action.
Do not change the installed SHA while scheduled producer 38689302428 is active.
Any operational publication must use its completed artifact under the existing
global EDE serialization and retain the original expected/source SHA. Never
retry the older failed generation over a newer installed ledger.
