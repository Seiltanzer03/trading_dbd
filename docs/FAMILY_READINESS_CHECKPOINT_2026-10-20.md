# Eight-family readiness checkpoint

Historical artifact12174446490 / publication39226831296 was read without a
new training run. ZIP SHAce2dc4b9d2412b873eeb87ffed1370c4e54c4a73dfb27d7debd365b3766c3d8a.
It contains25 fitted family/instrument groups;13 have at least one historically
working-supported direction or movement head. It explicitly reports
new_observations=false and market_forecast_not_net_action_profit=true.

| Family | Fitted groups | Historically supported groups | Exact unresolved dependency |
| --- | ---: | ---: | --- |
| macro | 0 | 0 | Ten targets: NO_HEAD_WITH_CAUSAL_VALIDATION |
| event | 0 | 0 | Ten targets: CAPTURED_EVENT_FEATURE_HISTORY_MISSING; no invented pre-release consensus |
| order_flow | 0 | 0 | Ten targets: CAPTURED_ORDER_FLOW_FEATURE_HISTORY_MISSING; candle pressure is not observed book/OFI |
| intermarket | 10 | 5 | Runtime still requires synchronized completed inputs and admitted current matching |
| positioning | 0 | 0 | Ten targets: CAPTURED_POSITIONING_FEATURE_HISTORY_MISSING |
| value_carry | 0 | 0 | Ten targets: CAPTURED_VALUE_CARRY_FEATURE_HISTORY_MISSING; no verified broker swap invented |
| option | 5 | 1 | Four insufficient nonoverlapping histories; JPY100 option capture history absent |
| session | 10 | 7 | Runtime source/calendar checks remain; movement/direction is not proven action profit |

BTCUSD/ETHUSD/SOLUSD are SOURCE_UNAVAILABLE in all eight families because
TARGET_CONFIGURED_SERIES_MISMATCH, independently of the ten-target counts above.
The configured universe remains13 instruments; unsupported identity is not
silently replaced by another series.

| Instrument | Supported intermarket head | Supported option head | Supported session head |
| --- | --- | --- | --- |
| NAS100 | none | direction, movement | none |
| SP500 | direction | insufficient history | movement |
| US30 | none | insufficient history | direction, movement |
| GER40 | none | insufficient history | direction |
| UK100 | none | insufficient history | none |
| JPY100 | direction | captured history missing | none |
| XAU | direction | none | movement |
| XAG | none | none | movement |
| EURUSD | movement | none | movement |
| USDCAD | direction | none | movement |

“None” means the fitted heads did not pass their historical support condition;
it does not mean a measured return or a value of zero. A supported movement
head does not automatically prefer CLOSE, stop adjustment or any other action.

For the two actual NAS100 reports of19October, current mathematical pool weights
were2.8178% and2.0559%, but selected stop-action mathematical contributions were
zero: the admitted option preference abstained on those actions; session had no
admitted action preference. No artificial nonzero vote was added.

Current publication/source matching, the next live family interpretations and
their actual selected-action contributions need a new exact-SHA runtime receipt.
Those measurements must not be inferred from the archived25/13 counts. The
remaining operational sequence is sustainable history compaction, exact-SHA
acceptance and existing EDE/source continuation; G.1 statistical gates are intact.
