# Единый edge: путь разработки и критерии завершения

Источник требований: приложенный `01-trading_dbd_unified_edge_plan_2026-10-01.md`,
разделы 1–12. Этот документ сохраняет полный объём исходного плана и не заменяет
его более узким планом предыдущего выпуска `EDGE_COMPLETION_PLAN.md`.

Последняя полностью подтверждённая production-база: PR #392, main
`f0c86e6b9ef8c40727892cbb4d961166bc668d2f` (2026-10-04).
PR #393 установлен как `1b1838744d6014b072365c4988f8070654af34dc`,
но его functional smoke не прошёл; полный production acceptance ещё не подтверждён.
Ветка runtime-интеграции `feat/unified-edge-full-integration` объединена.
Текущая контрольная точка остатка: `docs/unified-edge-residual-audit`.

## Порядок и граница текущей работы

По указанию пользователя сначала завершаем техническую интеграцию в рабочий
путь. После выпуска отдельно оцениваем эффективность модулей. Проверки
причинности, правильности расчётов, риск-ограничений, реального происхождения
данных и единственного решения обязательны до выпуска. Они не являются
доказательством прибыльности или оптимальности весов.

| ID / исходный этап | Что уже работает | Остаток / проверяемый результат |
| --- | --- | --- |
| E1 / 1 | Конфигурация 13 инструментов, 12 действий, конкретные параметры, HOLD и причины исключений | Сохранять матрицу конфигурации и provenance при новых источниках; не скрывать отсутствующие поля |
| E2 / 2 | Общая сценарная экономика, один ручной выбор, обязательные риск-фильтры | Закрыть возврат к старой экономике после отклонённого rollover-контракта; отклонённые издержки не публикуют новое решение |
| E3 / 3 | Текущий LLM скрывает quantitative winner, сохраняет 12 оценок и request/response hashes; ненулевое влияние проверено на provider-route | Сохранить независимость при расширении экспертов и bounded snapshot; настоящее пользовательское live-наблюдение отдельно, без открытия тестовой реальной сделки |
| E4 / 4 | Конечный поиск и опубликованные результаты всех 13 инструментов; крипто USD proxy имеет нулевую authority | Записывать и экспортировать точные уже полученные BinanceUSDT бары с причинным provenance; старые неверно помеченные записи не переименовывать; до достаточного точного архива криптомодели остаются diagnostic |
| E5 / 5 | Восемь адаптеров, фактические source facts, freshness/lineage/model admission, bounded production source bundle | Полный путь импорта применимых source-bound action forecasts; реальные book observations в одном сборе; каждый недоступный источник/модель имеет отдельный явный статус |
| E6 / 6 | Рабочий regime classifier, старение, инструментная применимость, уменьшение веса повторного происхождения | Исправить authority точного настроенного Binance контекста; расширяемые зарегистрированные эксперты с единым явным бюджетом 100%; новые эксперты входят в UI и ablations |
| E7 / 7 | Замороженные ablations, сравнение схем, actual path replay и UI; фактическая общая историческая когорта четыре сделки | Принимать проверенные полные broker costs без выдуманных комиссий/свопа; сохранять реальную ACK/cancel latency отдельно от модельной задержки; dynamic expert audit |
| E8 / 8 | Предыдущий exact-SHA CI, merge, deploy, readiness и isolated 12-action smoke прошли | Проверить итоговый новый tree: профильные проверки → полный green CI → PR/merge → exact-SHA production readiness/smoke и публикация источников/моделей |

## Реальные данные и незакрытые зависимости

| Семейство | Доступная роль / материал | Что нельзя объявлять готовым без данных |
| --- | --- | --- |
| Макро | Проверенные макро-факты и причинный контекст | Полный pre-release consensus, revision history и обученный net-action forecast |
| События | Фактическое календарное/событийное описание | Причинный surprise без сохранённого до публикации consensus и calibrated action model |
| Поток | Реальные разрешённые L1/tape observations; proxy venue явно указан | Брокерный CFD стакан; OFI без двух фактических последовательных наблюдений; freshness после срока 60 секунд |
| Межрыночное | Синхронные completed crypto proxy returns и их общий lineage | Независимый голос без source-bound forecast; доказанное соответствие CFD/ETF/futures |
| Позиционирование | Реальные CFTC reports с честным первым получением | Выдуманное время публикации и валидированное соответствие futures→CFD |
| Value/carry | Контракт фактических broker costs и rollover | Своп/комиссия конкретного executing account, если нет подтверждённого источника и единиц |
| Опционное | Действующее Q-distribution context и математическая механика | Физическая вероятность/VRP/dеaler positioning, выведенные только из IV/OI |
| Сессионное | Официальный NYSE календарь/DST/праздники с точной identity | Cash-index→CFD mapping и обученный net-action forecast без фактической причинной выборки |

HTTP denial не обходится альтернативным хостом, IP или переносом запрещённого
запроса на runner. Уже накопленные легитимные точные источники можно использовать
при подтверждённой identity и времени. Coinbase USD не превращается в Binance
USDT или в исполняемый брокерный CFD.

## Контрольные точки

1. Исправление отказа экономики и документирование полного остатка.
2. Интеграция реестра экспертов, точного архива и source/forecast ingestion.
3. Сквозные проверки публикации, snapshot budget, UI, risk gates и исполнения.
4. Конкретный финальный SHA, green CI, PR и production-подтверждение.
5. Отдельный этап эффективности: untouched chronological validation,
   наблюдения реальных решений, сравнение контролей и неопределённость результатов.

После каждой интеграции ниже дописывается фактический результат, тесты и
оставшийся блокер. Не переносим незавершённый источник в «готово» ради выпуска.

### Журнал текущей интеграции

- 2026-10-02: начата работа по исходному плану. Подтверждён дефект:
  `BROKER_ROLLOVER_*` не запрещал ранжирование по исходной экономике.
  Исправление блокирует все новые кандидаты и публикацию при таком отказе.
- Параллельно: реестр экспертов; exact-source crypto archive; source collector;
  broker cost contract и фактическая телеметрия подтверждений; импорт прогнозов.
- Интегрированы: дополнительные зарегистрированные эксперты с единым явным
  бюджетом, причинным receipt и привязкой lineage к фактам снимка; все новые
  компоненты сохраняются в audit/UI/ablations. Регистрация предпочтения не
  заменяет OOS/PIT admission прогнозной модели семейства.
- Рабочий API принимает локальные pinned SHA-контексты экспертов и family
  models, а полные проверенные broker costs требуют общего перерасчёта. Если
  перерасчёт невозможен, старые economics не публикуются как новое решение.
- Существующий intraday polling сохраняет точные completed configured crypto
  bars; off-host export/cache не переименовывает старые Yahoo labels и не
  обращается к denied Binance API. Объём точного нового архива ещё должен
  накопиться; диагностические USD-модели не получают authority.
- ACK/arm/cancel/decline и strategy take подтверждения записываются отдельно
  от fills; comparison экспортирует фактическое время получения и задержку,
  без выдуманных fee/slippage/broker-fill timestamps.
- Независимый review нашёл и закрывает streaming body budget, future archive
  receipt и реестр без фактического lineage. Прогноз семейства допускается
  только при совпадении с замороженным горизонтом сравнения.
- Полный broker cost feed, licensed PIT consensus и обученные family net-action
  модели всё ещё требуют настоящих входных данных. Импортный контракт готов;
  эти данные не созданы и не объявляются действующими прогнозами.

### Release gate, 2026-10-02

- Независимый read-only review закрыл два дополнительных Important findings:
  source facts должны быть получены до формирования оценки эксперта;
  HTTP redirects отклоняются до неучтённого чтения/повторного запроса.
  Оба дефекта воспроизведены RED, исправлены GREEN и перепроверены reviewer.
- Некорректный глубоко вложенный pinned broker JSON отклоняется без API crash;
  regression RED→GREEN и независимая проверка выполнены.
- Финальный локальный полный прогон: 1972 passed, один известный baseline
  failure `test_quiescent_sparse_clone_replays_wal_only_into_backup` в Python
  3.12/SQLite. Не объявляется green; тест и защитный код не отключены.
- Frontend syntax/smoke прошли; локальный real WebKit не запустился без
  Playwright. Штатный обязательный CI использует Python 3.11 и устанавливает
  Playwright/WebKit. Merge разрешён только после его полного green на final SHA.
- PR #391 выпущен: green обязательный PR/main CI, exact-SHA deploy/readiness/
  smoke/public HTTP и публикация source/math отчётов подтверждены.
  Все семь CI/production contexts — success. Подробности в следующей сверке.

Дополнительная сверка полного §7/§9 выявила остатки именно в коде, а не только
в данных: family artifact producer/PIT dataset, producer broker identity/units
и расширение comparison report. См. `UNIFIED_EDGE_REMAINING_IMPLEMENTATION.md`.
Этот остаток не закрывается наличием импортных адаптеров или green CI.

### Продолжение producer-интеграции, 2026-10-04

Ветка `feat/unified-edge-remaining`, исходный production SHA по-прежнему
`2bf3bfba598ced862dbcee05fcdcc93885a512e0`. Старые worktrees и результаты сохранены;
план не пересоздавался. Новый выпуск ещё не объявлен production-готовым.

| Задача плана 2026-10-02 | Технический результат | Граница готовности |
| --- | --- | --- |
| 1: archive | Bounded immutable episodes, совместимое продолжение реальных путей, конфликтующие identity исключаются обе | Накопление на доверенном off-host хранилище; 14-day artifacts не постоянный архив |
| 2: trainer | Fixed ridge, whole-trade purge, один fit перед обоими untouched holdouts, истинные counts/clocks, fail-closed costs/provenance | Минимум 40 независимых допустимых trade groups; exact geometry cohorts могут оставаться слишком узкими |
| 3: dataset | Причинные frozen features и net path-counterfactual labels; полная независимая position/cost evidence; original synthetic/context/horizon declarations проверяются до нормализации | Старые записи не обогащаются задним числом; TIME_STOP training binding реализован отдельной технической веткой, выпуск ещё не подтверждён |
| 4: packaging/runtime | Filesystem-only pipeline, pinned SHA/hash context, geometry admission, явная unavailable диагностика, private-gated workflow | Автоматическая активация модели не включена; public repo не экспортирует private account data |
| 5: comparison | Общая парная cohort, legacy/ablations, HOLD distribution, groups/transitions/ACK/cost breakdown | Portfolio drawdown/profit unavailable без settled portfolio ledger; replay не broker fill |
| 6: position producer | Независимый bounded pinned read-only импорт до cost ingestion, identity/units/fraction/clock guards | Настоящий executing broker/account feed пока не предоставлен и не настроен |
| 7: family coverage | Восемь существующих source adapters и реестр prerequisites; оригинальные источники/время/mapping не подменены | Earnings/consensus/text novelty/reaction, lag/breadth, OI/fund-flow history, valuation/forwards, CFD calendars и physical/VRP требуют настоящих входов и части дальнейших feature producers |
| 8: выпуск | Проверки и независимый final review выполняются на итоговой интеграции | Mandatory exact-SHA CI, PR/merge/deploy/readiness/smoke ещё должны пройти |

Свежие проверки: 243 baseline-profile passed; trainer review finding о global
macro/event согласован с действующим adapter и закрыт RED→GREEN, 148 profile
passed; 42 dataset review regressions RED→GREEN, 131 dataset passed; frontend
42 syntax/smoke commands passed. Первый полный интеграционный прогон:
2421 passed, 4 skipped, один известный Python3.12 SQLite/WAL failure
`test_quiescent_sparse_clone_replays_wal_only_into_backup`, воспроизведённый также
на старой baseline worktree. Storage guard и тест не изменены/не отключены.
После дополнительной правки runtime applicability требуется новый final прогон.

На сохранённом read-only real export pipeline принял 32 archive episodes из
14 distinct trades; dataset вернул 0 admitted rows и 0 models: 25 snapshots без
полной frozen geometry, 4 без independent position evidence, 2 с invalid
execution range/direction, 1 с несогласованным price/R. Runtime artifact не
создан. Эти результаты не означают оценку эффективности или прибыльности.

Переносимая affine-R геометрия реализована локально отдельной веткой 2026-10-04;
её review/CI/release ещё должны завершиться. Остаются learned geometry
conditioning/support ranges и причинные исторические feature producers при
наличии исходных observations. Остаются входы: реальные
позиция/расходы брокера, lawful PIT consensus и проверенные source mappings,
достаточная выборка и доверенная закрытая off-host среда. Эти пункты нельзя
считать завершёнными вследствие green CI или наличия import boundary.

Дополнительный runtime applicability finding закрыт: explicit context-only и
несовпадающий/некорректный horizon оригинального source, supporting consensus и
официальных macro roots/containers сохраняются в provenance и запрещают голос
соответствующих linear/conditional forecasts. Контекст остаётся видимым; source
без таких деклараций сохраняет прежний контракт. 88 regression cases RED→GREEN,
197 profile tests passed. Ни один признанный Important finding не оставлен без
исправления перед final CI.

Финальный полный локальный прогон после applicability fix: 2525 passed,
4 skipped, тот же baseline SQLite/WAL failure; других ошибок нет. Final tree
готовится к draft PR; официальный Python3.11/real WebKit CI остаётся обязательным
перед merge. Никакая production-готовность по локальному прогону не заявлена.


2026-10-04: отдельная техническая ветка закрывает TIME_STOP family-model binding.
Original concrete candidate и observed-path counterfactual label сохраняются;
relative key учитывает точный offset и присутствие явно заданного timeout.
Дробный timeout нормализуется по точной forward producer formula, geometry
использует тот же проверенный TIME_STOP descriptor. Trainer перепроверяет
retained binding, runtime после exact geometry/horizon admission переводит его
в текущий concrete candidate_id. Legacy artifacts, risk/cost checks и OOS floors
сохраняются. Это реализация контракта, а не обученная модель или результат
эффективности; отсутствие фактических входов остаётся блокером.


2026-10-04: PR #392 завершил официальный CI (2526 passed, 4 skipped),
main CI/staging/deploy, exact readiness, functional/public smoke и exact-SHA
публикации источников/математического отчёта. TIME_STOP PR #393 прошёл независимый
review без Important/Critical findings, 437 профильных тестов и официальный
Python3.11 CI (2561 passed, 4 skipped) с real WebKit. Локальный Python3.12:
2560 passed, 4 skipped, прежний SQLite/WAL failure. Его delivery/readiness прошли;
AI functional smoke вернул 422 INVALID_POLICY_INPUTS за 13.842 s при лимите 12 s.
Проверка isolated 12-action fixture прошла без orders, paid calls и изменений
реальной позиции. Это не полностью принятый выпуск.

Узкое исправление PR #394 проверяет явно объявленную недоступность авторитетной
цены до изменения позиции, тяжёлого enrichment/provider work и публикации.
Ответ — отдельный retriable 503 authoritative_price_unavailable. Smoke проверяет
его точный отрицательный контракт отдельно от успешного verdict; произвольные
422/503 и ответы с опубликованным решением остаются отказами, лимит 12 s и
положительная isolated 12-action проверка сохраняются. 34 регрессионных случая
проверяются RED→GREEN через CI отдельной ветки, поскольку локальная среда
исполнения недоступна. Final acceptance фиксируется в PR по фактическим checks.
Raw live quote availability и frozen source authority имеют разные значения;
кэш не перестраивается и authority не повышается через сравнение этих флагов.
Остатки переносимой геометрии, causal historical features и реальных входов
сохраняются; эти изменения не доказывают эффективность или прибыльность.


2026-10-04: статус PR #394 выше уточнён фактическим acceptance. Main
`29bbb28a692d2ef37cae4129b6c632735a52c579` прошёл main CI `37202760946`
(2595 passed, 4 skipped), deploy `37202917087` с readiness/functional/public
проверками; source `37203959101` и math `37203957738` опубликованы на том же
pinned SHA. Read-only inventory `37203996160`: 65/96 DATA_READY, 27 с
недостаточной independent evidence. Coverage не является доказательством
эффективности или прибыльности.

Отдельная текущая ветка реализует `edge-family-affine-r-geometry-v1` для
CLOSE_10/25/50, EXIT и relative TIME_STOP. Original concrete candidates,
observed-path net labels, cost/position proof, purge/holdouts/floors и runtime
risk admission сохраняются. Descriptor equality снимает только абсолютную
price partitioning: разные R-state/exposure/horizon/instrument/direction,
barrier/candidate topology и nonprice parameter presence остаются отдельными.
Extended price actions и legacy artifacts сохраняют exact geometry. Trainer
перепроверяет bounded frozen proof, original candidate membership и independent
position consistency; artifact/runtime/packaging отклоняют unknown/partial
extensions и unsupported portable actions. HOLD остаётся replay control.
Новые artificial contract fixtures не являются broker outcomes.

Локально focused portable/dataset/training/adapters/TIME_STOP/pipeline:
495 passed после initial 30 failed/1 passed и дополнительных test-first
hardening/namespace/canonical regressions. Два независимых read-only review
(task и весь feature diff) прошли без Critical/Important findings. Полный
локальный Python3.12: 2652 passed, 4 skipped, прежний baseline SQLite/WAL
failure; storage guard и его тест не изменены. Frontend syntax/smoke passed.
Mandatory official Python3.11/real WebKit CI и новый release ещё требуются. Реальные позиции/расходы брокера, lawful PIT consensus, проверенные
source mappings, достаточная выборка и закрытая off-host среда по-прежнему
требуются. V1 не обучает geometry conditioning и не extrapolates geometry.

### Portable geometry final acceptance, 2026-10-04

PR #395 supersedes the earlier pending-release note. Main
`9733ca81e8f81925c85274566a87c07b8de1ec6f`, tree
`3fedfe84e12c03a48290a522d2175bb8bea13cf3`, passed mandatory official
Python3.11 CI (2653 passed, 4 skipped), real WebKit, main CI `37219195480`,
staging `37219379444` and deploy `37219379434`. Readiness, isolated 12-action,
functional/public checks passed with zero orders/paid calls/real-position mutations.
Actual AI unavailable-price response was authoritative retriable503, 14ms<12s,
with no decision published. Source `37220393387` and math `37220391535`
published on that exact pinned SHA. Coverage inventory `37220444946` reported
65/96 DATA_READY, 27 insufficient independent evidence, 2 G1M_ONLY and
2 QUALITY_ONLY; family models remain zero. Coverage is not efficacy.

### Causal family history implementation, 2026-10-04

- Bounded pure historical producers now calculate signed positioning delta and
  report age, actual Coinbase USD five-minute returns lagged one minute, fixed
  ordered relative-return differences and related crypto peer breadth.
- Collector freezes received response hashes and compact proof using existing
  GETs; original COT histories, linked returns and proxy declarations remain.
  CFTC→CFD mappings stay unvalidated and cannot bypass admission.
- Adapter recomputes before admitting extended sources; runtime checks proofs
  against immutable bundle capture and the unchanged 8,000-byte selected-fact
  cap. Constituent applicability remains binding in derived and legacy features.
  Frozen dataset replay never reads future path observations into features.
- Deterministic RED evidence and focused GREEN verification are preserved in
  `.superpowers/sdd/2026-10-04-causal-family-history/task-1-report.md`. Fresh review,
  broad exact-tree checks and mandatory exact-SHA official CI/release remain
  integrator gates; this entry does not claim deployment or predictive efficacy.
- Still open: general event novelty/reaction, valuation/forwards, index
  constituent breadth, empirical learned lead-lag conditioning, real source-bound
  family calibration and external genuine flow/OI series. No profitability claim.

Causal-history task review found mapped target breadth and context-only position
admission defects; both fixed and scoped re-review approved. Fresh whole-feature
review found trainer nested-proof admission gap; shared bounded receipt/identity/
hash/applicability validation now closes it and fresh final fix review approved.
No Critical/Important finding remains. Final changed scope:526 passed. Root
broad Python on343f53f:2715 passed,4 skipped,known unchanged Python3.12 SQLite/WAL
baseline failure;42 frontend checks passed. Full official final-tree Python3.11/
real WebKit and exact-SHA release are still required. Evidence and decisions:
`docs/superpowers/reviews/2026-10-04-causal-family-history.md`.
