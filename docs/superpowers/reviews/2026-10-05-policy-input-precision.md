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
