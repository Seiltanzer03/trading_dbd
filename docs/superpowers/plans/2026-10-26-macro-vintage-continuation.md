# Macro vintage continuation implementation plan

> Use superpowers:executing-plans inline; one fresh final whole-package review.

Goal: retain already verified official ALFRED vintages when one month has no values; preserve acquired rows and stop further source requests on transport failure. No missing row is reconstructed.

Spec: original unified-edge plan, noncrypto-first user instruction2026-10-26. Base e095630/tree30332cf equals released main7b0. Actual read-only730day probe40281914988 found bothCPI/NFP fallbackKeyError2025-10 causing all acquired family rows to disappear. H1 prototype remains separate checkpoint/noncrypto-hourly-events-20261026; no H1/source-window changes ship here.

1. Regression-test build_bundle with a canonical verifiedCPI vintage and an unavailable period before/after it. Keep original bytes/hash/value/receipt; report failed date explicitly; validate retained evidence using existing bundle validator. Transport rejection must end source acquisition, retaining only prior actual vintages and inventing no later rows.
2. Fix the per-release boundary in macro_historical_offhost_bundle.py. KeyError means unavailable period; skip only it. Other source failures stop this family without retry/reroute. No changes to source parsers, clock rules, live source windows or model/admission thresholds.
3. Run archive/transport/family owning suites; one final review; full mandatory CI then authorized merge/automatic deploy/exact-SHA acceptance. Confirm actual historical source continuation, preserving partial errors. Do not claim this supplies actual intraday event timestamps: ALFRED remains day-granular conservative availability.

Review focus: failure before first/middle/last date; invalid/denied response cannot acquire authority; no post-denial request; unchanged retained payload/sourcehash/receipt; no imputation, no ALFRED date promoted to publication clock; all-failed family remains absent with error.
