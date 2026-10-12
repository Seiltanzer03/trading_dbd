# Broker quote continuity repair — 2026-10-13

Base main8c6d4aa39684432324dbc048aed0ea9926c70445/treebc8650cb407a04af7d8939f05074cf77cbae6b71.
Scope: keep an existing exact direct broker observation only within its original30-second
provider timestamp window when a refresh fails. Expired/wrong-symbol/derived/proxy/
delayed observations continue through existing fallback. Preserve originalts/value,
expose transport error; no authority promotion, new data, orders or risk-gate relaxation.

Observed public state2026-10-12T21:51UTC: OANDA websocket timeout, indicative Yahoo
fallback; AI snapshot rebuilding with PRICE_AUTHORITY_CHANGED and154752ms last build.
Last saved report was deterministic-policy-fallback, not a successful external LLM
answer; source/model manifest mismatch predates19:36exact-SHA24-model publication.
Current fitted publication accepted:24fitted groups/14working-supported. This does
not prove all8families active or sufficient native data for each market.

Evidence: real production refresh and freshness definitions AST-executed with controlled
transport/time; positive regression observedRED, then2testsGREEN including7rejected
input/expiry cases. Sourcecompiles. One independent final review: noCritical/Important.
No newprivateprovider request completed. Automatic approval review rejected direct
POST /api/ai/verdict because it sends private snapshot to OpenRouter and may incur
provider usage without explicit destination consent. Do not retry indirectly, including
a deployment smoke POST, until consent is established. Draft/codeCI may proceed.

Finish boundary: fullmandatoryCI; consent for existingOpenRouter review before merge
and automatic deployment smoke; installedexactSHA+actualpositiveLLMresponse and
candidate/component reasons. No claim fullterminal/8families complete.
