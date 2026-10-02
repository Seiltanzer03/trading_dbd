# Family model pipeline: письменный дизайн

Дата: 2026-10-02. Статус: подготовлен для проверки пользователем; реализация не начата.

## Цель и исходная точка

Завершить отсутствующий технический путь «реальные исторические данные → причинно допустимые признаки → наблюдаемые контрфактические результаты → проверенный artifact → существующий runtime consumer». После технической интеграции отдельно оценивать эффективность модулей; не подменять эту оценку успешным CI.

Нормативный источник: `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, SHA256 `4a49a73da6a9dd172c789631b5626c6ffaeb2afcb0e1e0a51075f655fb63b248`. Полный остаток сохранён в `docs/UNIFIED_EDGE_REMAINING_IMPLEMENTATION.md` ветки `docs/unified-edge-residual-audit`.

Исходная production main: `2bf3bfba598ced862dbcee05fcdcc93885a512e0`, PR #391. Выпущены runtime admission, общий selector, контроль издержек, read-only export, семейные adapters, реальные источники в ограниченном объёме и ACK telemetry. Producer моделей и воспроизводимая продольная сборка данных отсутствуют. Эти утверждения относятся к опубликованному GitHub checkpoint, а не к старой локальной рабочей копии.

Пользователь подтвердил продолжение подхода с существующим read-only export и off-host workflow. Этот документ фиксирует архитектуру для отдельного письменного согласования, а не объявляет разрешённой реализацию.

## Границы блока

Включены bounded archive, dataset builder, консервативный trainer, упаковка и интеграция artifact с существующим consumer, диагностический отчёт и проверки причинности. Существующие 13 инструментов, 12 действий, общий economics/risk selector и веса не изменяются.

Не включены новый warehouse, платные источники, новые брокерские подключения, обход ограничений источников, обучение внутри production API, автоматическая оптимизация весов, выставление ордеров и доказательство прибыльности. Полная разработка broker identity/units producer, остальных источников восьми семейств и расширенного сравнительного отчёта остаётся отдельными последующими блоками исходного плана. Этот блок не считается завершением всего ТЗ.

Выбранный подход повторно использует существующие export и workflows. Новый отдельный training service потребовал бы эксплуатации и доступа, а обучение в API нарушило бы ресурсные и причинные границы; оба варианта отклонены для этого блока.

## Компоненты и контракты

| Компонент | Вход | Выход и ответственность |
| --- | --- | --- |
| Archive assembler | Read-only export, сохранённые source records и их provenance | Версионированные immutable episodes и manifest; никаких дописанных задним числом фактов |
| Dataset builder | Episodes с T0 snapshot, действительными source receipts, retained price path и cost evidence | Причинные признаки отдельно от future labels; причины исключения каждой строки |
| Trainer / validator | Детерминированный dataset и фиксированная конфигурация | Проверка chronological purged splits; модель либо явный `UNAVAILABLE` |
| Artifact packager | Только валидированные модели и provenance | Существующий `edge-family-net-action-model-v1` внутри bounded runtime context |
| Workflow integrator | Утверждённые inputs, hash и deployment SHA | Off-host сборка, проверка, контролируемая публикация; production только читает |

### Архив

Использовать `scripts/export_unified_edge_reviews.py`, не расширяя доступ к production БД на запись. Сохранить ограничения: до 32 reviews за export, snapshot до 2 MB, path до 6000 points, decoded export до 96 MB и транспортный лимит 32 MB. ACK остаются отдельно от исходного snapshot.

Новый archive имеет отдельные ограничения: максимум 512 complete episodes и 96 MB decoded payload на bundle. При переполнении удаляется целый самый старый episode; manifest записывает удаление и исключённые диапазоны. Незавершённые пути не дополняются синтетическими точками. Trainer явно сообщает, что ограниченное окно не является полной историей.

Manifest хранит hashes, schema versions, exporter/config/code SHA, instrument identity, T0, publication/receipt clocks, revision identity, horizon, геометрию действия и cost provenance. Необходимо сохранять минимальные реальные source records, достаточные для воспроизведения признаков; hash ссылки на уже истёкший workflow artifact недостаточен. Непубличные snapshots, account identifiers и raw datasets не коммитятся в Git.

Повторный вход с тем же identity/hash дедуплицируется. Разные hashes одного identity не заменяют друг друга молча: конфликт исключается и отражается в отчёте. Архив не превращает revised/latest series в исторический consensus vintage.

### Dataset и наблюдаемые labels

Features должны существовать на T0: publication и receipt clocks не позже T0, точное соответствие инструменту, horizon, единицам и допустимому mapping. Future path, поздний ACK, future revision и validation outcomes никогда не попадают в features. Из имеющихся данных извлекаются только действительно поддерживаемые семейные признаки; отсутствующие поля не заменяются нулём.

Labels строятся на retained observed price path через существующую replay-логику: одинаковый path и horizon для конкретного candidate и HOLD, net action delta versus HOLD в R. Model-generated scenario estimates не являются training labels. Для обоих действий нужны полное покрытие horizon либо корректная terminal resolution, воспроизводимая geometry и подтверждённый causal cost context; truncated/unresolved path и unknown costs исключаются.

Такие labels — path-based counterfactuals с моделью исполнения, а не реальные broker fills или settled P&L. Даже подтверждённый cost input не делает контрфактическое исполнение фактическим. Вид данных и предположения исполнения сохраняются в каждой строке и отчёте.

Нельзя объединять разные stop/take/trailing/partial geometries под одним action name без conditioning. Первый producer выпускает прогноз только для geometry cohort, которую существующий consumer может однозначно идентифицировать и проверить. Если текущий контракт не может проверить cohort, соответствующее non-HOLD action остаётся `UNAVAILABLE`; необходимость расширения causal context contract выносится в отдельное согласование. Не создавать универсальную модель для всех расширенных действий из несовместимых labels.

### Обучение и validation

Первый trainer — детерминированная регуляризованная линейная модель с фиксированными настройками и простым baseline. Подбор гиперпараметров по финальному holdout запрещён. Существующие contract fields и feature keys используются без переименования; модели отдельных семейств не получают скрытые общие признаки без объявленного контракта.

Split chronological; все reviews одной сделки относятся к одной группе. Пересекающиеся label horizons purged, train/validation отделены полным forecast horizon. Scaling и обработка признаков обучаются только на train. Repeated reviews не считаются независимыми сделками. Отчёт показывает число строк, distinct trades и effective groups отдельно.

Сохранить требования consumer: `OOS_VALIDATED`, `point_in_time=true`, `OBSERVED_NET_ACTION_DELTA_VS_HOLD`, `purged_split=true`, минимум 20 validation observations и 2 folds, положительный proper-score gain и включённые costs. Дополнительно каждый validation fold должен включать минимум 10 distinct trade groups. Метрика gain — MSE baseline минус MSE модели на одинаковом OOS cohort; scoring rule и baseline фиксируются до validation. Эти пороги — техническая admission floor, не доказательство достаточной статистической мощности или доходности.

Соблюдать существующее `train_end + horizon <= validation_start < validation_end <= trained_at <= T0`, точное совпадение horizon и установленный consumer срок годности. Артефакт, обученный сегодня, не допускается в исторический snapshot вчера. Историческая проверка модели требует отдельного walk-forward artifact для каждого допустимого cutoff.

При четырёх distinct trades текущая реальная выборка не позволяет объявить полноценный результат validation. Trainer должен вернуть `UNAVAILABLE` с counts и причинами, а не ослабить admission. Synthetic fixtures разрешены только для CI, явно маркируются и никогда не публикуются в runtime.

### Публикация и издержки

Artifact включает dataset/config/code hashes, lineage реально использованных features, train/validation clocks, counts, scoring details и diagnostic exclusions. Неуспешная validation не создаёт пригодную runtime модель. Валидировать размер до записи: существующий runtime context не более 48 KB, pinned SHA256 и точный deployment SHA, ограниченный causal age. Размер нельзя обходить внешними неаудируемыми ссылками.

Workflow не меняет production weights и risk policy. Runtime loader остаётся единственной точкой admission, все hard risk gates сохраняются. Невалидный, будущий, устаревший, слишком большой или несоответствующий deployment artifact не участвует в выборе.

Реальные broker/account identity, position units и execution costs — независимая предпосылка. Public venue price/book не доказывает identity исполняющего брокера. Cost file не может сам назначить snapshot identity, чтобы затем проверить себя. Пока достоверного read-only producer этих полей нет, affected net labels и artifacts остаются `UNAVAILABLE` с явной причиной. Неизвестные комиссии, slippage, manual latency и rollover не равны нулю.

## Параллельная работа и интеграция

После согласования этого spec и письменного implementation plan: отдельные agents/worktrees для archive+schema, dataset labels и trainer+validation. Packager/runtime integration начинается после фиксации общих contracts. Один интегратор отвечает за порядок объединения; независимый review проверяет соответствие spec и качество кода. Agents не правят одни shared files одновременно.

Путь разработки сохраняется в roadmap: задача, входной SHA, ветка, владельцы, RED/GREEN evidence, review findings, blockers и итоговый SHA. Следующие блоки исходного плана не теряются и не помечаются выполненными этим pipeline.

## Критерии приёмки

1. Одинаковые реальные inputs и configuration дают одинаковый dataset/artifact hash; exclusions воспроизводимы.
2. Тесты ловят future receipts/revisions, wrong instrument/horizon/geometry, missing costs, truncated path, conflicting archive records, oversized payload и train/validation leakage.
3. Grouped chronological validation и proper-score calculation проверены независимыми fixtures; недостаточная реальная выборка честно даёт `UNAVAILABLE`.
4. Producer output проходит существующий consumer; negative artifacts исключаются. Нет новых provider calls или обучения в API, нет изменения обязательного риска, весов или торговых команд.
5. Реальный bounded export проходит полный технический путь до admission либо документированной причины отказа. Отказ из-за отсутствующих inputs не объявляется активной интеграцией модели; это отдельно видимый blocker.
6. До merge — полный mandatory green CI, spec review и code review; после merge — exact-SHA deploy/readiness/functional smoke в рамках ранее выданного разрешения. Реальные тестовые ордера не выставляются.

## Остаток после этого блока

Production broker identity/units и достоверные costs; полный PIT набор источников и features восьми семейств; поддержка всех требуемых action geometries; расширенные сравнения и replayed ablations, distribution/contradictions/rapid cancellation/grouping. Для portfolio drawdown нужны реальные согласованные portfolio returns: overlapping review deltas нельзя выдавать за эту величину. Эффективность и веса исследуются после технической интеграции и накопления достаточных независимых данных.

## Self-review

Проверены scope, причинные часы, независимость costs, совместимость consumer, action geometry, ограничение ресурсов и отделение технической приёмки от эффективности. Нет заглушек или обещания обученных моделей при отсутствии данных. Следующий шаг после письменного согласования пользователем — `superpowers:writing-plans`; этот документ не заменяет implementation plan.
