# Forecast outcome contract

Version: `forecast-outcome-f1-v1`.

For a forecast captured at `T0` with horizon `H`, only the independently stored
market path in `[T0, T0+H]` is admissible. Outcomes are mutually exclusive:
`take`, `stop_or_be`, `no_touch`, `censored`.

- A barrier first touched after H cannot label the forecast.
- NO_TOUCH requires observed market-path coverage through H.
- If a manual close ends observations before H, the outcome is CENSORED.
- If market observations continue independently after position closure, they may
  resolve the original market-path question and remain distinct from trade P&L.
- Lifetime trade MFE, final `result_r`, close notes and later decisions are not
  inputs to the resolver.
- BE state and active risk barrier are frozen as of T0 and then evolve only from
  the admissible future path.

`trade_market_path` is the canonical per-trade series; forecasts slice it by
prediction timestamp and horizon rather than duplicating paths per review.
Research source histories use the `source-as-of-f1-v1` adapter before rolling,
derivative or regime features are derived.

