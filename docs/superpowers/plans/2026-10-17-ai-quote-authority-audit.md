# AI quote authority audit — finite fix

Original specification and locked global plan unchanged. Autonomous inline implementation, focused regression, one final read-only review, full mandatory CI before merge.

Baseline: PR483 accepted treeef7c5f0e, main011b363d. Cloud-only439-part restore38981061168 continues independently; never repeat it for this AI change.

Actual read-only probe38981251743 on9bf0e5d: snapshot invalidated PRICE_AUTHORITY_CHANGED,245builds, last21790.1ms, no error. Mathematical artifact exists398786bytes/25trained15supported but published_for_shaa1e9 differs from installed9bf. No paid requests/fits performed. Do not rebind that old artifact or relax exact-SHA/source freshness.

## Scope and criterion

`ai_policy_v6._input_audit` must retain the quote's explicit production_authority/derived/is_proxy flags. The materializer compares audit authority to the live quote; omission of explicitFalse currently converts it to implicitTrue and can create a false refresh on an unchanged quote. Preserve the flag in the owning audit builder rather than absorbing an actual authority change at completion.

- [x] RED: real audit -> materializer with unchanged non-authoritative quote produced PRICE_AUTHORITY_CHANGED. ExplicitTrue branch exposed missing flags; absent-key test passed. Assert exact flags and tick immutability.
- [x] Preserve only existing explicit flags, no inferredTrue/default authority. True gain/loss and source changes still invalidate; normal/strict budget compaction retains provenance.
- [x] Focused audit/materializer/quote continuity/refresh/report guard:94passed3.25s, one existing dependency warning. Added strict-compaction variants:5new regression cases passed0.64s. No simulation or provider calls.
- [x] One final independent review: no Important blockers; no edits or repeated tests. Full union archive operation is outside this review.
- [ ] Full CI, authorized merge and exact-SHA delivery observation. Unchanged source disk guard may still block research acceptance; do not weaken it.

No source normalization, fallback promotion, broker fill, probability, weight, market freshness or paid LLM change. Mathematical/source publication recovery is a separate operational issue; new training requires new usable input.
