# AI verdict API contract

Version: `ai-verdict-api-f1-v1`.

`POST /api/ai/verdict` never delegates arithmetic or policy authority to an
LLM. The deterministic policy snapshot is built first. Provider success returns
`ok=true`, `mode=llm`; provider timeout, rate limit, auth/unavailability or bad
payload returns HTTP 200 with the complete deterministic report,
`mode=deterministic_fallback`, `degraded=true`, and a normalized
`provider_error`. Snapshot, application and journal programming failures remain
JSON errors and are not disguised as provider degradation.

Every response carries a non-secret `request_id`. Server logs correlate
request/trade/review, stage, elapsed time, provider/model, result mode and
exception class. They exclude credentials, headers, cookies and full prompts.
An AI failure cannot mutate the trade, deterministic recommendation, feed,
WebSocket state or previously persisted immutable snapshots.

The Phase F.1 production crash was reproduced as:

`ValueError: decision snapshot contains post-capture timestamps: policy_manager.recalculation_triggers.chain_refresh.next_attempt_ts`

Stage: journal persistence after a verdict had been produced. The scheduler's
future `next_attempt_ts` was not market information and is now excluded from the
research snapshot; the no-lookahead validator remains strict.

