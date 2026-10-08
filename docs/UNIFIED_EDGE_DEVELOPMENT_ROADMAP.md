# Единый edge: путь разработки и критерии завершения

Текущая принятая production-база: PR #410, main
`b41a5a1590e84c4146279104ffa67c0a7da7712c`, deploy `37615509675`;
полный CI, readiness и smoke подтверждены. Оставшийся объём и порядок пакетов
зафиксированы в [общем плане](LOCKED_GLOBAL_PLAN_2026-10-07.md).
Ниже сохраняется исторический журнал, включая старые pending записи.

Источник требований: приложенный `01-trading_dbd_unified_edge_plan_2026-10-01.md`,
разделы 1–12. Этот документ сохраняет полный объём исходного плана и не заменяет
его более узким планом предыдущего выпуска `EDGE_COMPLETION_PLAN.md`.

Историческая production-база до пакетов #404–#407: PR #403, main
`0cefc1acdb90db4af5f4c62fc813382a621f111a`, tree
`3b0805c66a53121602933c344ca60ca8bc50f761` (2026-10-06).
Ветка runtime-интеграции `feat/unified-edge-full-integration` объединена.
Конечная граница текущей версии и бюджет проверок:
`docs/UNIFIED_EDGE_VERSION_FINISH_LINE_2026-10-06.md`.
Журнал ниже хронологический: старые pending записи не заменяют текущую базу.

2026-10-06: пакет PR #404 объединён в main
`5e83860d0cc07365ca3f0745059637769b6fefa6`, полный CI зелёный (3068 passed,
4 skipped). Production deploy `37431568335` отказал при запуске: установленный
FastAPI не имеет `add_event_handler`. Это отказ интеграции, не завершённый
production-выпуск. Исправление переводит prospective FOMC worker на уже
используемый lifespan: запуск после входа в существующий lifespan, остановка
до закрытия app/store, сохранение исходного lifespan state и cleanup при
исключении. Точечная регрессия и collector-профиль: 12 passed; новый exact-SHA
CI/deploy/readiness/smoke остаются обязательными перед принятием.

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

### Causal history accepted baseline, 2026-10-05

PR #396 supersedes the earlier pending-release history note. Accepted main
`0481fae4f4d6bc5ca5fbd2349babba85869af95c`, tree
`6e6d196cd4c3f473c744b6890a24915b20cc2798` (local content-equivalent
`9c8fde4c502949d3f26ac4b2be2eeeb299f4ee19`) passed official Python3.11 CI
`37227153708` (2757 passed, 4 skipped), real WebKit, staging `37227371138`
and deploy `37227371140`. Delivery/readiness/functional/public and isolated
12-action checks passed with zero orders, paid calls or real-position mutations.
Source `37228433324` and math `37228431773` were published for that exact SHA;
all seven CI/production contexts succeeded. Source models remain zero across 13
instruments; inventory `37228476233` reports 65 DATA_READY and 27 insufficient
independent evidence. Optional EDE v13 run `37230075582` was shut down by the
runner after computation, before publication, without an assertion failure or
blind rerun. This does not change mandatory acceptance or prove effectiveness.

### Observed event reaction scoped implementation, 2026-10-05

The prospective producer now reads immutable actual first FOMC receipt and copies
the existing configured locked feed batch, without new fetching or old snapshot
enrichment. Its bounded proof preserves actual Binance USDT/Yahoo FX identity and
the explicit configured-market-context/non-broker role. Consecutive completed
closes produce observed 1m/5m returns and start delay, with conservative support
windows and one shared release dependency group. Legacy surprise still requires
strictly prepublication matching consensus.

New snapshots append the bounded packet before review identity; combined selected
facts above 8KB leave existing facts unchanged and explicitly refuse attachment.
Runtime validates at immutable bundle capture, adapter recomputes at review, frozen
dataset replay stays independent of future paths, and trainer recomputes values,
support manifests, configured identity, clocks and applicability. No risk, cost,
geometry, OOS, purge or sample floor was changed. Missing models remain zero vote.
Saved RED/GREEN and scoped verification are in
`.superpowers/sdd/2026-10-05-observed-event-reaction/task-1-report.md`; fresh task/
whole-feature review and root broad/official exact-tree CI/release are still
required. No genuine qualifying frozen release/target-price pair was established
by the prior source audit, and these artificial fixtures do not create one.
Broader mappings/broker equivalence, lawful PIT consensus, calibrated event action
models, general text novelty, value/carry and sufficient independent observations
remain explicit residuals; no profitability or causal-effect claim is made.

### Bounded optional reaction capture and request diagnostics, 2026-10-05

Optional reaction capture now has a fixed 0.25-second API budget including shared
executor queue time. Capture receives only a private bounded input projection;
only a timely completed result can commit the owned source/audit fields. Existing
source facts survive refusals, cancellation and late completion. Engine-local
admission prevents overlapping optional work until an already-running worker
actually completes. Received-release reads try the shared store lock for the
whole read; feed capture tries its lock and checks the 4096-bar bound before
copying. Historical/admissible reads and shared SQLite settings are unchanged.

A focused warning-level structured trace correlates materializer preflight,
route stages, actual thread submission/start/end and final response status with
one server-generated request ID. It emits only stage names, monotonic timings,
status and exception class; no request/source/financial payloads or exception
text. Diagnostic sink failures preserve success/error/cancellation behavior,
and direct no-argument route calls remain compatible.

Local event-controlled regressions reproduce and cover lock waits, executor
queue budget, late/cancelled work, immutable review bytes, oversized/malformed
input and sanitized traces. Evidence and scoped verification are preserved in
`.superpowers/sdd/2026-10-05-ai-request-latency/task-1-report.md`. These defects do
not establish the cause of PR #397's production POST timeout: its saved log has
no actual request stage trace. Provider guard6s/8s, acceptance12s/transport14s,
risk/cost/geometry/proof/sample gates and zero missing-model votes remain.
Fresh task/whole-feature review, root broad verification, mandatory exact-tree
CI and same-SHA production acceptance remain required; no release acceptance or
predictive efficacy is claimed here.

### PR #398 accepted baseline, 2026-10-05

This supersedes the latency release-pending note above. PR #398 passed mandatory
official CI, merge/deploy, readiness/functional/public/orchestration checks and
exact-SHA source/math publications; all seven required production contexts were
green for main `e574f4622639b09517ce7ee51c8eb536a7ede2cd`. Actual AI smoke was the
accepted authoritative-price-unavailable 503 in 112ms; the positive isolated
12-action fixture made zero orders, paid calls or real-position mutations.
Neither deployed trace observations nor causality of the earlier timeout are
claimed. Source liveness and predictive efficacy remain separate residuals.

### Native FOMC policy-sentence novelty implementation, 2026-10-05

The new native reader selects the newest publication/vintage and its exact frozen
predecessor, validates both original normalized body hashes and native identities,
and requires both first HTTP receipt and first local materialization before
capture. Text is bounded before SQL materialization; current publication,
not import/fetch time, starts the four-hour freshness window. Invalid newest
records never fall back to an older pair.

The frozen versioned extension retains each complete original target-rate
decision sentence, offsets and distinct projection digest. It calculates only
unsigned token-set Jaccard distance. Adapter/runtime/trainer validation
recomputes the metric, exact normalized metadata and original release bindings;
immutable dataset replay uses frozen bytes. Original full-body hashes remain
lineage verified at the store, rather than being authenticated by a snippet.
Invalid explicit extensions reject the whole record before any event feature
is inserted; consensus requirements for surprise and missing-model zero votes
remain unchanged.

Reaction and novelty share the existing single private 0.25-second capture owner
and deadline, with independent outcomes/audits and one timely commit. Prior facts
survive combined byte refusal, cancellation, timeout and late completion.
Synchronous reaction, risk/cost/geometry, sample/OOS/purge floors, acquisition,
refresh cadence and provider limits are unchanged. No new source/model pool,
weight, dependency or network path was added.

Local affected profile: 616 passed with one existing Starlette/httpx deprecation
warning; RED/GREEN and author self-review are recorded in
`.superpowers/sdd/2026-10-05-fomc-policy-sentence-novelty/task-1-report.md`.
Fresh independent task/whole-feature review, root broad checks and mandatory
official exact-tree CI/production acceptance are still pending for this feature.
No actual native pair, fresh event coverage or semantic/predictive effect was
established. Daily offhost generation/hourly polling and bundle validity remain
timely-source coverage blockers; full-statement semantics, lawful PIT consensus,
calibrated event action models and sufficient independent observations remain
residuals. These artificial contract fixtures do not prove profitability.

### PR #400 accepted baseline and CFTC OI continuation, 2026-10-05

PR #400 merged main `c85585f78afc6909796b1afbd12c2692ff1557d3`, tree
`8c1b469c3af1d6e8f549ec32fac18c0fc1c6e98b`. Official PR/main CI: 3020 passed,
4 skipped, one existing warning; real WebKit and all seven required contexts
succeeded. Deploy `37360246698`, source `37362684451`, math `37362681192`
and post-research `37362687781` passed for this exact SHA. Actual AI returned
`503 authoritative_price_unavailable` in 78ms, no decision published. The new
400 reached-barrier path is proven by producer→API tests, not this live request.
Precision and terminal-event preflight do not imply broker fills or restored
price authority. PR #399's earlier failed smoke is superseded by this exact-tree
release acceptance, without claiming the old request's undocumented root cause.

Next bounded §7 addition retains CFTC total futures open interest and its
immediate-report change from the already fetched body. It uses the existing
position history contract and shared dependency group, separate OI namespaces,
conservative first-seen clocks and unchanged CFTC→CFD mapping refusal. Missing
or malformed OI keeps the original noncommercial positioning record. No extra
GETs, model votes, learned edge or profitability are implied. The source
producer/profile is implemented; final review, official exact-tree CI and release
acceptance remain pending until evidenced in the new PR. Missing independent
executing-broker inputs, consensus, applicable mappings and model samples remain
explicit residuals of the original plan.

### External agent package review, 2026-10-06

See `docs/UNIFIED_EDGE_AGENT_REVIEW_2026-10-06.md` for factual acceptance and
original eight-stage reconciliation. OI deterministic series identity is
accepted independently. The event branch is rejected for prefetch receipt
backdating, unaccounted acquisition and missing native first-receipt persistence;
its implementation is excluded from this release, not declared complete.
Readiness audit now includes all configured 13×8 cells, explicit missing
captures/families, capture freshness, mapping refusals and separate source/model/
forecast status. Missing sample counts and active runtime votes remain null/
NOT_REPORTED. Existing terminal audit/UI distinguishes input from forecast.
The off-host source workflow saves the matrix with input hash and exact code SHA.
Profile526 and node syntax/render smoke pass; official final-tree CI and new
production acceptance remain pending until recorded. No new live data/model
coverage, trained forecasts, executing costs or profit is asserted.

### Native FOMC same-URL revision ingestion, 2026-10-06

A verified revised official page at an already-known dated URL was ignored by
historical off-host ingestion before reaching the immutable native store. The
regression reads an actual-schema verified file, reparses/hashes all bodies,
imports two originals, then a changed same-URL body with later actual receipt;
baseline refused to add the third row. Ingestion now delegates URL/body identity
to the existing native store, which retains the original immutable receipt and
materialization, caches exact duplicates, and adds a changed verified vintage.
The actual bound loader, parser/strict predecessor store and clock guards remain.
RED1 failed; affected native/offhost/novelty profile110 passed. Independent review
and official final-tree CI/release still pending. Acquisition cadence/transport
is unchanged and timely prospective event coverage is not declared complete.


### Storage remediation and prospective observability, 2026-10-07

Accepted runtime baseline: main `6b047e45a6653bb15c52ea95c030e6117da2e11b`,
with exact-SHA deployment/functional smoke run `37434556616`. The finite math
and comparison reports remain historical evidence at their recorded source
SHA; no new profitability, crypto mapping or family model is asserted.

External operational cleanup run `37546490117` removed retired runner files,
raising free space from 372,940,800 to 1,790,099,456 bytes. The logs show a
runner **restart**, not reload. Run `37547972224` repeated that restart; these
operations must not remain in a diagnostic workflow triggered on main push.
PR #407 restores read-only diagnostics and removes hardcoded acceptance leases
and cross-workflow research dispatch.

Run `37548024889` exported 16,616,505,344 bytes off-host and logged
`OFFHOST_BACKUP_VERIFIED=1`, object
`backups/v1/daily-slot-6/snapshot.sqlite3.gz` in the configured private Yandex
bucket. Export, upload and immutable-input verification succeeded. Overall
research failed: after an EDE discovery summary, the runner reported exit 143
and a shutdown signal; publish/handoff did not run. The cause of the runner
shutdown is not established. A summary log is not a successfully published
research artifact. Completed backup manifests and discovery outputs are now
checkpointed before later expensive work; publication still requires the whole
audit to pass. This repair does not re-export the already verified database.

Ten native deterministic FOMC records and historical bootstrap status do not
prove current prospective delivery. The existing macro status API now exposes
`fomc_prospective`: the worker's in-memory capture hash, exact publication SHA,
receipt/materialization clocks, stored/skipped counts and current admission
reason. It performs no request-time SQLite, file or network access. Historical
bootstrap errors remain distinct. Focused profile: 19 tests passed; official
final-head CI and release acceptance for PR #407 remain pending until evidenced.



### Выпуск исправления разбора и bounded P2/P3 readiness, 2026-10-07

PR #410: согласованы экономика общего сравнительного банка, отдельный bridge
контроль и независимая консервативная проверка допуска. Source lineage quant
показывает реально использованный банк; bounded compaction сохраняет metric
identity, малые веса не округляются до нуля, flat LLM preference не получает
голоса. Self-confidence не подменяет calibrated accuracy. Риск/издержки и
исходный отказ admission не ослаблены. Независимый final review одобрен;
CI PR run37590901100: 3098 passed /4 skipped, real WebKit green. Main CI
37615234667 и deploy37615509675 green; readiness/function/public HTTP и все
семь обязательных production contexts green. Public management_ui.js byte-for-byte
соответствует выпущенному source. Post-research37617817743/G1M37617852126 green.

Exact-SHA math37617807999 и source37617811579 опубликованы на b41a5a1;
source bundle: 13 инструментов, models_produced=0. FOMC37617814632 опубликован
и native materialization принят: capture digest
 d52a314d897dc81e77d688a69d80104a5ce5784f81a51e6d7d247eab44020521,
materialized_at1791374532.5897613, admissible=true, WITHIN_TARGET. Это одна
свежая доставка, не доказательство cadence. Пользователь подтвердил отсутствие
отдельной площадки scheduler; на тот момент P1 BLOCKED до появления такой площадки.

Без новой выгрузки использован existing comparison artifact11468461697 из
run37590901260 (его headf06eb83 имеет тот же code tree). Existing family pipeline
один раз выполнен off-host: 32episodes/15trades, 0 admitted action rows, 0 models,
нового runtime artifact нет. Dataset exclusions26 incomplete geometry,4 missing
independent position evidence,1 invalid range/direction,1 price/R mismatch.
31 structural snapshot validations не являются31 training rows. Pipeline contracts
47 passed. Два snapshots содержат официальные macro facts; consensus/reaction/
novelty имеют отдельные недостатки. P2 EVIDENCE-GATED в этой bounded выборке,
не global no-signal conclusion. Исторические overlays не подмешивались.

Ограниченный P3 preflight тех же inputs:1 intermarket packet/16features,
3 completed source histories; session calendar packets0,31 derived contexts
требуют holiday/early-close calendar. Forecasts/causal training joins0. Coinbase
source context не доказывает executing-broker equivalence. P3 EVIDENCE-GATED
для выбранных snapshots; другие инструменты/полный архив этим не проверены.
Подробные aggregate результаты, scope и digests:
`P2_EXISTING_SAMPLE_AUDIT_2026-10-07.md` / `.json`.

Runtime код в readiness-аудите не изменялся, DB/production writes и network/LLM
calls pipeline=0. Raw broker/account snapshots не включены в новые отчёты.
Следующий шаг — отдельная проверка доступных синхронных P3 входов и календаря
по locked plan; повторить тот же поиск/обучение без новых входов нельзя.


### P3/P4: существующий all-13 source capture, 2026-10-07

Следующий finite source audit использует уже опубликованный artifact11480592477
из run37617811579 exact b41a5a1, capture1791374394.2332888. Межрыночные factual
features16/target на всех13, forecast0; crypto context не target broker prices.
NYSE cash calendar packets3 отвергнуты по неподтверждённому CFD mapping,
session features/forecasts0. P3 EVIDENCE-GATED в этой проверенной базе.

P4: BTC/ETH/SOL имеют один book packet с реальной последовательной top pair
и один неполный tape page каждый. Интервалы2.296608/3.836987/2.300446s,
trades437/420/38, window_complete=false. Mapping validated=false; все6packets
отвергнуты, у остальных10 packets0. P4 заканчивается UNAVAILABLE для broker-CFD
forecast; sampled top flow не full incremental OFI/CFD book.

P5 preflight: CFTC XAU/XAG/EURUSD fetched/parsed, historical receipt first-seen
не backdated, mapping unvalidated; carry packets в bundle0. Это не вывод об
отсутствии executing-broker cost import. Следующая задача: bounded P5 проверка
сохранённых broker carry/cost provenance, units и отсутствия double counting.
P6 этим не закрыт. Runtime diff/training/activation/новые LLM и полный CI0.
Детали и all-13 matrix: P3_P4_EXISTING_SOURCE_AUDIT_2026-10-07.md / .json.

P5 bounded cost inspection дополнен на existing32 reviews: broker rollover
schedule/execution cost context/position execution units top-level0/32;17
candidate rollover audits BROKER_QUOTE_UNAVAILABLE,2 context audits
BROKER_EXECUTION_COST_CONTEXT_UNCONFIGURED. Existing contract требует causal
quote/units/coverage, included_in_base_costs исключает повторное списание.
Новый behavioural test не выполнялся, model carry не выдуман. P5 EVIDENCE-GATED
в выбранных inputs; остальной архив не проверен. Следующий пакет P6.


### P6: bounded calibration truth and identity, 2026-10-07

Reused math artifact11480028705 exactb41; no repeated search. JPY100 NO_WEIGHT,
crypto diagnostic-only mapping-unvalidated despite some supported math heads.
Calibration GET: models0/fitruns0/predictions0, reported Qeligible718, errors147.
Materialized presentation falsely showed FITTED_UNVALIDATED, hardcoded periods/
expiry0, omitted errors and diverged from canonical labels/CDF/effectiveN.
Fixed bounded eligible projection/shared G1C stats/gates without model fit or
authority change. Regression4 red before fix; profile19passed. Final CI/release
evidence remains separate. P6 empirical EVIDENCE-GATED. Detailed scope:
P6_EXISTING_CALIBRATION_AUDIT_2026-10-07.md / .json.

### P6 release blocker: obsolete health-dispatched recovery, 2026-10-07

PR411 merged1b9dedfa, final treeb7bc5361, CI37625077891:3102passed/4skipped,
WebKitgreen. MainCI37625593931 and deploy37626130762 accepted delivery/readiness/
smoke/publicHTTP/all7contexts. Source37628550606, FOMC37628553001,
post-research37628555744/G1M37628611823 green.

After acceptance, health workflow37626728407's older dispatch (made during
startup) ran queued recovery37626780640. Its manual-event branch bypassed the
healthy exact-SHA guard, stopped the serving terminal at13:27Z and refused
rotation while trades.db remained open. No authoritative deletion/rotation
occurred; restore_service_on_exit attempted a restart. Math37628547881 completed
its runner computation but failed publication on localhost connection refused;
upload artifact was skipped. This run is not reported as a delivered model.

Minimal blocking fix: the existing on-host exactSHA+active+HTTP200 guard applies
also to workflow_dispatch, preventing a stale health request from stopping an
already recovered/accepted service. Unhealthy recovery, CI authority, serialized
execution and all database protection remain. Real bash guard regression failed
for healthy manual dispatch beforefix;4behavioral+existingworkflow tests19passed.
No repeat math search or manual production writes were launched to mask the
failure. New final-revision CI/release evidence remains separate.

## 2026-10-07 — P1 independent-host transport preparation

User supplied spare host 212.193.24.125 with an unrelated existing project.
One bounded SSH attempt returned Network is unreachable before authentication;
no credential use, host inventory or server modifications occurred. Historical
absence-of-host blocker above is superseded by an offered but unreachable host.

Prepared dedicated systemd service/timer and bounded forced-command FOMC SSH
transport. Independent review found delayed-producer overwrite; fixed with one
shared receiver for GitHub and spare, flock and same-SHA monotonic captured_ts.
SHA, health, freshness/body/digest, refusal and bounded stdin remain mandatory;
no shell/SFTP/forwarding privilege is needed on the new key. Other report
publication paths and evidence/authority gates are unchanged.

Transport/capture/exact-SHA/release-workflow profile: 37 passed. Read-only
independent re-review found no remaining blocker. Systemd configuration parses;
local missing target venv prevents an installation claim. Detailed isolated
activation and rollback: docs/P1_INDEPENDENT_FOMC_SCHEDULER.md.

P1 is NOT complete: no spare deployment, native materialization confirmation or
consecutive 600-second delivery receipts. Required next evidence is host access,
resource/network/time inventory, dedicated key installation and actual observed
receipts. Release evidence for this code is recorded separately in its PR.

## 2026-10-08 — P2/P3 causal-contract package

P2 family trainer previously counted different trades on one official release
as independent train/OOS groups. Explicit release dependencies now connect
trade groups transitively before chronological split and whole-group purge.
Existing group floors, costs and OOS score requirements remain; generic source
IDs and unused provenance do not inflate or erase independent groups.
P3 rejected/older calendars no longer erase or misalign an admitted session
context with its freshest features. Six regressions red on baseline; unused
provenance regression red on intermediate code; combined profile685passed.

Fresh inventory37684749072 exactfc657:78120observations/22533resolved,
macroFOMC3independent releases, numeric4–5perfeature; coverage is not a family
model. Existing research37687814068 continues off-host without duplicate work.
New forecasts/trained models/authority are not asserted. Full scope and all
P1–P6 readiness: P2_P3_CAUSAL_CONTRACT_CHECKPOINT_2026-10-08.md.
Exact final CI and release evidence belong in the package PR.

## 2026-10-08 — Overnight P2–P6 admission/economics package

Finite user-authorized combined package closes explicit label-clock and integer
OOS admission (P2), stale book rewrapping (P4), executing carry identity/units
(P5), and expiry crossing during option prediction writes (P6). Existing gates,
legacy omitted-context compatibility, source/action set and authority remain.
Independent review identified SQLite writer-wait boundary: fixed with writer
reservation before deadline check and real contention/transaction controls.
Final integrated profile558passed; final revision CI
and exact-SHA acceptance belong in PR. Fresh existing EDE pass1 discovery:
8063 eligible rows,3960 hypotheses,119 outer candidates,0 all-gate passes.
Baseline inventory37696953612:78195observations/22552resolved; macro release
counts still insufficient. Full evidence/remaining P1–P6 boundaries:
NIGHT_EDGE_CONTRACT_CHECKPOINT_2026-10-08.md.

## 2026-10-08 — Immutable transition loading package

Existing overnight audit37705574141 completed discovery, then transition was
cancelled by runner shutdown at01:17:46Z. Root cause is unproven. Reproduced raw
JSON amplification is removed: immutable source batches32, requested-ID frozen
augmentation batches128, and horizon-local15/30/60 inputs retain unresolved
transform context. Rows, coverage, baseline counters and fingerprint match.
Mutable/live source extraction remains atomic under lock after independent
review and an indexed same-connection mutation regression. Profile106passed.
Synthetic real SQLite augmentation allocation control:17270825 to900475bytes;
not market evidence or a total-memory guarantee. Exact CI/release evidence belongs
in the package PR. No new gate passes/models, scheduler, sources or search budget.

## 2026-10-08 — P2/P3 source/outcome/forecast causal-chain package

Three finite tasks preserve actual receipt and declared availability separately,
reject conflicting explicit imported outcome units/origin/execution assumptions,
and carry/check their canonical semantics through model packaging and runtime.
Legacy omissions stay compatible; prepublication consensus and OOS gates remain.
False surprise, normalized late receipt, wrong-unit/origin fitting and artifact
votes were reproduced before changes. Combined profile1015passed20.46s.
Fresh inventory37734156374 exact54988fc:78615observations/22642resolutions,
65DATA_READY/27insufficient-independent/2G1M-only/2quality-only features; coverage
is not trained family edge. Existing frozen audit37736484124 continues without
duplicate export/research. Factual P1–P6 chain and concrete input boundaries:
FAMILY_CAUSAL_READINESS_2026-10-08.md. Final review/CI/release evidence in PR.
# 2026-10-08 — Current research snapshot from a verified historical seed

Support package for the existing mathematical/discovery audit, not a new source
or model search budget. Accepted parentPR421/main3a7e040; source/mathematical/FOMC
publications match that SHA. Earlier audit37736484124 exhausted the unchanged
1GiB filesystem reserve before discovery/transition; WAL alone is not proven
as the cause.

Restore explicitly verifies an existing live storage slot as a historical seed,
without full-backup retirement authority. Current SQLite refresh revalidates
bytes, retains old source clocks/SHA separately, and writes fresh research
cutoff only on success. Run-owned origin PID/birth/executable cleanup is confirmed
before fresh manifest; primary abort errors survive cleanup errors. Capacity
reports filesystem free and WAL growth separately. No live DB/WAL deletion,
reserve relaxation, new storage resources or duplicate manual research.

Plan/verification boundaries: LIVE_SNAPSHOT_SEED_PLAN_2026-10-08.md. Actual pinned
copier/protocol/wrapper integration passed locally; native process cleanup is
required in final CI due sandbox PID/proc mismatch. Actual production adequacy
and transition recovery remain pending this package's release/automatic chain.

## 2026-10-08 — Verified export and finite discovery/transition result

PR422/main77407e8 passed required release/publication receipts. Automatic
37753286968 completed actual stored-slot verification, seeded live refresh,
confirmed origin exit and immutable17117564928-byte input;1GiB reserve retained.
Discovery3960hypotheses/8126eligible rows/116outer candidates/0allgatepasses;
transition1392hypotheses/21innerFDRpasses/0stabilitypasses,2EARLY_CONTEXT.
Full actual receipts and P1–P6 boundaries: EDGE_AUDIT_CHECKPOINT_2026-10-08.md.
Active-edge37761416181 continues; no duplicate manual search or profit claim.

A deterministic ranking regression reproduces valid zero delta being treated
as a missing metric in both per-horizon and combined transition lists. The owned
ranking now distinguishes zero from None, retains missing penalty/ID tie-break
and unchanged admission/authority. Three regressions RED→GREEN; combined
transition/loading/discovery/selective/comparison profile115passed1.42s.
Final review/CI/release belong in the correction PR; not inferred here.

## 2026-10-08 — P2 bounded refresh of retained historical outcomes

Private history retention now feeds incomplete frozen-horizon review identities
back into the existing read-only exporter. Indexed probes admit only reviews
with newer actual path observations; at most half of the32 export slots refresh
history, leaving at least half for the existing recent/instrument/time selection.
Complete, truncated or unusable frozen horizons are not repeatedly requested.
Snapshots/features/source clocks remain original; actual paths still obey the
frozen horizon, every-point and first-bracketing-observation contract. Identity
conflicts remain tombstoned and model/cost/OOS gates are unchanged.

Real SQLite regressions reproduce an old review falling outside the latest512
metadata window and demonstrate private restore→export→append-only merge. Public
output adds numeric requested/refreshed counts only; IDs and raw paths remain
private. Combined archive/export/dataset/training/runtime profile523passed7.97s.
This repairs one missing historical-outcome link in P2/P3; it does not create
missing broker costs, consensus or forecast votes. Final release/actual refresh
receipts are recorded in the package PR.

## 2026-10-08 — P3 retained-history readiness by configured instrument

The existing private pipeline now emits a bounded intermarket/session matrix
for all configured instruments. It distinguishes retained and usable frozen
reviews, admitted causal-feature reviews, verified complete executing-cost
reviews, net-action label reviews, and training cohorts/models. Actions from
one review are counted once; independent-group coverage is the maximum of one
cohort, never a sum across actions, geometries or horizons. Packaged models are
explicitly not active production models.

This projection reuses already assembled archive/dataset/training artifacts and
existing frozen snapshot, source/mapping/clock/scope and cost validation. No
network acquisition, replay or model fitting is repeated. Public output has
configured instrument/family names, bounded numeric counts and fixed status
codes only; raw snapshots, trade/review IDs, feature values and provider reasons
stay private. Historical coverage is explicitly separate from live freshness.
Unusable frozen geometry is not mislabeled as an unavailable market source.

Profile588passed8.47s; real replay output counts, no-repeat-replay, privacy,
context/future exclusion and byte-bound controls. Final CI, release and actual
per-instrument production result belong in the package PR. This describes P3
input/evidence boundaries; it does not claim new models or net profit.

## 2026-10-08 — P4 sampled liquidity measurement and historical chain

Existing two-top observations now preserve venue and canonical sampled scope in
feature provenance and frozen training rows. Limited tape features preserve
window completeness; neither packet is full incremental OFI or executing CFD
book evidence. Source mapping, clocks, 60s age, costs, labels and OOS gates stay.

The existing private pipeline projects P4 order_flow readiness for all13
configured instruments alongside unchanged P3 contracts in ONE bounded frozen
validation pass. No duplicate replay/fitting/acquisition. Retained versus usable
reviews, admitted inputs, complete broker costs, distinct net-label reviews,
maximum single-cohort groups and packaged models have separate counters; absent
sequential input/mapping has an explicit status, not a no-signal claim. Public
output stays fixed names/counts/enums under existing16000-byte bound, with no
private source/position IDs. Historical coverage is not current live readiness.
Final release and actual private-run evidence belong in the package PR.

## 2026-10-08 — P5 executing carry quote contract

The value/carry source adapter now uses the existing rollover matcher for
explicitly declared broker/account/trade/direction and position-unit context.
A conflicting quote cannot be reported available. Cost inclusion is an explicit
boolean, future rollover time is required and normalized R must be finite.
Unknown inclusion/date/units stays unavailable. Signed credits remain measured
credits, already-included costs stay marked, with no extra directional vote or
automatic expense application. Quotes without full declared execution context
remain explicitly quote-only; matching declared fields is not broker-fill proof.
No new provider, broker access, fitting or G.1/policy admission relaxation.
Exact release, actual source limitations and test receipts belong in the PR.
