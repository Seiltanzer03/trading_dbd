# Noncrypto live reference series implementation plan

> Use superpowers:executing-plans inline; one final fresh whole-package review. The user's autonomous execution instruction remains in force.

Goal: train and consume the existing family market models on the same explicitly configured, fresh reference series, without replacing execution quotes or weakening source clocks.

Base local545041c/tree3f7b931 equals released main6e2897a6d3e52305312f28337f81d9332cb33de8. Original unified-edge plan and latest noncrypto-first completion instruction apply.

Evidence: read-only capability run40331832336/job121602959209 returned4800 actual5m bars each for OANDA:NAS100USD and OANDA:XAUUSD, through the already-used TradingView transport. Exact resolved symbols matched; latest completed bars were33–34seconds old, versus the measured ten-minute YahooGC=F delay. Remaining eight symbols are being verified once. No future-window/retimestamping fallback will be implemented.

Architecture: keep Yahoo/Binance Price/path training and existing execution quotes unchanged. Add an independent bounded family-reference lane. Each fitted family declares its exact target provider/symbol; runtime supplies that same series or rejects it. OANDA spot-metal/FX references are explicitly forecast references, not equivalent Swissquote execution quotes. Crypto-specific work stays paused.

- [ ] RED→GREEN `reference_market_series.py`: exact configured symbols; bounded public chart request4800historical/120runtime bars; completed actual5m OHLC only; resolved identity/hash/original receipt; heartbeat/error/timeout/duplicate/future/malformed handling. No auth escalation, pagination around provider limits, interpolation or shift to spot scale.
- [ ] Extend off-host `scripts/run_mathematical_edge.py` with optional independent family target sources, collected within the existing total refresh budget. Use those targets for existing family fitting; Price/path sources stay separate. Missing reference history falls back to its explicitly declared legacy target. Model target choice uses input availability, never held-out score.
- [ ] Extend family model source validation with the exact configured reference profile; retain statistical gates, frozen hashes and legacy models. Keep raw bar arrays out of the published model report.
- [ ] Feed refresh collects a small reference window outside locks; instrument switches clear it. Freeze it with existing expert inputs. Attach each family using its own declared target lane; unchanged latest-completed-bar, receipt, source freshness and model validity checks apply. No current bar is manufactured from delayed Yahoo bars.
- [ ] Add bounded background model-readiness receipt to the existing snapshot status so actual forecasts can be checked without paid LLM calls. Show concrete trained-but-blocked reasons instead of conflating missing current input with missing training.
- [ ] Profile changed contracts; one final reviewer; mandatory full CI once for final package, authorized merge and exact-SHA deploy/actual model consumption. Verify actual active-instrument family forecasts separately; do not infer votes from archive fit count.

Review focus: no provider/target mismatch; no late source after frozen cutoff; exact completed bars and unchanged runtime freshness; no partial or conflicting duplicate bar; private source inputs not published; no changes to broker orders/ACKs/cost/risk gates; failure of an optional reference cannot remove valid legacy evidence.

Remaining event/order-flow and JPY positioning/option gaps stay explicit unless actual approved inputs resolve them. This package does not fabricate those inputs or start another large database transfer.
