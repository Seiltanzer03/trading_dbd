# Eight edge family adapters

`seiltanzer.edge_family_adapters.build_edge_family_evidence(snapshot)` reads one
frozen point-in-time snapshot and returns `families`, `components`, and
`economics_adjustments`. It performs no network, fit, provider, or trade write.
All eight source adapters exist. That does **not** mean eight independently
validated forecast sources are available in production.

## Current source audit

| Family | Existing repository source | Live adapter / limitation |
|---|---|---|
| Macro | `macro_t0_context` + official numeric/FOMC stores | Reads actual official releases and semantic/deterministic FOMC values from `macro_context_v1`; existing research authority is preserved until a measured action model is admitted. Official changes are not called surprises. |
| Event | Actual CPI/NFP/ISM/FOMC facts | Computes `actual-consensus` only for a separately verified consensus received before the same release, with the same period and unit. No licensed consensus collector or fitted net-action model was found. |
| Order flow | Existing candle/volume-profile delta; off-host Coinbase book/tape collector | Existing delta is not an exchange book/tape. Adapter computes observed Cont OFI only from two real updates within 60 seconds. Coinbase USD venue data is not the configured broker CFD; no validated proxy mapping or fitted action head is supplied. |
| Intermarket | Existing correlation matrix | Correlation remains context. Adapter computes log returns and lag from causal price endpoints supplied by a synchronized related-market collector. A trained return-window-specific action model is required; the current matrix is not one. |
| Positioning | Existing option OI/GEX; off-host official CFTC COT collector | OI does not reveal dealer positions. COT economic report date is not availability; first receipt is retained without inventing publication. Futures-to-CFD mapping and calibrated action head remain unavailable. |
| Value/carry | No point-in-time valuation or broker rollover collector found | Reads supplied valuation/factor-carry facts. Broker rollover is a signed outcome cost, never a separate directional score. |
| Option | Real-expiry and projected IV surface + chain age/quality | Existing IV fields remain Q context in one `instrument:option_distribution` family, consumed by quantitative_base. A separately received actual source and physical net-action calibration are required for an additional forecast. GEX is not observed dealer inventory. |
| Session | Existing UTC time-of-day mathematical inputs and US session context | Instrument timezone uses `zoneinfo`, including DST. Clock context alone does not assert trading hours. Holiday/early-close-complete PIT calendar and measured conditional action outcomes are required for a session forecast. |

To attach official data without fetching, the review integration can call:

```python
from seiltanzer.macro_t0_context import build_macro_t0_context

factory = getattr(getattr(engine, "passive", None), "_macro_data_factory", None)
if factory is not None:
    snapshot["macro_context_v1"] = build_macro_t0_context(
        factory, float(snapshot["captured_ts"]))
```

The helper reads the existing local stores. Missing stores remain explicit.

## Frozen source contract

Attach collectors' actual records to `snapshot.edge_family_sources[family_id]`.
Each family accepts one record or a bounded list of records. A common record
requires:

```json
{
  "source_id": "immutable-source-or-snapshot-id",
  "source_verified": true,
  "instrument": "NAS100",
  "observed_ts": 1780000000,
  "received_ts": 1780000001,
  "published_at": 1780000000,
  "quality": 0.8,
  "dependency_group": "same-underlying-information-family"
}
```

`available_at` may replace `received_ts`; either means actual information
availability. `report_ts` is the economic report date, not the date the trader
could read it. Source time, publication time and availability must be no later
than captured_ts and in valid causal order. Missing numbers are missing, not
zero. Source age thresholds are 45 days for macro, 4 hours for events, 60 seconds
for flow, 15 minutes for intermarket/session, 14 days for positioning, 1 day for
value/carry, and 30 minutes for options. A record can shorten its threshold.

Global macro/event records can specify `global_context:true`. Any other source
instrument must match the trade instrument or include a `proxy_mapping` with
`validated:true`, `mapping_id`, matching `source_instrument` and
`target_instrument`, and causal `validated_at`. A proxy is never silently
declared the traded CFD. Its collector must provide a measured quality estimate.

Family data fields:

| Family | Additional actual fields | Extracted features |
|---|---|---|
| Macro | Numeric `features` or official `macro_context_v1.numeric_macro.releases/candidate_vector` and FOMC records | The supplied macro feature IDs; official release/document IDs preserved |
| Event | `release_id`, `event_type`, `actual`, `period`, `unit`, exact `published_at`; consensus record with own source/clock, same release/period/unit and numeric `value` | `event.<type>.surprise`, `event.<type>.age_minutes` |
| Flow | `kind:exchange_order_book`, venue, previous_top/current_top with ts/bid_price/bid_size/ask_price/ask_size; or kind exchange_trade_tape with aggression volumes and window_start_ts | `flow.ofi`, `flow.ofi_over_depth`, `flow.depth`, `flow.spread`, `flow.aggressive_imbalance` |
| Intermarket | `linked_returns`: leader, start_ts/end_ts, start_price/end_price | `intermarket.<LEADER>.return`, `.lag_seconds`; exact return window preserved in provenance |
| Positioning | kind cot_report/fund_flow/observed_open_interest, published_at, report_ts, net_position, historical_positions with report_ts/available_at/net_position | `positioning.net`, `.percentile`, `.history_n` |
| Value/carry | kind valuation/factor_carry and numeric features; or broker_carry fields below | Actual supplied valuation features; broker carry produces economics metadata only |
| Option | Actual dated option source with numeric features and valid proxy mapping where needed | Actual supplied option features; chain transforms share one dependency group |
| Session | calendar_complete, calendar_id, calendar_available_at, session_id, session_open_ts/session_close_ts, optional numeric features | `session.minutes_from_open`, `.minutes_to_close`; actual session-state metadata |

An extreme position, correlation, raw OFI or IV level is not automatically mapped
to a management instruction. The effect sign must come from the calibrated
instrument/action model, not from an assumed economic relationship.

## Action forecast artifact admission

`snapshot.edge_family_models[family_id]` holds a frozen model or list of models.
No models are trained on the request path. The supported contract is
`edge-family-net-action-model-v1` with feature contract
`edge-family-observed-features-v1`. Required artifact fields are:

- family_id, exact instrument, model_version, dataset_sha256;
- component_id in mathematical_edge / active_edge / historical_llm;
- trained_at, train_end_ts, validation_start_ts, validation_end_ts;
- horizon_minutes, score_scale_r, optional regime (default ALL);
- validation: OOS_VALIDATED, point_in_time true, purged_split true, at least
  20 observations and 2 folds, positive proper_score_gain, costs_included true,
  outcomes OBSERVED_NET_ACTION_DELTA_VS_HOLD;
- action_models with separately validated action effects.

The last training target plus forecast horizon must precede validation start.
Validation ends before training/publication and all these times precede the
snapshot. Model age is bounded to at most 30 days. Validation on model scenarios
cannot replace observed held-out outcomes. These are minimum operational
admission checks, not a claim of statistical optimality or guaranteed profit.

A linear action head specifies `validated:true`, `intercept_r`, and coefficients
keyed by the actual extracted feature IDs. Its prediction is a net advantage
versus HOLD, divided by the artifact's declared score_scale_r and bounded to
[-1,1]. Intermarket return heads additionally require `feature_windows_sec`
matching the exact historical/live return windows, avoiding a 5-minute model
silently consuming a 60-minute return.

A conditional action head specifies:

```json
{
  "validated": true,
  "kind": "conditional_net_outcomes",
  "bins": [{
    "conditions": [{
      "feature": "session.minutes_from_open", "lower": 30, "upper": 90
    }],
    "sample_count": 60,
    "mean_delta_net_r": 0.08
  }]
}
```

The mean is from observed conditional **net action outcomes**, not from a
directional frequency converted into invented profit. Stored direction
frequencies may accompany a bucket, but cannot independently supply its action
score. A missing feature, stale source, wrong instrument/regime/window, invalid
split, or failed validation means no score. HOLD's delta baseline is exactly
zero. Stop/take actions require their own validated effect; a direction head does
not automatically imply EXTEND_TAKE or TIGHTEN_STOP.

Components include source IDs, dependency groups, oldest actual source clock,
expiry-preserving age limit, quality, model version and family forecasts. The
consensus source is retained alongside its release ID. All option-derived
forecasts retain one option_distribution dependency group. Duplicate artifacts
cannot add votes. Multiple families are quality-averaged **inside the existing
component budget**. The unified scorer must merge these with any already
materialized expert in that pool; it must not overwrite a mathematical price
head or allocate an additional nominal weight for every family.

The adapter supplies rankings only. The unified optimizer continues to apply
every mandatory CVaR/stop/position/strategy constraint to every candidate.

## Broker carry economics

A kind `broker_carry` record needs currency,
charge_currency_per_rollover, risk_currency_per_unit, and
charge_basis `per_unit_of_remaining_position`. Positive charge is a cost,
negative charge a credit. The adapter publishes the measured ratio as
`cost_r_per_rollover` and retains `next_rollover_ts` and
`included_in_policy_economics`.

It does not apply this adjustment itself. The outcome simulator must count
rollovers on each holding path, include the cost once, and recalculate both
Expected net and CVaR net. It cannot be applied twice or used as an extra vote.
The common candidate bank and historical replay now consume an optional frozen
`snapshot.broker_rollover_schedule` through `rollover_economics.py`. A verified
quote must match instrument, causal receipt, currency/R units, full horizon
coverage and explicit fills-before/after-rollover ordering. Each scheduled signed
charge applies once to the quantity still held on that path, including partial
closes and TIME_STOP. Expected net, CVaR net and paired uncertainty therefore use
the same net outcomes. Invalid declared schedules fail closed; charges already
included in base costs are not added twice. No actual broker quote collector is
available: missing quotes remain explicitly unknown, not evidence of zero carry.

## Verification

`tests/test_edge_family_adapters.py` covers each of eight measured family inputs
reaching action scores and each unvalidated artifact remaining at zero voting
authority. Further cases cover causal clocks, same-release consensus, candle
flow rejection, both sides of OFI, correlation context, future COT exclusions,
broker economics, shared Q-chain context, proxy mapping, purged/OOS/net evidence,
conditional session effects, DST, and duplicate artifact stability.
