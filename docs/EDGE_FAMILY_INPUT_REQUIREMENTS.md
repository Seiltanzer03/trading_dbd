# Tasks 6/7: actual-input inventory (read-only code review)

Reviewed 2026-10-02 against `AGENTS.md`, original unified-edge plan §7/§9,
`UNIFIED_EDGE_REMAINING_IMPLEMENTATION.md`, workflow and current integration tree.
This report inventories repository evidence; it does not claim remote production
credentials, private account data or source subscriptions were inspected.

## Executing account and position units

**A real production identity/unit producer is absent. No already-configured
executing-account source was found in code or deployment configuration.** The
existing cost importer is a valid ingestion boundary, not that producer.

| Evidence | Concrete location | Consequence |
| --- | --- | --- |
| Independent identity required | `seiltanzer/execution_cost_context.py:38`, `validate_execution_cost_context`: snapshot `trade_identity.broker_id/account_id` must match document | The cost document cannot supply its own independent identity; missing fields fail closed |
| Independent units required | Same function, lines 75–85: `position_execution_units.currency`, `quantity_units`, `risk_currency_per_unit`, `quantity_basis=current_remaining_position` | Price geometry and a normalized fraction cannot establish money per contract or actual quantity |
| Runtime cost attachment exists | `seiltanzer/unified_edge_runtime_context.py:76`, `attach_unified_edge_context`; app route lines 1250–1254 | Pinned costs are loaded after snapshot construction; complete accepted costs trigger repricing |
| Snapshot producer lacks both fields | `seiltanzer/ai_verdict_base.py:233`, `build_snapshot`, snapshot literal around 301; v18/v19 wrappers extend this | Only local trade ID, strategy, geometry and normalized position state are produced |
| Trade input lacks execution units | `seiltanzer/app.py:361`, `TradeOpen`; `TradeFill:382`; `TradeEdit:396` | Inputs contain geometry and fills as fractions/prices, no broker/account/contract quantity |
| Account input is a planning ledger | `seiltanzer/app.py:411`, `AccountUpdate`; `seiltanzer/journal.py:95`, account table; `Engine._account_payload:523` | Name/phase/size/balance and recommended risk percent are not a verified broker account identity, account currency, or observed position risk |
| Position ledger is normalized | `seiltanzer/position_state.py:263`, `PositionLedger.state` output | `initial_position_fraction=1` and remaining fraction are not lots/contracts; changing fraction requires independently known original quantity |
| Settings only import cost evidence | `seiltanzer/config.py:203–206`, four context path/SHA settings | No executing-broker API/account adapter setting exists here |
| Public quote configuration | `seiltanzer/config.py:89–125`, `seiltanzer/data/feeds.py:89`, `_fetch_tradingview_quote` and `_fetch_tradingview_quote_ws` | OANDA/FPMARKETS/Swissquote labels identify price sources. They do not establish the user's execution venue/account |
| ACK explicitly preserves uncertainty | `seiltanzer/execution_ack_telemetry.py:40`, `record_execution_ack` | Server ACK timestamp is not broker fill timestamp; independent fill verification is false |

`rg` found `trade_identity` / `position_execution_units` only in cost consumers
and test fixtures, not a production assignment. The fixtures in
`tests/test_execution_cost_context.py:12–34` are synthetic test values.
Repository deployment/workflow search found no OANDA/MT5/cTrader/IBKR executing
account integration; TradingView authentication is quote access, not broker
account access. No secrets were printed or inferred.

**Bounded next implementation:** add a separate pinned, bounded position/identity
import boundary, validate immutable source provenance/clock, local trade-to-broker
position mapping, instrument/direction, currency, contract unit and original risk,
then attach it before cost loading. Preserve mismatch/stale/partial/unconfigured
audits and keep costs unavailable unless independently matched. Do not derive
identity from the cost file or recommended account risk. A connector can follow
once the real broker and a lawful read-only source are known. This can prepare the
producer contract but cannot truthfully close live-source integration on its own.

Unavoidable inputs: executing broker, stable account identifier (an opaque ID is
enough), read-only export/API availability and access, instrument contract/lot
unit and multiplier, account/cost currency, actual quantity and broker-position
mapping to local trade, initial risk definition/FX conversion where needed,
timestamps/provenance for fee/spread/slippage/rollover, and actual fill clocks for
measured latency. User-supplied planning values must remain explicitly manual,
not independently verified broker evidence.

## Eight families: actual fields versus missing upstream evidence

Shared runtime references: `edge_family_adapters.py` `_meta:86` verifies source,
PIT clocks, staleness and mappings; `_action_models:394` requires OOS, purged split,
20+ samples, 2+ folds, positive proper-score gain and costs included. Facts do not
become action votes merely because feature code exists.

| Family | Actual available fields and implementable bounded work | Missing evidence / limits |
| --- | --- | --- |
| Macro | `_official_macro:157` reads official CPI/NFP/ISM candidate vectors and FOMC semantic/deterministic payloads. `macro_numeric_data.py:_candidate_vector:539/research_context:592` and historical/offhost stores preserve release context. Can retain actual changes, release ages and longitudinal features when causal releases exist. | No corporate earnings-expectation feed, market rate/yield expectations curve or growth-consensus source in this family collector. NFP average hourly earnings are wages, not index earnings expectations. Historical releases/revisions require authentic vintages. |
| Event | `_event:202` computes release-minus-prepublication consensus and event age when exact same-period/unit/release clocks exist. Official FOMC text/semantic stores can support document comparison if prior causal documents are retained. | Collector produces no event records. No licensed/pre-captured consensus producer, dedicated text-novelty feature, or exact-release-aligned reaction feature producer. Reaction can be implemented prospectively from observed paths only with actual timestamps; present-first-reaction is not future return. Never infer consensus from subsequent actuals. |
| Order flow | `_order_flow:229` already computes sampled top-book OFI, depth, spread, aggressive-volume imbalance. `edge_family_sources.py:205/237/272/314` parse Coinbase/Binance book/tape. | `build_bundle:451` currently fetches Coinbase USD products, whose `_proxy:199` mappings to configured Binance USDT are explicitly unvalidated. Binance parser existence is not live collection; geographically denied Binance REST remains disabled. No broker CFD depth/tape or price-impact sequence producer. 600s collection cannot guarantee 60s flow freshness. |
| Intermarket | `_intermarket:257` exposes synchronized log return and observation lag. `build_bundle:565–594` joins Coinbase crypto closes on common completed 5-minute endpoints. Can implement relative returns between actual leaders and longitudinal lagged features from retained histories. | Existing `lag_seconds` measures quote age, not predictive lead-lag. No constituent breadth feed; no historical target/leader PIT dataset for predictive lag/relative-value models. BTC/ETH/SOL context cannot be renamed equity/FX breadth. Target CFD equality is explicitly not asserted. |
| Positioning | `parse_cot:349` supplies real CFTC noncommercial long-minus-short and historical net positions, with first-seen receipt availability; `_positioning:272` calculates percentile. Existing Deribit `data/deribit.py:198/361` really reads option OI; existing G1 short-horizon feature contract computes OI-based context. Can retain dated venue OI observations and changes when stable chain/expiry/strike/receipt identity exists. | COT-to-XAU/XAG/EURUSD mapping is unvalidated. COT report date is not publication date; history first-seen today cannot be backdated. No fund-flow producer. Option OI is neither dealer inventory nor automatically total futures OI, and must share option dependency group rather than create a second independent confirmation. |
| Value/carry | `_value_carry:295` accepts `valuation`/`factor_carry` facts and explicit `broker_carry` charge/risk units; carry affects outcome economics. | Collector has no valuation, forwards or broker rollover producer. Recommended risk/account balance does not establish carry units. Need authentic dated valuation/forward inputs and independently bound executing-account swap evidence. Do not re-count rollover already charged elsewhere. |
| Option | `_existing_options:315` reads actual IV surface real expiries and projected 24h ATM IV/skew; `policy_manager.evidence.iv_surface/data_quality` identifies source age. Existing Deribit/Yahoo option pipelines provide actual chain fields. Can archive genuine received chains and compute changes of matched skew/term structure from them. | Current family projection infers observed time from snapshot minus age, leaves received time null and marks context-only. Physical forecasts/VRP require actual realized forward outcomes/calibration; Q, OI and model dealer proxies do not establish physical edge or dealer positions. Receipt-aware history and verified proxy mapping are necessary for admitted training features. |
| Session | `_session:347` provides true timezone/DST local context; `parse_nyse_calendar:409` parses official 2026 scheduled cash core/holidays/early closes. Can add exact Binance crypto UTC-day features as 24/7 context when clearly distinguished from a cash session, or release applicable calendars with provenance. | NYSECASH-to-CFD mappings are explicitly unvalidated; no GER40/UK100/JPY100/metal/FX broker-session calendars. Local weekday/hour alone cannot prove market open. Current source loader additionally rejects future schedule timestamps (`edge_family_source_runtime.py:94–105`); a validated planned-calendar contract would be needed, not weakening future-fact validation generally. Conditional overnight/continuation features require actual session-aligned paths and OOS outcomes. |

Collector deployment: `.github/workflows/edge-family-sources.yml:49` calls
`scripts/build_edge_family_sources.py` without `--existing-context` or
`--previous-bundle`. Thus preserved/imported macro/event/value records supported
by `build_bundle(existing=...)` are not themselves configured producers in this
workflow. The collector declares `models_produced=0`, exact public publication
times not inferred, and authority false (`edge_family_sources.py:624–630`).
`edge_family_source_runtime.py:38` loads an exact-SHA local bundle, then admits
only real PIT/mapping-valid facts and preserves rejections.

## Finite implementation recommendation

1. Continue the authorized independent longitudinal-export/model-producer and
comparison-report work using genuinely frozen existing features/outcomes.
2. Close Task 6 structurally with independent position evidence import + snapshot
attachment and explicit missing-input status; real broker activation remains a
named external input dependency until actual source evidence is provided.
3. For Task 7, add provenance-preserving matched-history deltas/relative returns
and actual venue OI context where original observations support them. Publish
per-feature availability/required field instead of adding empty forecast adapters.
4. Keep earnings forecasts, consensus, fund flows, valuations/forwards, CFD book,
applicable calendars/mappings and physical/VRP/dealer data explicitly unresolved.
No provider choice, subscription, license, account ownership or favorable model
validation can be guessed from the repository.

This inventory did not mutate production, place orders, call paid APIs or fetch
new public data. Report-only file added; no behavioral tests required.
