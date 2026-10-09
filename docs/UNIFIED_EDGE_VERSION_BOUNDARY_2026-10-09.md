# Граница текущей версии unified edge

Основание: исходное ТЗ `TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md` и
утверждённый эффективный план `superpowers/plans/2026-10-09-effective-unified-edge.md`.
Цель версии — работающий ручной терминал с единственным решением, честной
экономикой, математическим контекстом и применимыми действиями. Полнота
технического пути не означает, что все восемь семейств уже имеют обученные
прогнозные модели или что доказана прибыльность.

## Шесть блоков исходного плана

| Блок | Технический результат | Конкретный остаток |
| --- | --- | --- |
| Математический edge | Конечный поиск по13 инструментам, исторические Price/path heads и импорт результатов; LIMITED_HISTORICAL с половинным вкладом и STABLE_HISTORICAL отдельно | Не каждый инструмент имеет применимый head. JPY и crypto identity/архивные ограничения сохраняются; новый поиск только при новых пригодных входах |
| Восемь видов edge | Макро, события, поток, межрыночное, позиционирование, value/carry, опционы, сессии: adapters, причинные features/dataset, producer/trainer/import и source-bound admission подключены | В последнем finite private archive40 episodes/32 reviews новых допущенных family-моделей0. Это отдельный счётчик от действующей математики; неполные position/cost labels не заменяются ослаблением статистики |
| Веса экспертов | Единый бюджет100%, фиксированные схемы, availability/freshness/dependence и фактические вклады; ablations | Оптимальность не доказана: текущая matched comparison содержит4 независимые парные сделки. Не переоптимизировать этот неизменившийся материал |
| Общее решение | Один выбор с HOLD, общей сценарной экономикой, издержками, риск-гейтами и независимым LLM; manual confirmation | Исполнимая авторитетная цена, реальные расходы и executing account могут блокировать конкретное решение. Proxy/fallback не выдаётся за broker price/fill |
| Расширенный менеджмент | Все12 действий, конкретные параметры/identity, частичное закрытие, безубыток, stop/gamma trailing, take-profit, momentum/time exits | Применимость зависит от позиции/геометрии/цены. Isolated smoke проверяет функциональность, не результат настоящей сделки; реальные тестовые orders0 |
| Интерфейс и сравнение | Вклады/withheld weights, причины исключения, одно решение, ablations, сравнение HOLD/control/balanced/llm20 и реальные параметры действий | Историческая когорта ограничена; portfolio profit/drawdown нельзя получить без settled ledger. ΔHOLD0 на известной matched cohort не означает нулевой edge всех моделей |

## Остаток по источникам — следующий вход, а не повторная задача

| Семейство / эксплуатация | Чего конкретно не хватает |
| --- | --- |
| Макро/события | Lawful prepublication consensus и revision/first-receipt evidence для surprise; текущие официальные факты и FOMC stated ranges уже используются |
| Поток/межрыночное/позиционирование | Проверенные venue/CFD mappings и последовательные независимые observations; source facts сами по себе не новый голос |
| Value/carry и labels всех семейных моделей | Независимая frozen position/geometry и полный pinned executing-broker/account cost contract с units/clocks, пригодные net labels |
| Опционы | Реальная физическая/VRP/dealer evidence и зрелые expiry outcomes; Q context не объявляется physical forecast |
| Сессии | Проверенная cash-index/CFD calendar identity и пригодная причинная net-action выборка |
| P1 | Независимый off-host scheduler и реальные600s cadence receipts; GitHub transport работает, непрерывность cron не доказана |
| Хранение | Свежий проверенный полный off-host backup/RPO и capacity для растущей liveDB. Yandex dated snapshot проверен; live история не удаляется. Режим low-disk остаётся явно degraded |

Ни один из этих отсутствующих входов не считается реализованным вследствие
green CI. Не создавать новый источник/сервер/права и не повторять обучение
на той же непригодной истории. Следующая продуктивная разработка семейных
прогнозов начинается с пригодной независимой position/cost evidence, а не
с ещё одного refit.

## Проверяемая линия завершения и текущая квитанция

Последняя полностью принятая база — PR435/main14b2fe9d,
actual deploy37982521624: delivery/readiness/smoke/public/orchestration и все
семь status contexts success. Полный свежий backup не заявлен: действует
ранее разрешённый low-disk/skip-local-restore режим.

PR436/main8eb522ed исправил измерение FOMC из уже сохранённых точных текстов.
CI green и сервер установлен, но actual deploy37999591631 остановился на
таймауте `/api/research/passive/status`; public/orchestration skipped.
Это не принятое завершение. Новый пакет устраняет только этот воспроизведённый
блокер: exact last-good materialization off HTTP, timestamp/error/staleness,
UNKNOWN при устаревании, без фиктивных нулей и изменения risk/source gates.

Версия закрывается после единственного final scoped review, обязательного
exact-tree CI, merge и фактического automatic deploy/readiness/smoke/public,
сверки exact-SHA необходимых публикаций. Финальная квитанция с SHA и run IDs
фиксируется в PR metadata после выполнения; запись pending не является успехом.
После этой линии не открывать заново закрытые A/C/D и не запускать новый
search без новых входов. Дополнительные семейные модели и доказательство
эффективности относятся к следующей версии с отдельным конечным объёмом.
