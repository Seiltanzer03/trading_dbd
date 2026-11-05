# Current source inputs after production acceptance

Execute inline; one final review of this follow-up diff, no repeated review of PR550.

PR550/main197176c is installed with5313 Python tests and all CI/production contexts green. Actual acceptance found only3–15/92 fresh fields for NAS100 while its CFD quote is live: the canonical one-minute inputs still use closed cash-index bars. Probe41987993006 obtained359 actual completed1m OANDA:NAS100USD bars, latest5s old, without publishing them or changing any quote. Option cache is intact:60 actual QQQ snapshots,676KB; independent reader works. New math/source publications are current-SHA; six Active Edge reports are regenerating in existing jobs. One actual open NAS100 trade exists; last real AI review Nov5 06:27UTC, not Oct30. Never fabricate or ACK it.

Goal: supply the unchanged metric definitions with fresh actual source inputs, including outside the cash session, and close observed multi-hour gaps in best-effort source publication. No broker quote replacement, fills, training, thresholds, weights, source/expiry/identity or execution-gate changes. One-minute reference is a distinct contract; existing5m forecasting/model proofs remain5m and reject1m. Never resample5m as1m or mix differently priced native and cash bars into one return path.

## Task1 — configured native minute reference

- RED: real-shaped recorded1m transport resolves exact configured symbol, excludes incomplete/future bars and preserves actual request/receipt clocks; existing5m contracts still reject minute data.
- Add interval1m to bounded existing transport; keep default5m and exact symbol/refusal/body/time checks. Acquire on existing minute feed refresh, store separate current-instrument input, reset on switch, preserve original last-good clocks on failure.
- Freeze independently; use valid native1m rows alone for current V2/V3 metric calculations, retain archive/received legacy fallback. Preserve reference/proxy provenance and source clocks, never execution authority. Existing two-generation/runtime and snapshot bounds remain.
- GREEN: closed cash archive plus actual native reference yields current correct returns/volatility without SQL/research writes; all10 configured instruments, switch/future/wrong-source cases and5m model separation.

## Task2 — bounded existing family-source refresh

- Activate deferred source-continuity tests from prior task; reproduce reader and refresher failures.
- Independent single-flight producer uses the existing bounded public collector only when no current exact-SHA publication is younger than300s. Preserve denied/missing sources and original source clocks, no new providers or models. Failed/invalid/generation-raced refresh keeps prior bytes; atomic bounded publication on same existing disk.
- Reader validates both publications through unchanged admission and merges source identities within existing8KB selected budget, preserving baseline facts on invalid/overflowing runtime data. Requests remain local reads only; source publication invalidates AI materialization.
- Dedicated background capacity and shutdown drain; a blocked collector cannot stop quotes or decision metrics. No new service, paid infrastructure or credentials.

## Task3 — combined current-LLM transport

- The PR550 automated review identified a separate production path: the explanation projection drops the metric packet, and the combined provider only restores selected authoritative fields.
- Reproduce through the actual combined transport after explanation compaction. Restore the decoded authoritative vector and its original provenance alongside existing independent facts. Preserve masked quant choice and provider byte bounds; never forward unverified compact values.
- Regression failed with missing `decision_model_metrics`, then passed with all92 readable fields, original as-of/receipt clocks, proxy role and immutable authoritative snapshot. The seventeen transport/packet/projection checks passed. Include this narrow correction in the single final review of this follow-up package.

## Release

Owning tests, one fresh final review of this new diff and one correction pass, mandatory final-tree CI, authorized PR/merge/normal exact-SHA deploy. Verify actual runtime metric availability and per-family source/model reasons; wait for current six-report publication. Preserve the first release receipts and existing research; do not duplicate searches or restamp reports.

The single final review found one Important continuity defect: refresh inherited and appended old collector windows, and changing body hashes also defeated baseline/runtime deduplication. Two regressions reproduced the second-cycle stale-baseline fallback and duplicate older window. The correction builds a fresh acquisition and replaces only the same known collector lineage after unchanged admission; observed/receipt clocks choose the latest observation. Four sequential real-parser cycles and baseline/runtime merges pass. Final owning profile:239passed. No Critical or Minor findings. Full native3.11CI, live exact-SHA freshness and actual current provider packet remain release checks; localcurl_cffi native import crashes on this restored3.12host, without product/CI changes. Current-family and rich-management fixture packet measured39960B under the unchanged60000B provider bound; production composition still needs verification.
