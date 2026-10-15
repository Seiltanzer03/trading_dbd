# AI review correction — 2026-10-15

Scope: the user's reported ai-944fd92fac764c5f97af / decision-b91c4fe28247268c86b95a8a2791.
Original unified-edge specification and handoff remain unchanged. No new trading
policy, provider, feed, forecast, broker acknowledgement or position mutation.

## Confirmed causes

Read-only diagnostic38702513160 found actual review388 and its request trace.
The production combined v20 provider call timed out after6025ms. The existing
one-call structured adapter is installed by __main__; an early bypass hypothesis
was disproved before implementation. There was no retained provider reason or
unavailable-shadow contract in that frozen review.

Deployment54a1625 delivered code but failed readiness38698698305 on cold detailed
Q-audit latencies4724/4039/3796ms against the unchanged3000ms requirement.
Its subsequent model/source deliveries never started. Actual installed artifacts
still name724ce14, explaining exact-code generation rejection. Current warm
diagnostic Q timings were915/268/272ms; this is not a general API outage.
The stale_observation market-input refusal is genuine and remains mandatory.

## Bounded correction

- Preserve a valid structured preference if optional commentary is absent,
  empty or imperative. That commentary is never displayed as authority; the
  server still owns all action/numerical prose. Missing/invalid policy scores
  still fail closed. The same one request is asked for less redundant text.
- Retain normalized provider failure code/retriability and explicit unavailable
  shadow in the frozen review, without raw provider bodies or credentials.
  Show the safe actual reason in the UI instead of an unexplained fallback.
- Name the actual decision authority and unknown cancellation boundary.
  When the ensemble is applied, describe its current contract rather than
  presenting legacy degraded-overlay rules as its execution conditions.
- Count observed adverse rows instead of substituting zero for a missing
  counter; an unavailable derivative workspace counts as zero displayed metrics,
  not one narrative line. Preserve original source-anchor refusals for blocked
  actions which deliberately have no executable parameters. None become eligible.
- Use the already-existing aggregate-only Q endpoint for readiness: the same
  5000-attempt cohort, maturity/overdue semantics,3000ms budget and retry count.
  Only unused item decorations and their predecessor lookups are omitted.

The6second provider guard, hard-risk/CVaR, source TTLs, original timestamps,
identity checks and manual-only execution are unchanged. A slow upstream may
still cause an explicit fallback. LOWER Expected with better CVaR is not by
itself an error under the existing bounded tail-protection policy; no weights
or risk utility were tuned to this trade.

## Verification and acceptance boundary

Eleven reported regressions observed RED then GREEN. API fallback persistence
and readiness-path contract also observed RED before correction. Frontend
warning tests observed missing-export RED then GREEN. Owning profile169passed;
Q scalability/dependency profile5passed, including real HTTP summary/full parity
across resolved, pending, blocked and unavailable-source cases. Existing one
FastAPI/Starlette deprecation warning remains. Real installed provider transport
is tested with a local HTTP fixture, all12scores, nonzero LLM contribution,
masked prior winner and immutable input/response hashes; it is not a paid
production LLM success claim.

One final independent review, mandatory full final-tree CI and exact-SHA release
are required. Recover only the normal model/source delivery chain after actual
readiness; never relabel old-generation artifacts or stale prices as current.
