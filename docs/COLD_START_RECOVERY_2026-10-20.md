# Cold-start recovery regression — 2026-10-20

## Observed incident

PR493/main54a33626f2fc6ece9f75b06528cc5997e02ab345 passed final CI.
The original deployment stopped before delivery because free root capacity was
below the unchanged512MiB prefetch gate. Existing canonical APT cache reclamation
returned342181560logicalbytes (78indexes); canonical Git metadata reconstruction
returned82251776bytes while preserving4refs and the installed working tree.
The actual root reserve reached604704768bytes; HTTP remained200.

Only failed deploy39285567964 was retried, attempt2/job118495722981.
It delivered the exact target, then exhausted its360two-second startup polls.
Its final HTTP probe failed at07:04:47UTC. The current process3235502 finished
storage.prepare at445.671s, app.create at474.789s, schema.backup at754.956s.
Automatic recovery39384232015 started in this gap and stopped that process;
the later read-only trace found MainPID0. No database corruption is established.

## Bounded correction

- Deploy gets480two-second startup polls (finite16min). Actual HTTP/service,
  integrity, live-route latency, readiness and smoke gates remain mandatory.
- Recovery recognizes only an active current process, a positive bounded
  monotonic activation age at most16min, and an allowed STARTUP_PHASE marker
  queried for that PID in the current boot. It checks before and after taking
  the production lock. Expired, absent or unrecognized evidence follows the
  existing recovery path; an undelivered SHA still refuses before mutation.
- Deferral is explicit: rotated=false, deferred=true, status=pending. It neither
  claims a healthy terminal nor dispatches a redeploy. Normal health observation
  can retry later; no monitoring schedule is disabled.

The primary user authorized autonomous necessary fixes, focused checks during
implementation, one independent review, final CI and exact-SHA delivery.
No new broker orders, provider calls, source/risk gate changes or fabricated
ACK/fill were introduced.

## Verification

Actual workflow shell regressions first reproduced the755s failure, two premature
recovery admissions and a false rotated/success classification. Final focused
profile:34passed in1.72s; diff-check clean. Independent review and mandatory
final-tree CI remain release gates. Production acceptance is not yet claimed.

The earlier AI-case audit also now reproduces REDUCE_TAKE on the saved inputs:
piecewise600/seed delta−0.04291585266585R; bridge1200/seed delta+0.08632272588523R.
At the same1200/seed, piecewise delta−0.05526580R versus bridge+0.08632273R.
Execution interpretation is sufficient to reverse the sign; this does not prove
which method predicts actual market execution better. Mandatory dual admission
is unchanged. The stored final second snapshot contains macro_context_v1;
its earlier cumulative exclusion marker is not proof of final macro input loss.
