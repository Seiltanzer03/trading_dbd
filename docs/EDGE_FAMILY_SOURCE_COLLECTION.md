# Off-host edge-family source completion

`scripts/build_edge_family_sources.py --output /tmp/edge-family-sources.json`
collects a bounded actual-source bundle. Run outside the trading review path.
At most 13 public GETs, four workers, 12-second per-request timeout, 4 MB body
limit, no retries, credentials, trade writes, model fitting or added votes.
Every fetch retains official URL, SHA-256 of actual bytes and receipt timestamp;
failed requests and malformed responses remain explicit in `errors`.

The contract is `edge-family-source-bundle-v1`, with `production_authority:false`
and the existing high-risk manual-trader edge policy. Every instrument has all
eight readiness rows computed by the existing adapter, including unresolved
requirements. Availability of context is not OOS validation or profit evidence.

## Implemented actual inputs

* Coinbase Exchange BTC/ETH/SOL USD L1 books and latest trade page. Aggregated
  sizes are not multiplied by order count. API trade side is maker side, so
  aggressive buy volume removes maker sell. The bounded latest page is labelled
  sampled coverage, not a complete 60-second tape. A second causal L1 snapshot
  is used for OFI only when supplied by `--previous-bundle` within 60 seconds and
  with a larger exchange sequence. One book does not invent a previous update.
* Coinbase completed one-minute closes use the same two endpoints separated by
  five minutes across at least two related markets. In-progress candles are
  excluded, mismatched/absent endpoints do not fabricate synchronous prices.
  These are explicitly related venue returns, not claimed broker CFD prices.
* CFTC Legacy Futures Only reports for gold, silver and euro FX. Noncommercial
  net positions retain economic report dates. The API does not give an exact
  publication clock: publication is conservatively upper-bounded by first-seen
  receipt. Historical availability is first-seen now, NEVER backdated to a
  guessed Friday time. These records cannot supply features to earlier reviews.
* Official scheduled NYSE cash-core 2026 holidays and early closes, validated
  against the live year's actual table. DST is `America/New_York` zoneinfo.
  Cash session calendars are not broker CFD hours. Unsupported years/table
  changes are rejected rather than estimated; unscheduled closures are outside
  this calendar's scope.

Venue crypto, CFTC futures and NYSE cash mappings to broker instruments remain
`validated:false`. The adapter will reject them for scoring until measured
instrument mappings exist; collection does not bypass this guard.

`--existing-context` may supply frozen instrument `macro_context_v1` and actual
`edge_family_sources` from the project's existing official macro/option stores
or a legitimate configured provider. No model artifacts are imported. Source
verification, causal clocks, staleness and proxy admission remain adapter checks.
No licensed event consensus, broker carry quote, valuation data or trained
physical option/action head is fabricated. Missing carry remains missing cost,
not a zero charge. Those families remain honestly unresolved.

## Primary source specifications

* https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-book
* https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-trades
* https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
* https://publicreporting.cftc.gov/Commitments-of-Traders/Legacy-Futures-Only/6dca-aqww
* https://www.cftc.gov/MarketReports/CommitmentsofTraders/ExplanatoryNotes/index.htm
* https://www.nyse.com/trade/hours-calendars

Live availability must be reported from the actual off-host run, not from unit
fixtures. Local restricted-network timeouts are not evidence of provider outage.

## Production publication and review path

`edge-family-sources.yml` verifies all 13 configured instruments and all eight
readiness rows, then exports actual receipts. PR runs never publish or save
shared source caches. Trusted main runs publish atomically for the exact deployed
SHA; automatic deployment dispatches this collector alongside mathematical refresh.
The runtime reads at most 1 MB locally, admits at most 8 KB of selected source
facts, and performs no HTTP or fitting. Source clocks must precede both bundle
capture and review capture; stale or unvalidated instrument mappings are audit-only.
`collected_available` is separate from current runtime availability. No trained
models are imported by this loader. Existing official macro context is attached
from its normal local store, not overwritten by optional collector macro context.
