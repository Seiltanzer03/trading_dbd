# Policy input precision and actual execution barriers

## Confirmed defect and bounded change

`PolicyInputs.as_dict()` rounded current/stop/take coordinates to four decimals.
Valid `stop_r < r0 < T` inputs (e.g. current 0.00001R and stop 0R) could become
equal after JSON serialization and fail unified economics as INVALID_POLICY_INPUTS.
Keep finite numeric precision for these three machine-consumed coordinates.

The extraction clamp also moved an actual reached/breached stop below current
price by 1e-8R. Removing display rounding alone would expose that artificial
interior domain. Preserve the actual stop instead. Strict economics validation,
risk/cost gates, smoke acceptance and order/ACK behavior remain unchanged.

## Verification evidence

- Regression before precision change: 3 failed, 2 passed, 12 deselected, 0.76s;
  all three valid frozen-bank examples failed INVALID_POLICY_INPUTS.
- After precision-only change: affected profile 95 passed, one existing
  Starlette/httpx deprecation warning, 4.12s.
- Added producer controls for reached/breached stops. First fixture had an
  unrelated current-price geometry mismatch; corrected geometry and asserted
  actual-stop domain directly. Corrected RED: 4 failed, 17 deselected, 0.38s,
  because extraction fabricated stop below current price.
- After preserving actual stop: affected profile 99 passed, same existing
  warning, 4.21s. Includes unchanged authoritative-price smoke tests rejecting
  arbitrary 422 responses. No whitelist or latency-budget relaxation.
- Whole local suite (`../trading_quotes/.venv/bin/python -m pytest -q`):
  2969 passed, 4 skipped, 1 failed, same warning, 109.90s.
  Failure: `tests/test_storage_sparse_backup_guard.py::test_quiescent_sparse_clone_replays_wal_only_into_backup`,
  RuntimeError authoritative database changed during quiescent sparse clone.
  This is the unchanged known Python3.12/SQLite local baseline failure already
  present for PR399; guard and test are untouched. No all-green local claim.
  Mandatory official Python3.11 exact-tree CI remains required before merge.
- Independent read-only consolidated review: approved, no material findings;
  reviewer inspected diff and consumers, did not run tests or remotes.

Affected command: `../trading_quotes/.venv/bin/python -m pytest -q
tests/test_unified_candidate_economics.py tests/test_extended_policy_evaluation.py
tests/test_ai_policy.py tests/test_ai_policy_v2_cache.py tests/test_ai_policy_v4.py
tests/test_ai_policy_v6.py tests/test_ai_policy_v7.py
tests/test_ai_authoritative_price_preflight.py`.

## Production evidence limits

PR399 main 3d5b3523c6d79ed8147deab9f9738e514a0a4e54 installed and passed
readiness, but functional acceptance run37348394765/job111892736349 failed
AI422 invalid_common_economics / INVALID_POLICY_INPUTS (~5008ms).
Request ai-92eee6032ddd442a97a8 did not reach publication. The actual invalid
predicate and frozen input values remain unavailable. This locally confirmed
defect is not claimed to explain that request.

User explicitly approved read-only HTTP `/api/state` and
`/api/ai/snapshot/status` on 94.241.171.182:8790. Browser access was nevertheless
automatically rejected after its HTTPS upgrade: approval was for HTTP origin.
No alternate route, runner, transport, security-warning bypass or production
mutation was used to obtain the blocked data. Live linkage/full acceptance
remain blocked pending authorized access. No blind rerun or rollback.

## Authorized read-only diagnostic channel

User subsequently explicitly approved all diagnostic reading, including both
origins. Browser access is now authorized but technically unusable: HTTP is
upgraded to HTTPS and the server does not speak TLS (browser 502). The separate
GitHub browser is signed out; the GitHub connector remains fully connected and
continues to own branch/PR/release operations.

Added production-policy-input-diagnostics.yml to read installed exact-SHA
cached state through the existing SSH Actions channel. Owner/same-repository
guard; fixed two cached GETs; 5s transport, 4MB response, 1MB source and 4min job
bounds. No verdict, refresh, Engine, package initialization, database write,
service restart or orders. Only bounded pure extraction definitions are AST
loaded from the clean installed file; no secrets or private numeric values are
printed. Evidence is explicitly present cached tick, not the failed frozen AI
request. Output flags identify invalid domain predicates and actual barrier
crossing without fabricating attribution to the earlier request.

Scoped safety review found package import/installers, nullable original-stop
fallback mismatch and unsanitized exception text. All three addressed; reviewer
verified fixes and approved. Root YAML/Python parse and three controlled
null-fallback/domain fixtures passed with no private numeric output. Product
unchanged from e56e725; no repeated local broad run for workflow/report-only
addition. Prior exact product official tests37356900469 and lattice37356900472
both succeeded; final amended-tree official CI remains required.

Actual installed-SHA diagnostic run37357880702/job111924819461 succeeded:
active trade true, option available true, no invalid numeric keys, finite rungs,
positive sigma/horizon, valid fraction. Raw and serialized domains false;
lossy-domain-collapse false; serialized_take_reached true. Actual stop available,
unbreached and not moved. This establishes a currently open manual position at
or beyond its take, not a serialization-only explanation. Historical request
ai-92eee6032ddd442a97a8 is still not bound to these later cached bytes.

Bounded follow-up design: only explicit authoritative complete finite canonical
price geometry can return a typed fast400 execution_barrier_reached before
rate-limit consumption, mutable finalization, provider/enrichment/publication.
Actual prices produce unrounded normalized-R current/barrier proof; reached
take/stop is explicit, execution remains unconfirmed. No automatic close or
invented broker fill. Incomplete/invalid/untrusted geometry cannot use the new
accepted negative contract. Smoke validates its exact schema, numeric proof,
boundary inequality and no-publication flags, while retaining the isolated
12-action positive test, original latency budget and rejection of arbitrary422.
User's standing waiver of design/spec approval pauses applies to this bounded
existing-flow correction. Sole coupled writer; one consolidated final review.

Coupled implementation frozen: pure complete finite positive canonical prices,
ordered stop/take and declared source authority; raw-price comparisons include
long/short exact boundaries without using rounded snapshot R. Fast400 occurs
before ai_last_call, sync_be, enrichments/provider/publication. It leaves journal,
management events and shadow actions unchanged and explicitly denies broker
execution confirmation. Frontend safe_fetch already displays error.message;
no UI change needed. Existing economics422 rejection remains untouched.

Actual preflight TDD: initial20failed17passed1knownwarning2.86s (missing helper,
route forbidden synchronization, smoke400 rejection); added huge-integer input
RED1failed43deselected0.41s (OverflowError), then bounded fail-closed handling.
Final writer command `../trading_quotes/.venv/bin/python -m pytest -q
tests/test_ai_execution_barrier_preflight.py
tests/test_ai_authoritative_price_preflight.py tests/test_ai_verdict_api.py
tests/test_ai_report_semantics_guard.py`:96passed1existingStarlettewarning6.15s.
Tests include both directions and boundaries, just-inside provider success,
malformed/undeclared/untrusted geometry, exact strict smoke and arbitrary422/
slow/schema/proof failures. No existing-test fixture changes or weakened guard.
Root pre-producer-fix broad:3013passed4skipped1known unchanged sparse-WAL
baselinefailure1existingwarning113.27s. This run is explicitly pre-I1, not final.

Consolidated independent full-PR review found one Important I1: the owning
ai_verdict_base producer rounded all five machine price fields to four decimals,
so true interior124.999999 could become equal to take125 and yield an accepted
negative response. No other material findings; independent narrow65passed
1existingwarning3.96s. I1 accepted and fixed in the owning producer: finite raw
price precision retained; R/display-derived fields remain unchanged.
Actual producer (real Engine/journal/position with only policy analysis stub)
to API regression RED4failed2passed44deselected1warning1.76s, then GREEN109passed
1existingwarning8.40s including new/authority/API/semantics/ai_verdict profiles.
Near-interior prices reach provider200; exact/reached barriers400 do not.
Reviewer scoped I1 verification approved: independently6passed44deselected
1existingwarning1.59s. I1 resolved; no remaining Critical/Important findings;
whole review was not repeated.
Final mandatory exact-tree official Python3.11 full suite and real WebKit will
provide final broad verification; no redundant local whole-suite rerun for
this scoped fix against the known unchanged local Python3.12 WAL failure.
