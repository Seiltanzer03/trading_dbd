# SDD ledger — plan: docs/superpowers/plans/2026-10-27-treasury-ai-input-freeze.md

Pre-flight: one shared capture-to-inference contract. Base f671626/treeb1ad9f3d equals accepted main69328c; no unfinished source change.
Ruling: reuse the linked worktree and accepted CI baseline. User/AGENTS authorizes inline fixes and release after final mandatory CI; no renewed approval and no implementer agents.
Ruling: Treasury is an existing low-frequency in-memory source. Copy it once before the builder; never refresh, backdate or expand authority. Cost if unavailable: existing carry forecast remains explicitly unavailable.
Ruling: retain the separate missing Yahoo market bar and aligned related-window restrictions. Fix only the confirmed missing Treasury AI-input bridge.
Task 1: constructing RED regression before production implementation.

Task 1: three actual regressions observed RED: missing AI root/child forecast, builder replacing original source, and pressured snapshot losing carry features. Eight preexisting bad/missing-parent behaviors characterized. Implementation reads only the existing cache at the original frozen market time, outside the price lock; finite bounded JSON copy before builder and private-root attachment before model inference. Original clocks/parent authority and all child gates unchanged. Eleven wiring tests GREEN. Owning freeze/Treasury/reference/materializer profile198passed9.83s; budget/compaction26passed0.82s; one known Starlette warning. Diff check clean. Final review/CI/production pending.
Ruling: retain existing v1 frozen-input marker: optional Treasury fields extend new captures, while already frozen historical/cached snapshots must not be retroactively supplied a later parent. Cost: older captures continue to show the old explicit input limitation.
Ruling: local focused profiles preload unused yfinance stubs solely for the known native Python3.12 BusError; real Python3.11 full CI stays mandatory. No runtime provider requests or fitting occur in this bridge.

Final independent review of f671626..e52c2e8:0Critical,1Important,0Minor; independent98passed5.65s. Reviewer confirmed originalcutoff/busylock/sourceimmutability/pressure behavior. Important nonstring source_url caused urlparse AttributeError and suppressed unrelated models. Reproduced three RED cases(dict/list/int) through the actual fitted-model pipeline; source-owner validator now rejects nonstring URLs before parsing. Final whole owning profile227passed11.86s, one existing Starlette warning; diff check clean. No re-review.
Final Ruling: consumer type rejection is part of the same input-isolation fix; no URL authority/freshness policy changes. Cost of malformed optional input remains an unavailable carry forecast, not loss of other models.
Final Ruling: full Python3.11CI, exact-SHA deployment, live carry readiness and qualified preference remain actual release gates. Fixtures and local review establish wiring/isolation only; no profitability claim is inferred.
