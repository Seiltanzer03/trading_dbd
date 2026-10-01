# Causal working regime context

`snapshot.edge_regime` uses contract `edge-regime-working-v1`. The companion
`snapshot.market_regime` is `TREND`, `RANGE`, `STRESS`, `EVENT`, or `UNKNOWN`.
This is deterministic management applicability context. Thresholds are explicitly
working and uncalibrated; no trained predictor, profit proof, independent vote,
dynamic weight authority, automatic execution, or hard-risk exception is added.

The bounded classifier requires 61 consecutive completed direct one-minute bars
for the current instrument, all ending at or before the frozen T0. It rejects
missing source authority, duplicate/gapped clocks, stale prices older than three
minutes, inconsistent OHLC values, synthetic/derived data, and unvalidated
proxies. Future and incomplete bars are ignored. Source observation must cover
the final completed bar and source receipt must occur at or before T0.

The source authority is captured when the actual provider fetch succeeds. Its
contract is `completed-direct-minute-authority-v1`; it records source ID, source
symbol/instrument, completed-bar observation time, actual receipt time, interval,
and direct/proxy/derived status. It does not inherit the separate live quote's
authority. Instrument changes and fetch failures clear that authority. Provider
responses for an instrument changed during the request are discarded under the
same lock as the instrument change.

The currently admitted direct subset is zero-offset Yahoo `EURUSD=X` and
`CAD=X` for the exact configured FX pairs. Their indicative provider quality is
0.75, and they are explicitly not broker execution bars. Yahoo cash indices
versus broker CFDs, futures/spot metals, all offset series, and Binance USDT
versus the USD-labelled instruments remain proxy context and yield `UNKNOWN`.
This is an explicit source limitation, not a completed regime implementation
for every feed. Demo data never acquire actual-source authority.

Working price thresholds use the last hour of close-to-close log returns:

| Regime | Working condition |
|---|---|
| STRESS | Last 15-minute RMS is at least 2.5 times the prior 45-minute RMS and at least 0.1 bp/minute; or the final absolute return is at least 2 bp and four times that baseline |
| TREND | Absolute hourly return is at least 2 bp and path efficiency `abs(sum returns)/sum(abs returns)` is at least 0.65 |
| RANGE | Path efficiency is at most 0.35, including a verified constant-price window |
| UNKNOWN | The working thresholds do not distinguish a transition, or source/clock requirements fail |
| EVENT | A verified release was actually published and received in the last 30 minutes, before T0, while admissible direct price context remains available |

STRESS has precedence over TREND/RANGE. EVENT retains the underlying
`price_regime`, price features and causal clocks. It detects observed release
presence only; it invents neither scheduled events nor a consensus surprise.
Official frozen macro releases and explicitly verified instrument/global event
sources are accepted only with ordered publication/receipt clocks.
`refine_regime_with_events(snapshot)` attaches freshly loaded local macro
observations to the existing frozen price classification without re-reading a
feed or changing T0. Missing direct price authority cannot be rescued by an event.

The audit retains feature values, working thresholds, quality, source authority,
bar/receipt clocks, and at most eight observed releases. Quality shrinks with
price age; it is not a regime probability. Experts using regime-specific
applicability must validate outcomes under this exact contract separately.
