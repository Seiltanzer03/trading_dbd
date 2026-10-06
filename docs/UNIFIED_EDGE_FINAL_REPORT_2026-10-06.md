# Итоговая сверка unified edge

Сводка сохранённых результатов. Прибыльность и оптимальность весов не доказаны.

SHA целевой версии: `0cefc1acdb90db4af5f4c62fc813382a621f111a`.

Исходные отчёты: math=EXACT_REPORT_SHA; comparison=HISTORICAL_OTHER_SHA.

Их времена создания и SHA сохранены в JSON. Старые отчёты не являются проверкой нового выпуска.

## Математический поиск

| Инструмент | Поиск завершён | Статус | Роль по отчёту | Поддержанные выбранные головы |
| --- | --- | --- | --- | --- |
| NAS100 | True | WORKING_SUPPORTED | GENERIC_PATH_MANAGEMENT | downside_excursion:15, early_first_touch:15 |
| SP500 | True | WORKING_SUPPORTED | GENERIC_PATH_MANAGEMENT | early_first_touch:15 |
| US30 | True | WORKING_SUPPORTED | TIME_STOP_REDUCE_TAKE_ONLY | movement:60, upper_before_lower:30, early_first_touch:15 |
| GER40 | True | WORKING_SUPPORTED | TIME_STOP_REDUCE_TAKE_ONLY | movement:15 |
| UK100 | True | WORKING_SUPPORTED | GENERIC_PATH_MANAGEMENT | downside_excursion:15, early_first_touch:15 |
| JPY100 | True | NO_SUPPORTED_ADVANTAGE_YET | NO_WEIGHT | нет |
| XAU | True | WORKING_SUPPORTED | TIME_STOP_REDUCE_TAKE_ONLY | movement:15, upside_excursion:15, early_first_touch:15 |
| XAG | True | WORKING_SUPPORTED | GENERIC_PATH_MANAGEMENT | downside_excursion:15 |
| EURUSD | True | WORKING_SUPPORTED | TIME_STOP_REDUCE_TAKE_ONLY | movement:15, downside_excursion:15, early_first_touch:30 |
| USDCAD | True | WORKING_SUPPORTED | DIRECTION_SOFT_RANKING | direction:15, movement:15, downside_excursion:15, upside_excursion:15, upper_before_lower:15, early_first_touch:60 |
| BTCUSD | True | WORKING_SUPPORTED | DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED | movement:15, upside_excursion:15, early_first_touch:15 |
| ETHUSD | True | WORKING_SUPPORTED | DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED | movement:30, upside_excursion:15, early_first_touch:15 |
| SOLUSD | True | WORKING_SUPPORTED | DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED | downside_excursion:15, early_first_touch:15 |

Полная матрица инструмент × цель × горизонт находится в JSON. NOT_REPORTED означает отсутствие результата в исходном файле. Поддержанная голова не означает фактический runtime-вес; mapping и возраст могут ограничивать применение.

## Сравнение схем

Сохранённых разборов: 32.

| Схема | Парных разборов | Разных сделок | Средняя ΔR против HOLD | CVaR10 воспроизведения |
| --- | --- | --- | --- | --- |
| balanced | 4 | 4 | 0.0 | -1.01 |
| legacy_control | 4 | 4 | 0.0569375 | -1.01 |
| llm20 | 4 | 4 | 0.0 | -1.01 |
| quant100 | 4 | 4 | 0.0 | -1.01 |
| without_active_edge | 4 | 4 | 0.0 | -1.01 |
| without_current_llm | 4 | 4 | 0.0 | -1.01 |
| without_historical_llm | 4 | 4 | 0.0 | -1.01 |
| without_mathematical_edge | 4 | 4 | 0.0 | -1.01 |
| without_quantitative_base | 4 | 4 | 0.0 | -1.01 |

Модельные Expected/CVaR, издержки, частота вмешательств, ablations, смены решения и наблюдения исполнения сохранены отдельно в JSON, если есть во входном отчёте. Replay на фактической траектории остаётся контрфактическим расчётом; он не становится broker fill.

## Ограничения

- Report generation is not a new search or evidence of current production activation.
- Missing target/horizon evidence stays NOT_REPORTED; unsupported models get no inferred runtime vote.
- Generic 2bp path targets are not the actual trade stop/take geometry.
- Statistical support does not prove positive net management value.
- Model scenarios, observed path counterfactual replay and actual execution observations are separate.
- Overlapping reviews are not independent trades or a settled portfolio equity ledger.
- A report expected_sha is not proof that every old frozen review was produced by that code.
- No live orders, paid LLM calls or provider requests were performed.
