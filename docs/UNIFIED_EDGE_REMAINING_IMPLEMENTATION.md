# Сверка остатка исходного ТЗ после PR #391

Источник: `TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, прежде всего §7 и §9.
Это контрольная точка разработки, не заявление завершения всего ТЗ и не
оценка прибыльности. Независимый read-only review и поиск по production-коду
подтвердили различие между отсутствующими входами и недостающей реализацией.

## Выпущенный код

PR #391 объединён в main `2bf3bfba598ced862dbcee05fcdcc93885a512e0`.
Reviewed tree: `3e1587bfd6db90a284fc320ddc97995c451f8217`.
Все пять обязательных PR workflow прошли; main CI 36996377153 также прошёл:
1969 passed, 4 штатных skipped, frontend и real WebKit/iPhone — success.
Локальный Python 3.12: 1972 passed, известный baseline SQLite/WAL failure
`test_quiescent_sparse_clone_replays_wal_only_into_backup`; он не скрыт.

Интегрированы расширяемый frozen registry, явные бюджеты 100%, причинное
source binding, динамический audit/UI/ablations, bounded source collection,
exact configured crypto archive/export, pinned runtime context, fail-closed
repricing/publication и фактическая ACK-телеметрия. Регистрация эксперта не
равна наличию обученной модели; импортный контракт не равен работающему
подключению executing account.

Deploy run 36996647296 завершился success: delivery, safe research pause,
exact-SHA readiness, functional smoke и public HTTP прошли. Все семь
CI/production contexts — success. Isolated smoke проверил 12 действий
(7 extended), orders=0, paid calls=0, real position mutations=0.
Post-research 36998178346 также прошёл. Source run 36998175454 опубликовал
184235 bytes для этого SHA: 13 instruments, 16 requests, errors=0, models=0.
Math run 36998173081 опубликовал 251817 bytes для того же SHA и завершился success.
Это не утверждение доступности всех forecasts, реального broker fill или profit.
Итоговые evidence сохраняются в [PR #391](https://github.com/Seiltanzer03/trading_dbd/pull/391).

## Остаток именно в реализации

| Пункт | Подтверждённый пробел | Критерий закрытия |
| --- | --- | --- |
| §7: producer семейных моделей | `edge_family_adapters.py` принимает `edge-family-net-action-model-v1`, но producer этого контракта отсутствует | PIT dataset → action labels по фактически наблюдённым путям → purged обучение/validation → воспроизводимый artifact → существующий runtime admission |
| §7: longitudinal family dataset | Есть latest bundle и 14-дневные run artifacts, отдельные macro archives; они не собраны в единый воспроизводимый источник семейных training records | История receipts/features с hashes, exact identity и причинным соединением с исходами; без восстановления прошлого из сегодняшнего файла |
| §7/§9: executing-account adapter | `execution_cost_context.py` требует `trade_identity` и `position_execution_units`; production snapshot producer этих полей отсутствует | Независимая привязка broker/account/trade и currency/quantity/risk units; валидный cost file сам по себе не должен создавать ложную identity |
| §9: полный comparison report | Есть Expected/CVaR, частота вмешательства, replay deltas и ACK statistics; нет полного requested report | Action distribution с HOLD; корректные drawdowns; изменения решений/быстрые отмены; instrument/regime/horizon/family groups; aggregate legacy control и replay ablation cohorts |
| §7: family-specific coverage | Реализованы не все перечисленные источники и признаки | Earnings expectations; event novelty/reaction dynamics; lag/relative value/breadth; OI/fund flows; valuations/forwards; применимые сессии — только при настоящем источнике и проверенной instrument mapping |

Таблица не означает, что любой новый голос допустим после написания producer.
Существующие PIT/OOS, source freshness, horizon и risk gates обязательны.
В частности, adapter требует OOS_VALIDATED, не менее 20 samples, не менее
двух folds, purged split, положительный proper-score gain и costs included.
Небольшая текущая когорта не превращается в такой результат автоматически.

## Нерешённые настоящие входы

- Executing broker/account: реальные fees, swaps, slippage, execution clocks
  и единицы позиции; текущая ACK receipt не является broker fill clock.
- Законно доступный pre-release consensus, revisions и исторические vintages.
- Брокерный CFD стакан и проверенные exchange/CFD, cash-index/CFD mappings.
- Достаточная exact Binance/USDT история: запись/экспорт уже работают, но
  наблюдения должны накопиться. Denied HTTP requests не обходятся.
- Physical/VRP/dealer-position evidence: Q/IV/OI этого не доказывают.
- Достаточная причинная выборка: четыре distinct historical trades не являются
  независимой доказательной базой для обучения и оптимальных весов.

## Следующий архитектурный этап

Рекомендуемая граница: расширить существующий read-only export и off-host
workflow, не обучать модели на production host. Сохранять реальные frozen
features отдельно от позднейших outcomes; производить artifact только после
действующих admission checks, иначе публиковать явный UNAVAILABLE.
Comparison reporting можно развивать независимо от недоступных paid sources.

Альтернативы: внешний longitudinal warehouse и broker-source интеграция
сначала (нужны выбор провайдера/доступ и отдельная архитектура), либо оставить
только external model import (не закрывает producer из §7).

По `superpowers:brainstorming` новый training/broker subsystem требует
согласованного дизайна, затем written spec/implementation plan. Этот документ
фиксирует обнаруженные пробелы и предложение, а не подменяет такое согласование.
Ранее завершённый runtime/review/CI не перезапускаем как незавершённую задачу.
Проверки эффективности и оптимизация весов остаются отдельным последующим этапом.
