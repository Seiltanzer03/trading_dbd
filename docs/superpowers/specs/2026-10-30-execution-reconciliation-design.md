# Unresolved broker execution blocks new commands

User request resumes development and prioritizes real execution control. Actual review ai-fda8cdf114424b6691dd shows CLOSE_10 replacing an unresolved EXIT while a superseded stop remains unconfirmed. Current code protects stop proposals separately, but ordinary reductions can supersede pending work.

Choose a fail-closed API boundary: return409 broker_reconciliation_required with saved unresolved references before snapshot/provider work, and recheck under publication/delivery locks. No new HOLD/reduction/extended command, report or ledger supersession is published. This is preferable to masking the condition as a HOLD recommendation or requiring new broker integration. Existing explicit ACK/manual fills/trade closure remain factual manual reconciliation; no inferred fills or automatic orders. Pending commands in both ledgers and an unresolved superseded stop are covered. A delivery check ignores ONLY the just-published current review, so it cannot reject its own new command or hide an older one.

UI shows a critical reconciliation panel and saved references. It can record explicit non-execution using existing ACK, or a broker-reported actual execution with an explicit fill price; the latter is available only for current pending commands. Crossed legacy stop installation is never replayed; actual closed positions use existing journal closure. Unknown state never becomes an ACK.

No weight/admission/source/risk/clock changes. Actual diagnostics show ProxyError in provider HTTP and model publication4d35 versus installed407999. Refresh exact-SHA models after existing storage release; diagnose actual proxy route before any transport change. Old .env probe used the wrong configuration source and is not connectivity evidence; next probe uses actual service environment internally. Existing PR548 scheduling correction is already reviewed/CIgreen and retained.

Owning integration: background poll retains the same structured409; /api/position exposes the actual reconciliation context; production smoke checks it against the blocked response without claiming LLM availability. Explicit non-execution does not acquire a market quote or invent fill data.

Review corrections: unresolved superseded reductions also require factual outcomes. Non-execution receipts precede stale execution checks and cannot alter actual geometry. Late delivery guards may invalidate only the same current job.
