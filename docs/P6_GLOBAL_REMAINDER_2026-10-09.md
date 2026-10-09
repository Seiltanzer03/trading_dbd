# P6 и остатки глобального плана — 2026-10-09

Сверка существующего выпуска и сохранённых свидетельств. Это не новый выпуск,
не обучение и не объявление полного выполнения исходного ТЗ.

## Проверенная база

GitHub main `37a1fc29548d3c1e4f502f756087e1b19a9ae4aa` (PR #430) и локальный
HEAD `8a912d36877fe7d4b0cd105e5f48333787e37e50` имеют одинаковый tree
`d5046d7dd49292ab6298e31d826d12789a68aa6d`. Main проверен 2026-10-09.
P6 исправления `bf10065`, `00a52c5`, `e9e558d` входят в локальный HEAD.
«Не выпущен» в checkpoint-документах 2026-10-07 — историческое состояние,
не текущий backlog. Semantic scopes, canonical labels/CDF, nonoverlap
effective N, critical errors и READY_TO_FIT без фиктивного FITTED уже реализованы.

## P6: фактическая граница

Повторно прочитан сохранённый source publication run `37850534973`,
job `113562061546`, для main выше. Свидетельство датировано 2026-10-08,
не является новым live snapshot. Во всех 13 строках EDGE_SOURCE_READINESS
option: UNAVAILABLE, forecast_available=false, NO_ADMISSIBLE_SOURCE,
voting_weight=0. Инструменты: BTCUSD, ETHUSD, EURUSD, GER40, JPY100, NAS100,
SOLUSD, SP500, UK100, US30, USDCAD, XAG, XAU.
Необходимые входы: dated option chain, validated instrument/proxy mapping,
physical net-action calibration общих опционных признаков.

Это статус unified option-family adapter, не отсутствие наблюдений отдельного
native-expiry Q collector. Q не равна физической P или готовой net-action модели.
Малый Q audit slice последней приёмки не является полной обучающей выборкой.
Свежий полный calibration summary в этой сверке не получен. Старые числа
9775/3729/718 получены до canonical исправлений и не используются для fit.
Нулевые fit/model counts старого аудита тоже не объявляются свежими.
Пригодность fit на текущем полном архиве остаётся НЕ ПРОВЕРЕНА.

Сохранённый finite identity audit: JPY100 — NO_SUPPORTED_ADVANTAGE_YET.
BTCUSD/ETHUSD/SOLUSD имеют исторический Price/path результат, но Coinbase USD
archive не подтверждает mapping на configured Binance USDT или broker series.
Повторного поиска, подмены currency basis и активации через P6 нет.
Отсутствие новых family-моделей не отменяет исторический математический режим.

## Оставшаяся карта

| Пакет | Уже реализовано | Остаток прогнозного результата |
| --- | --- | --- |
| P1 | FOMC delivery, receipts, возраст/просрочка | Независимый scheduler и реальные 600s cadence receipts |
| P2 | Причинные clocks, admission, event/macro adapters | Prepublication same-period/unit consensus, исторические outcomes/costs и модель |
| P3 | Intermarket/session identity, calendar и readiness contracts | Синхронные независимые когорты, mappings и net economics |
| P4 | Sampled flow provenance и causal/cost readiness | Пригодная история разрешённой площадки и модель; proxy не CFD book |
| P5 | PIT admission и broker carry contract без двойных расходов | PIT positioning/value facts, broker quote facts и OOS calibration |
| P6 | Q/P separation, semantic readiness, finite identity verdicts | Текущий полный пригодный cohort, frozen physical model/OOS и chain/proxy mappings |

Отдельный эксплуатационный остаток: последний live AI smoke дал честный быстрый
503 authoritative_price_unavailable без публикации решения. Полный живой
macro-enriched разбор этим smoke не доказан; отсутствие цены не обходится.

## Следующий конечный шаг

Один bounded read-only полный calibration status через уже разрешённую
production-инфраструктуру: semantic scopes, raw/effective N, classes,
periods/expiry clusters, critical errors, fit/model artifacts. Только реально
пригодный scope допускает существующий finite fit/OOS path. Без входов —
конкретный EVIDENCE-GATED verdict, без нового search, ожидания прибыльности,
ослабления G.1 или автоматической торговли. G.2 остаётся вне P1–P6.

Прямой доступ из текущего окружения ранее не дал calibration body;
существующий post-research receipt его не содержит. Новые площадки/права
не создаются. Runtime этой сверкой не изменён: full CI, deploy и повторный
source/model search не запускаются. Цена этого решения: текущий полный fit
verdict пока отсутствует; он остаётся явным пробелом, не заменяется старым N.
