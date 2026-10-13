# Реальное покрытие unified edge — 13.10.2026

Источники: actual mathematical-edge run38320486597/artifact11779132085; source run38366233047/artifact11796768426; SHA26c9e437. Инвентаризация публикаций, не текущего production-вклада.

{'current_run_fitted_models': 25, 'market_forecast_not_net_action_profit': True, 'retained_fitted_models': 0, 'trained_models': 25, 'working_supported_models': 17}
Все25 fitted-групп проверены существующим runtime validator; contract/hash errors: [].

| Инструмент | intermarket | session | option |
| --- | --- | --- | --- |
| NAS100 | direction:30m | movement:15m | fitted, OOS без перевеса |
| SP500 | movement:60m | movement:15m | INSUFFICIENT_HISTORY |
| US30 | movement:60m | direction:60m, movement:60m | INSUFFICIENT_HISTORY |
| GER40 | fitted, OOS без перевеса | direction:30m | INSUFFICIENT_HISTORY |
| UK100 | fitted, OOS без перевеса | fitted, OOS без перевеса | INSUFFICIENT_HISTORY |
| JPY100 | direction:15m | fitted, OOS без перевеса | HISTORICAL_FEATURES_UNAVAILABLE |
| XAU | movement:60m | movement:120m | fitted, OOS без перевеса |
| XAG | direction:120m | movement:120m | direction:15m |
| EURUSD | movement:30m | movement:30m | fitted, OOS без перевеса |
| USDCAD | direction:30m, movement:60m | movement:120m | fitted, OOS без перевеса |
| BTCUSD | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE |
| ETHUSD | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE |
| SOLUSD | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE | SOURCE_UNAVAILABLE |

У остальных пяти семейств нет пригодной новой fitted-группы в этом опубликованном конечном проходе:
- macro: официальный архив содержит0 CPI/NFP release features и по2 ISM manufacturing/services; нет причинных train/validation/test folds. Официальные текущие факты — контекст, не обученный прогноз.
- event: нет сохранённой причинной event feature/outcome history и pre-event consensus. Наблюдённая реакция не будущий edge.
- order_flow: Coinbase book/tape — площадочный proxy, не CFD стакан; историческая последовательность и transfer mapping отсутствуют.
- positioning: нет пригодной PIT COT/OI feature history для калибровки. Source доступность и модель — разные стадии.
- value_carry: нет PIT valuation/carry feature history; новый broker-cost модуль исключён пользователем.

Crypto Coinbase USD → configured Binance USDT mapping не подтверждён; price diagnostics не дают рабочий голос. JPY100 имеет intermarket15m, что не доказывает применимость остальных голов.

Исправления runtime: original input clock переживает normal/emergency compaction; frozen origin не переносится на время завершения builder. Short direction — bounded manual preference с собственным горизонтом, не полный MC Expected. Movement→time/target по-прежнему требует соответствия горизонта. Все common economics/CVaR/source gates сохранены.

104cells/model identities: UNIFIED_EDGE_COVERAGE_2026-10-13.json. Production contribution=null: нет активной сделки. Полная готовность восьми семей частично открыта по указанным данным.
