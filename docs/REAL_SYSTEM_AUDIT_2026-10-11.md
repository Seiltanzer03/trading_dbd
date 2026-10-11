# Real system audit — 2026-10-11

Scope: cached production HTTP, current exact-SHA publications, and source → model → expert → decision admission code. No new verdict, order, research search or broker-cost collection. Baseline main `db1cc3c6676563d4346ba2774a687034357205b3` has the same tree as local accepted baseline `36aa3f1`.

## Findings and corrections

1. Extra-family adapters rejected `synthetic=True`, but other demo markers could survive into a validated forecast and vote. Reproduced with `demo`, `is_demo`, `synthetic_demo`, `contract_fixture`; extended coverage includes `synthetic_data_used`, nested records, container metadata, mathematical training/live source and expert overrides. One bounded admission guard now rejects simulated evidence at its owning boundary. An invalid optional source/model does not erase unrelated genuine inputs. Explicit demo mode and test fixtures remain available as such.
2. Yahoo streaming discarded PricingData's provider time and substituted receipt time; instrument/proxy feeds repeated the substitution. Actual installed protobuf schema uses field3 `sint64` milliseconds. Decoder now preserves that clock. Missing/future clocks and nonfinite/nonpositive prices cannot become fresh; out-of-order frames cannot overwrite a newer quote. Native crypto proxy copies its observed quote time and freshness instead of inventing a new timestamp.

## Actual readiness, not contract completion

| Part | Observed evidence | Limit |
| --- | --- | --- |
| Production mode | `/api/state`: `demo=False`; actual delayed Yahoo/QQQ data | A cached quote is not a current authoritative broker quote; weekends and failed broker retrieval remain explicit |
| Mathematical models | Real historical bars and exact-main publication, run38124846822 | Supported heads are not evidence of positive out-of-sample gain for every head; crypto venue/quote identity must match |
| Eight additional families | Published source bundle490328bytes, run38124848348; real completed related-market returns | **0 admitted trained family forecasts in this bundle**. Fact delivery is not forecast readiness |
| Source mapping | NAS cash session, CFTC proxies and Coinbase USD tape/book recorded | These cannot acquire CFD/Binance USDT authority without validated instrument mapping |
| Runtime source reader | Selected records NAS4321bytes, XAU3466bytes, crypto5679bytes at publication cutoff | Existing8k budget fits these records; no evidence of the hypothesized budget blockage |
| Current/history LLM | Existing source-bound interpretation and matched-history contracts | Missing inputs/matches receive no invented independent forecast or weight |
| Scenario/visual analytics | Cone, lattice, strike landscape, IV/proxy and volatility fallbacks disclose modeled/indicative roles | Scenario output is not an observed path or proven probability. GEX does not observe dealer positions |
| Decision and management | One risk-constrained comparison; twelve candidate actions and manual confirmation path | An action can be blocked by missing authority, geometry or evidence. Candidate existence does not prove live execution of all12 |
| Weights | Availability, freshness, dependence and declared budgets remain enforced | Equal/nonzero weights cannot be forced for missing experts; positive trading edge is not established by normalized weights |

Production HTTP and source bundle were read into ignored audit scratch; private state and trade records are not committed. Source-reader reproduction used the bundle's original capture clock, not today's clock to relabel stale data as fresh. No readiness hypothesis is promoted without reproduction.

## Verification and finish boundary

Behavioral failures were observed before implementation. One independent final review found5Important gaps: macro vector/release-container markers, registry definition metadata, snapshot-level expert admission, runtime instrument-container metadata, and a malformed optional model matrix discarding an unrelated registry. All were reproduced (14additional RED cases) and fixed in one pass; no repeated review. Registry definitions retain their explicit budget but simulated definitions cannot vote; mandatory quantitative/risk calculations survive a simulated snapshot. Invalid optional models are rejected locally and do not erase a genuine registry.

Final owning profile: **839passed in7.84s**, including existing real-input contracts, source/runtime/model/ensemble, stream, crypto/Bybit, training/dataset, active-edge and snapshot materialization. This proves these software regressions, not forecast profitability or complete family readiness.

Release completion still requires full final-tree CI, then actual exact-SHA deploy/readiness/public smoke and math/source/FOMC publication receipts. Those immutable receipts are recorded in the release PR and progress ledger once available. Until then this document is an implementation audit, not a production completion claim. Remaining family model/data gaps above are deliberately open; this integrity package does not manufacture them.
