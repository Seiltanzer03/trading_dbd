# CFTC observed open interest

Scope: original unified-edge plan §7 positioning, using the existing bounded
Legacy Futures Only CFTC response, without additional HTTP calls or providers.
Official field reference: https://publicreporting.cftc.gov/Commitments-of-Traders/Legacy_All/srt6-5q2f
(`open_interest_all`); https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm.

Emit a separate `observed_open_interest` record for the newest report and its
immediate predecessor, only when both actual counts are finite, nonnegative
integers. Missing/invalid optional OI refuses this record without discarding the
existing noncommercial net-position record. Never skip a missing immediate
predecessor to manufacture a change. Reuse the existing position history proof,
body hash, CFTC instrument, first-seen receipt and release dependency group.
No historical receipt/publication clocks are backdated.

OI level/history use `positioning.open_interest`,
`positioning.open_interest_percentile`, `positioning.open_interest_history_n`;
existing signed change uses `positioning.observed_open_interest.change` and
previous report age. Net-position features remain unchanged and cannot be
overwritten by OI, irrespective of record order. Negative OI is refused at
adapter/history admission while signed noncommercial positioning stays valid.

Unvalidated CFTC-to-CFD mapping remains refused. Zero model votes, source
freshness, risk/cost/geometry, immutable replay and OOS/purge floors stay intact.
No forecast, broker identity, execution or profitability is inferred from OI.

One root writer, one final independent review; existing authorization waives
repetitive design/plan approval pauses. Exact-tree mandatory CI and production
acceptance remain required before release completion.
