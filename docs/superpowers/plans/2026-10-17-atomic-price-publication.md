# Atomic publication of a completed price refresh

Original specification/locked plan unchanged. One inline implementer, one fresh final review, one final-tree CI. Existing worktree has one writer; preserve all unrelated untracked work.

Baseline main a2a48108cd0fd924e5fcf684be16af5043e48388/tree42b549d5. Actual read-only38985347411 showed canonical Yahoo cash alternating with Bybit mapped proxy and repeated PRICE_AUTHORITY_CHANGED builds12→14. Code refresh_price restores _primary_price into public .price before blocking primary HTTP, then publishes the fallback; concurrent canonical tick can consume the intermediate primary. Previous audit False-preservation remains necessary and unchanged.

Scope: MarketData price publication only. Private thread-local candidate during refresh; readers retain the last completed quote without blocking on HTTP. Publish primary/final quote once after fallback selection, only for the same instrument generation. Unexpected refresh failure retains previous completed quote; concurrent refresh owner abstains. Existing source selection, actual timestamps/freshness, flags, anchors, reserve/risk/acceptance rules and AI event checks remain unchanged. No paid LLM/fit/restore/provider experiment.

- [x] RED: six cases:5failed/1passed; primary two reproduce PRICE_AUTHORITY_CHANGED through real audit/materializer while network owner is suspended. Event handshakes, no sleep timing. Failure leaves previous quote; recovery causes genuine invalidation; instrument switch cannot publish old price; overlapping refresh is bounded.
- [x] Minimal owner implementation: thread-private candidate, nonblocking owner lease, revision-checked publication;85focused price/crypto/Bybit/AI tests passed3.31s,1existing Starlette deprecation warning; diff clean. Source timestamps/flags, actual selection and AI event guards unchanged.
- [x] One fresh read-only review: no Critical/Important blockers; no edits, provider calls or extra suite. Optional same-thread recovery-test extension deferred; cleanup is already covered by nested finally ownership. Additional46canonical/authoritative-price/feed resilience tests passed9.29s, same existing deprecation warning. No repeated broad local suite.
- [ ] Final CI, authorized merge and exact-SHA delivery/observation. No claim of full terminal acceptance while disk remains below1107296256B.
