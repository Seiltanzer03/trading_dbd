# Bounded real mathematical-edge source refresh

The runner can refresh all 13 configured instruments without making HTTP calls,
fitting models, or changing databases in a production request. The production
export remains indexed/read-only; independent collection and fitting run off-host.

```sh
python -m scripts.run_mathematical_edge \
  --sources mathematical_sources.json --refresh-sources \
  --sources-output refreshed_sources.json --cache-dir .math-source-cache \
  --source-days 60 --source-budget-seconds 360 \
  --output mathematical_edge_latest.json
```

Without `--refresh-sources`, `--sources` reproduces frozen input without network.
`refreshed_sources.json` contains the exact bars and collection failures necessary
to reproduce a report, while the report holds provenance, hashes and coverage.
Model search remains the existing bounded 15/30/60/120-minute search with untouched
held-out tests. Source availability is not evidence of an edge or net profitability.

## Providers and limits

| Instrument set | Fresh series | Explicit limitations |
| --- | --- | --- |
| BTCUSD, ETHUSD, SOLUSD | Coinbase Exchange BTC/ETH/SOL-USD candles | USD is a proxy for configured Binance USDT; basis mismatch is disclosed |
| Crypto fallback | Kraken USD OHLC | Latest 720 entries maximum; last uncommitted entry is excluded regardless of cutoff |
| 10 traditional instruments | Configured Yahoo ticker, 5m via yfinance | Index/futures/FX proxy, not broker execution series; last 60 days maximum |

Coinbase has at most 300 candles per request. Requests cover at most 299 five-minute
intervals and discard rows outside their range. Newest-first pagination is bounded
to 60 requests per instrument, 20,000 bars and 60 days. A shared conservative budget
spaces crypto requests by at least 0.35 seconds (under 3 requests/sec). Each HTTP
request has a maximum 12-second timeout, the shared collection budget is at most
420 seconds and yfinance requests are individually bounded. No retry loops,
redirect rerouting, credentials, alternate IPs or geographic-limit bypasses are used.
HTTP denial/rate limits/non-JSON responses are recorded, not accepted as candles.

Primary documentation checked 2026-10-02:

- [Coinbase candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles): bucket start timestamps, 300/request, history can have missing no-tick intervals.
- [Coinbase public rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits): current documented 10 requests/sec public limit; collector deliberately uses less than 3.
- [Kraken OHLC](https://docs.kraken.com/api-reference/market-data/get-ohlc-data): 720 latest entries, final current entry always present and uncommitted.
- [yfinance download](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html): five-minute interval, intraday depth limited to the last 60 days.

## Integrity and failure semantics

Every new source records provider, ticker, interval, causal completed-bar cutoff,
observed receipt timestamp, actual bar hash, first/last timestamps, total and
consecutive coverage, gap count, missing-bar policy and known proxy semantics.
Only positive finite valid OHLC on five-minute boundaries is accepted. No candle
is synthesized, forward-filled or interpolated. Coverage reports the latest
consecutive run rather than treating an isolated recent candle as fresh history.

Persistent caches are separate per instrument/provider, bounded and checked against
bar hashes, ticker/provider identity, timestamp validity, duplicates and the cutoff.
Coinbase reuses only its own cached bars plus a small overlapping refresh. Kraken
likewise accumulates only Kraken observations. A late Coinbase failure preserves a
real partial newest-first collection with an explicit error. A failed zero-page
refresh preserves original receipt clocks instead of claiming the cache was fetched
again. Yahoo fresh history replaces the old series rather than splicing sources.

Failures can retain a hash-verified same-provider cache, or the read-only exported
source, explicitly marked as fallback. Provider failures remain visible even when
fallback bars are available. If no legitimate source exists the 13-row matrix stays
`UNRESOLVED`, zero-weight and search-incomplete. A short Kraken sample is permitted
input, not automatically sufficient training evidence.

The production exporter no longer joins historical Yahoo/proxy candles with retained
direct bars of another price series. It exports historical provider bars alone or,
if absent, completed retained bars from one provider. Derived/partial minute bars,
mixed-provider five-minute groups and gaps are excluded. Exported hashes identify
the actual chosen series; original historical hashes remain separate metadata.

## Verification and remaining live proof

Unit tests cover bounded pagination, malformed/denied/HTML responses, deduplication,
unfinished Kraken candles, interrupted collection, hash-invalid/future caches,
explicit fallback, an immutable seed, offline reproduction and the complete
13-instrument matrix. Test candles are contract fixtures, never empirical evidence.
Actual successful source depth, training recency and crypto results must be read from
the runner artifact. Network access in this environment was previously unavailable
for Coinbase and returned HTML for Kraken; the implementation does not assert a
successful real crypto fetch until a legitimate runner fetch is observed.

## Crypto runtime admission: USD research is not USDT management

Coinbase/Kraken USD collection and chronological model searches remain legitimate
offline research. They do **not** confer a management scoring head for configured
Binance USDT. Returns are invariant to a constant multiplicative scale, not to a
time-varying USD/USDT or exchange-price basis. The generic 2bp movement and path
barriers are especially sensitive to this unmeasured transfer.

Before any price or path prediction, runtime requires one audited training source
whose validated-bar hash binds the model and proves the exact configured Binance
USDT venue/symbol and quote semantics. It also checks the actual causal live
Binance intraday authority. Missing legacy crypto provenance, USD/USDT mismatch,
another venue, unbound source hash or missing live authority results in zero math
contribution and no head/path prediction. The report retains historical findings
but marks supported USD models `DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED`.

No measured transfer mapping contract is implemented yet: `validated: true`, a
mapping name, an approximate peg or correlation assertion cannot override this
guard. The new USD collectors cannot satisfy exact Binance USDT admission, so any
supported crypto research result stays diagnostic pending measured transfer
validation or legitimate exact configured-source history. Existing bounded TradFi
working-proxy policy is unchanged by this narrow guard.
