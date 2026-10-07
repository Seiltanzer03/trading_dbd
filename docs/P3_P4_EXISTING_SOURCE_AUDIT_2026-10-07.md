# P3/P4: конечная проверка уже опубликованных входов, 2026-10-07

Результат этого ограниченного пакета: **P3 EVIDENCE-GATED**, **P4 UNAVAILABLE
для broker-CFD forecast**. Источники исследованы по всем 13 инструментам;
моделей/новых прогнозов/активаций не создано. Это не вывод об отсутствии сигнала
в полном архиве. Исторические joins из P2 остаются отдельной проверкой.

## База и предел работы

Использован существующий artifact11480592477, source publication run37617811579,
exact main SHA b41a5a1590e84c4146279104ffa67c0a7da7712c.
Capture1791374394.2332888 (2026-10-07 14:59:54 Москва), models_produced=0,
collection errors=[]; ZIP digest проверен, численные результаты и хеш исходного
bundle в соседнем JSON. Источники свежи относительно capture, а не обязательно
момента чтения отчёта. Срок годности order flow60s не продлевается.

Это новый осмотр ранее не разобранного source bundle, а не повтор frozen32
поиска. Новая acquisition/DB export, fitting, LLM, production writes и полный CI
не запускались. Runtime-код не менялся; отчёт сохраняется в документационной
ветке без нового merge/deploy. Сырые account/broker records не публикуются.

## P3: межрынок и сессии

Каждый из 13 targets имеет один intermarket packet и 16 принятых factual
features. Источники — завершённые Coinbase crypto bars, returns, lag,
relative returns и breadth. Related crypto context не является котировками
NAS100/XAU/другого executing instrument и не доказывает прогнозную связь.
Все 13 intermarket forecasts unavailable; source availability не даёт голоса.

NYSE официальный cash calendar получен и разобран. Его packets для
NAS100/SP500/US30 отвергнуты с INSTRUMENT_OR_PROXY_MAPPING_UNVALIDATED:
NYSE cash hours не подтверждают broker-CFD trading session. Для остальных
10 session packets нет. Session features/forecasts=0 на всех 13. Производный
clock context требует HOLIDAY_AND_EARLY_CLOSE_CALENDAR_REQUIRED; локальная
зона времени сама по себе не подтверждает рабочую сессию/holiday/DST calendar.
Новые DST-transition испытания в этом read-only пакете не выполнялись.

| Инструмент | Межрыночные features | Session packets | P4 packets | Прогнозы P3/P4 |
| --- | ---: | ---: | ---: | --- |
| BTCUSD | 16 | 0 | 2 | unavailable |
| ETHUSD | 16 | 0 | 2 | unavailable |
| EURUSD | 16 | 0 | 0 | unavailable |
| GER40 | 16 | 0 | 0 | unavailable |
| JPY100 | 16 | 0 | 0 | unavailable |
| NAS100 | 16 | 1 | 0 | unavailable |
| SOLUSD | 16 | 0 | 2 | unavailable |
| SP500 | 16 | 1 | 0 | unavailable |
| UK100 | 16 | 0 | 0 | unavailable |
| US30 | 16 | 1 | 0 | unavailable |
| USDCAD | 16 | 0 | 0 | unavailable |
| XAG | 16 | 0 | 0 | unavailable |
| XAU | 16 | 0 | 0 | unavailable |

Порог causal training/OOS не изменён. Обучать повторно на прежних нулевых
admitted action rows не требуется. Готовый исторический dataset для P3 этим
единичным capture не доказан; не выполнялся новый исторический model search.

## P4: реальные наблюдения и ограничение источника

BTCUSD/ETHUSD/SOLUSD имеют по два packets: **один book с двумя фактическими
последовательными top observations и один tape page**. Это не два полных
стакана, не непрерывный incremental OFI и не два независимых evidence families.
Оба packets используют один dependency group book_tape для каждого venue asset.

| Coinbase asset | Интервал top observations, s | Trades в page | Полнота tape window |
| --- | ---: | ---: | --- |
| BTCUSD | 2.296608 | 437 | false |
| ETHUSD | 3.836987 | 420 | false |
| SOLUSD | 2.300446 | 38 | false |

Во всех трёх случаях source mapping validated=false; adapters отвергли оба
packets с INSTRUMENT_OR_PROXY_MAPPING_UNVALIDATED. Поэтому доступных broker-CFD
flow features/forecasts нет. Остальные 10 targets не имеют order-flow packets.
Coinbase exchange observations не называются брокерными исполнениями/стаканом.
Пакет заканчивается с этим конкретным ограничением; модель effects/costs не
выдумывается, издержки и admission не ослабляются.

## P5: bounded проверка позиционирования и value/carry

Preflight того же bundle: CFTC для XAU/XAG/EURUSD FETCHED/PARSED, по одному
cot_report и observed_open_interest packet. У каждого COT127 historical entries,
OI1 historical entry. Историческая доступность FIRST_SEEN_NOW_NOT_BACKDATED,
publication clock FIRST_SEEN_UPPER_BOUND_NOT_EXACT_PUBLICATION; дата отчёта
не подменяет дату известности. Mapping CFTC contract→broker CFD не подтверждён,
все positioning packets отвергнуты, features/forecasts=0. У остальных10 packets0.

Value/carry packets/features/forecasts=0 у всех13 **в этом bundle**. Это не
проверка отсутствия фактического cost import у executing broker. Дополнительно рассмотрены уже сохранённые32 frozen reviews из P2, без
повторного pipeline: top-level broker_rollover_schedule, execution_cost_context
и position_execution_units отсутствуют на всех32. Сохранённые rollover audit
имеются у17 candidate rows, все BROKER_QUOTE_UNAVAILABLE; execution context
audit на2 snapshots сообщает BROKER_EXECUTION_COST_CONTEXT_UNCONFIGURED.
Candidate count не равен числу независимых сделок.

Read-only code inspection: rollover_economics.frozen_rollover_schedule требует
source identity, causal observed/received clocks, currency/risk per unit,
полную horizon coverage и явный included_in_base_costs. Включённые в базовые
расходы charges повторно не применяются. execution_cost_context учитывает
spread/commission/slippage/manual latency отдельно от rollover; отсутствующие
компоненты не становятся измеренными нулями. Это осмотр существующего
контракта, не новый behavioural test или доказательство реальных broker costs.

P5 EVIDENCE-GATED в этих ограниченных входах: фактических quote/units/causal
receipt для вычисления нового carry нет. Расчётные expected_rollover_cost_r
не заменяют quote и обязательный cost audit. Runtime-код и gates не изменены,
carry не подмешан в голос модели. Новые источники/лицензии не добавляются.
Следующий P6: существующая options calibration/Q→P и identity readiness;
повторный поиск без новых данных запрещён. P6 этой проверкой не закрывается.
