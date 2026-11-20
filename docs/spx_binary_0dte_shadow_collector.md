# SPX Binary / SPY 0DTE Shadow Collector (research only)

**Opt-in and not deployed.** Adds `scripts/spx_binary_shadow_collector.py` as a standalone program. It is not imported by the live terminal and does not change `trades.db`, `cache.db`, existing APIs, risk models or any production configuration.

## Why

Sep–Oct 2026 archive has 233 unique SPY proxy snapshots, but 0 confirmed provider-level quote-as-of times; 214/233 selected a further expiry for broad strike support. Most recorded normalized Breeden–Litzenberger densities fail the forward/moment check. Old snapshots cannot be retroactively repaired.

## Guarantees

- Select **only** SPY chain with expiry = current New York exchange date, never any longer-dated fallback. After 16:00 ET, no collection. Exchange early-close days require an authoritative calendar.
- Save actual 0DTE raw bid/ask, IV, OI, volume, contract symbol, last trade and time, sizes if present, plus chain request/receive times. `lastPrice` is retained for diagnostics **but never substitutes for absent bid/ask in the curve**.
- Every Yahoo row has `quote_ts=null` and `quote_timestamp_basis=unavailable`. `lastTradeDate` timestamps a trade, *not the quote*. Yahoo output is always `strict_asof=false`, `trading_probability=null`.
- When trusted provider timestamps become available, age-gate each quoted bid/ask, then demand verified spot time, an independently as-of-confirmed forward and market-calendar verification. Reject call monotonicity, convexity, tail mass and forward-mean failures.
- Atomic per-snapshot gzip JSON in a dedicated shadow directory; hash and conservative `persisted_available_at` in a post-write `manifest.jsonl`. Never touches live SQLite.
- Does not calculate or assert a Polymarket edge. Risk-neutral Q is **not** the physical P(Up).

## Tests

```bash
python -m pytest -q tests/test_spx_binary_shadow_collector.py
```

## Manual research run (only after resource budget approval)

```bash
python scripts/spx_binary_shadow_collector.py --output-dir /path/to/dedicated/research/sidecar
```

To collect repeatedly at least 300 seconds apart, explicitly add `--interval-seconds 900`. A running process consumes Yahoo requests and disk space; neither the PR nor deployment starts it. Validate provider rate limits, disk budget, data retention and I/O overhead before scheduling.

## Still required

1. Licensed or otherwise authoritative option quote feed with genuine quote timestamps and bounded publication delay, with recorded spot/forward time as of the same decision.
2. Authoritative early-close exchange calendar and official SPX close threshold for the matching Polymarket market.
3. Dataset accumulation, P vs Q calibration and walk-forward OOS tests including actual Polymarket YES/NO bid/ask and costs.
4. **Separate reviewed production integration** after shadow tests; existing broad-scope `adaptive_chain.py` is intentionally untouched.

The original 233 snapshots remain ineligible for strict Polymarket backtests.