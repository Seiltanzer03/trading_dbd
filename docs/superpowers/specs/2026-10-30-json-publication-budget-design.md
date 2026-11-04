# Current JSON image publication within the original owner budget

This is a correction to tasks 6/7 of the existing JSON-storage package, under
the user's existing instruction to finish autonomously using Yandex and the
authorized exact-SHA release process. The integrator chooses the approach below;
no new source, model, action, storage service or trading authority is introduced.

Actual run 40937497010 preserved 19,682,756 rows across 110 tables and rebuilt
19,612,536,832 bytes into 11,520,102,400 bytes. Both worker full GETs passed.
The native owner refused handoff before any database write and returned
ABORTED_ORIGINAL/service_started=true. The original is serving HTTP 200.
The precise native exception was not retained; deadline exhaustion is an
inference, not a recovered error code. The measured worker publication occupied
19:46:30–20:26:26 UTC, leaving only about 16 minutes of the 90-minute preparation
budget for the owner's repeated source check and two independent full GETs.

Approaches: widening clocks conflicts with the fixed safety contract. Changing
native verification ordering would touch the destructive owner unnecessarily.
Chosen: publish/read back the two independent off-host images concurrently,
with two isolated clients, a conservative combined staging admission and the
same single publication deadline. The observed hosted worker had 104 GiB free.

Preflight must reserve both raw files plus both worst-case gzip files and 2 GiB
before freezing. Once the actual smaller image exists, repeat admission with
both actual sizes before starting either publication. Gzip staging uses the
existing conservative size*101//100+65536 bound. Two publisher jobs are the
maximum; each retains its existing four upload workers. SSH/SFTP and epoch
checks stay on the parent thread. Join every publisher before requesting abort
or releasing the owned capture. No plan/request is handed off until both real
full GETs finish and the same frozen epoch is checked again.

On successful safe abort, retain a whitelisted public failure code in the
terminal receipt. Unknown/private exception messages become a fixed public
fallback. Fencing failures keep their existing guarded behavior. This improves
the observed missing terminal reason; it does not promise a reason if service
restart itself fails before a terminal receipt can be written.

The 5,400-second preparation, 10,800-second owner, transfer/recovery/start
budgets, full native source/GET checks, actual Timeweb reserve, code-before-data,
old cold journal and no-rollback-after-commit rules remain unchanged. A new
attempt must freeze and capture current data including legitimate new writes;
the old prepared image is not installed over the resumed original.
