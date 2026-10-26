# Treasury receipt and conservative boundary clocks

> Use superpowers:executing-plans inline; one final fresh whole-package review.

Goal: accept genuine recorded Treasury inputs fetched before their conservative next-UTC-day availability boundary only after that boundary, preserving actual receipt and purged dependence. Prioritize missing Nikkei carry history; no new crypto work.

Base localc8d1e6b/treec01da7ee equals released main72a6eae094a0a70bd72696a1795a3806614bfd28 (PR526). Deploy40286687359 and mainmath40288088943 succeeded; the actual consumer verified all75 models against that exact SHA. Spec original unified-edge plan and user noncrypto-first historical readiness instruction.

Root cause: treasury_t0_context documents published_at as CONSERVATIVE_NEXT_UTC_DAY and explicitly permits max(bound,fetched)<=captured. Child treasury_observation wrongly requires fetched>=published; real producer may legitimately fetch official closing data before next UTC midnight and activate it later. Existing source24h and fetch freshness must remain bounded; do not invent actual publication time.

1. RED tests using the REAL build_treasury_t0_context producer: fetchedAug21 22UTC, boundAug22 00UTC, capturedAug22 01UTC; accept only after boundary, preserve actualfetched clock, label boundary separately from actualpublication. Missing/unknown kind retains existing fail-closed legacy semantics. Wrong source date, stale source with freshfetch and future availability stay rejected.
2. For exact conservative-kind parent, validate observation date→nextday boundary; proof observed_ts/received_ts are actualfetch, available_at remains max(bound,fetch), actual published_at absent. Keep bothsourcebound andactualfetch<=24h plusparentstale/expiry/hash/source guards. Preserve legacy proof semantics when marker absent. Add stable curve-bound purge dependency; append this conservatively to existing receipt-clock groups so repeated fetches of SAMEcurve cannot leak across validation. Preserve distinct curve states sharing one HTTP fetch by deduplicating nativehistory by originalbound+receipt, not actualreceipt alone.
3. Owning regression suites, one final independent review, actual bounded native clock inventory and actual historical fit. No 180sample/admission/freshness threshold weakening, fabricated receipts, timestamp retiming, broker-carry authority or new data provider. MandatoryfullCI→authorizedmerge→automaticexactSHA release/actualconsumer. Claim newJPYmodel only if actualfit confirms it.

Review focus: before/after boundary with realparent; two source dates in one HTTPfetch; same dailycurve across differentfetches remainspurgedtogether; wrongkind/date/stalebound with freshreceipt; native/frozen/runtime facts use identical original clocks; no underlying curve becomes broker rollover/economic proof.
