# Historical validation without waiting for new observations

Software acceptance and Monte Carlo candidate selection do not establish a
historical trading advantage. Validate the exact deployed selector and execution
rules against the strategy on identical observed future paths.

## Existing evidence

The successful production audit run 36696646356 on 30 September 2026 used
commit 31c9db542341d21694e1ea61c585ceb1a1215051. Its v1 audit covered 208 resolved
reviews, with one qualifying review per trade for each hypothesis:

| Historical hypothesis | Independent trades | Mean gross paired delta |
| --- | ---: | ---: |
| Gamma flip stop | 4 | +0.165844R |
| Next-rung partial (old spike proxy) | 9 | -0.058710R |
| Half-horizon time stop | 2 | 0.000000R |

These are exploratory old hypotheses, without slippage. They are not the current
seven-action selector. Most reviews were excluded for path gaps, incomplete
horizons, ineligible anchors or duplicate trades. Neither 208 reviews nor the
positive gamma average establishes an advantage.

Source: https://github.com/Seiltanzer03/trading_dbd/actions/runs/36696646356

## Required procedure using existing history

1. Inventory the current DB and any available immutable Yandex Storage backup
   read-only. Record dates, instruments, independent trades, point spacing,
   complete future horizons, original trade geometry, T0 decision snapshots,
   option/source timestamps, and actual fill/cost provenance. A backup is useful
   only if it contains additional observations, rather than another copy of the
   same trades. Do not assume a missing source is zero or reconstruct its past
   values from today's option chain.
2. Freeze the exact selector, economic band, parameter rules and source gates
   before scoring. Replay the strategy baseline and management on the same path,
   respecting absorbing stop/take, ladder fills, BE, original risk, current
   remainder and frozen conditional order quantity. Evaluate the full sequential
   manager as well as individual actions; profitable isolated variants do not
   establish that the selector chooses them profitably.
3. Use only information available at each review. Price-only rules can be tested
   on sufficiently granular price history with their required T0 trade state.
   Gamma/option-wall decisions require contemporaneous anchors and provenance;
   gaps make those cases unavailable. Candle paths need explicit conservative
   treatment of unknown intrabar event order and fill slippage.
4. Report paired net delta per independent trade, tail loss/CVaR, drawdown,
   action frequency, baseline participation and concentration by instrument and
   period. Measured broker costs and stated cost sensitivity are separate; gross
   results must not be presented as net profitability.
5. Keep chronological development and held-out periods separate. Account for
   overlapping outcome horizons and correlated trades when resampling; do not
   treat repeated reviews of one trade as independent observations. Report
   uncertainty and multiple-policy selection rather than choosing the best
   result after seeing all outcomes.
6. Declare historical support only for the tested scope when a conservative
   held-out lower confidence bound for net improvement clears the predeclared
   economic threshold, risk constraints hold and results remain stable across
   periods and plausible costs. Report insufficient data or a negative result
   otherwise. Never loosen the test because the first result is inconvenient.

Waiting is unnecessary if sufficiently complete independent history already
exists. If the necessary T0 information or observations were never recorded,
existing candles can support a narrower price-based study, not validation of
the entire option-informed manager. No fixed trade count by itself proves an
edge or guarantees future profitability.
