# Эффективное завершение unified edge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Один implementer, один final reviewer всего пакета. Пользователь 2026-10-09 поручил зафиксировать приоритеты и сразу действовать; дополнительный approval этап не применяется.

**Goal:** Выпустить пригодный ручной терминал с математикой, восемью честно представленными семействами, общими весами, одним решением, 12 действиями и понятным сравнением.
**Architecture:** Используем существующие ensemble, scenario economics, family pipeline и UI. Новые подсистемы, серверы и источники не создаём. Недоступные входы завершаются конкретным блокером и не удерживают весь выпуск.
**Tech Stack:** Существующие Python/SQLite, JavaScript, GitHub CI/deploy.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, §§1–12; приоритеты пользователя 2026-10-09.

## Global Constraints

- Этот план по прямому указанию пользователя заменяет последовательность P1→P6. Объём исходного ТЗ сохраняется.
- Единственный management_decision, ручное подтверждение, никаких реальных тестовых ордеров.
- Риск, source clocks, identity, purging и реальные издержки обязательны. Отсутствие данных — UNAVAILABLE, не ноль и не готовая модель.
- Исторический working math остаётся действующим: LIMITED_HISTORICAL с половинным вкладом, STABLE_HISTORICAL отдельно; G.1 не ослабляется.
- Уже выпущенные задачи не повторять. На пакет: один проход существующих данных, профиль изменённого кода, один review. Full CI один раз для общего финального tree.
- Не повторять search/training без новых пригодных входов. Накопление будущих событий и доказательство прибыли не входят в срок технического выпуска.
- После green обязательного CI: разрешённый merge, actual deploy/readiness/smoke/public/exact-SHA publications. Подготовленный локальный код не называется выпущенным.

## Четыре конечных пакета

| Пакет | Результат | Условие остановки |
| --- | --- | --- |
| A — решение и менеджмент | Рабочий путь существующих 12 действий, параметры, экономика, HOLD, риск и независимый LLM | Исправлены воспроизводимые блокеры основного пути; недоступная live цена остаётся явным эксплуатационным блокером |
| B — восемь edge | По каждому семейству: доступные исторические признаки → существующая модель → вклад, либо конкретный вход/сигнал UNAVAILABLE | Один finite проход утверждённых архивов; нет новых данных — нет повторного поиска. Не ждать будущего события |
| C — веса и интерфейс | 100% бюджет, реальные доли/вклады, отсутствие двойного счёта, ablations и сравнение HOLD/control/balanced/llm20 | Фиксированные стартовые схемы при недостаточной выборке; не оптимизировать веса на четырёх сделках. Блокеры видны пользователю |
| D — единый выпуск | Один review интеграции и обязательный CI, затем подтверждённый серверный выпуск | Exact-SHA acceptance и конкретный список оставшихся входов. Техническая версия завершена; empirical эффективность отдельно |

P1 scheduler, prepublication consensus, executing account feeds, неподтверждённые mappings и зрелость expiry — отдельные зависимости соответствующих блоков. Ни одна не должна превращать A/C/D в бесконечное ожидание. B остаётся явно частично готовым, если моделей нет.

## Task 1: A — сохранить identity и запретить выбор повреждённого действия

**Files:** `seiltanzer/unified_edge_ensemble.py`, `tests/test_unified_edge_ensemble.py`,
operational consumer `seiltanzer/app.py`, `tests/test_unified_edge_runtime_context.py`.
**Consumes:** frozen snapshot.active_management_candidates, существующий candidate_id(policy, parameters), готовый общий оценщик.
**Produces:** collect_candidates(snapshot) сохраняет разные корректные параметры как разные candidates; несериализуемые/не-object параметры не получают eligible/ranking_eligible и не публикуются как новое действие.

- [x] RED: parameterized regression для семи EXTENDED с NaN, Infinity, array/string parameters; snapshot не меняется; audit JSON конечен; invalid row видима с причиной и не выбрана.
- [x] GREEN: отклонить invalid parameters до dedup/ranking; не сворачивать повреждённый вариант в valid policy candidate; безопасная диагностика без raw invalid values.
- [x] Проверить корректные два TIGHTEN_STOP с разными ценами: оба остаются, invalid первый не скрывает valid следующий.
- [x] Профиль: unified ensemble/candidate economics/extended policies/audit/registry; frontend unified audit smoke. Без network/provider/order calls.
- [x] Commit, записать результат. Full CI отложен до D.

## Следующие task briefs

**Task 2 / B:** использовать существующий `scripts/run_edge_family_pipeline.py` и последний approved private archive, без повторного acquisition. Владелец признаков — `seiltanzer/edge_family_history.py`/dataset/training; менять код только по воспроизведённому препятствию. Зафиксировать 8×13 forecasts/конкретные blockers и применимость Price/path отдельно. Не обучать на непригодной геометрии/costs и не считать source adapter моделью.

**Task 3 / C:** `seiltanzer/unified_edge_ensemble.py`, `seiltanzer/expert_registry.py`, `scripts/run_unified_edge_comparison.py`, `seiltanzer/web/js/management_ui.js`. Один проход existing frozen comparison; проверить matched candidate economics, budget, withheld/available weight и action parameters в UI. Исправлять только воспроизводимый разрыв; оптимизация весов не нужна для working release.

**Task 4 / D:** интегрированный final tree, один fresh review, один Critical/Important fix pass с регрессиями; minors отдельно. Mandatory CI + существующий deployment chain. Один final отчёт по шести исходным блокам, не только P1–P6.

## Review Focus

Повреждённый вариант не скрывает корректный вариант той же policy; JSON diagnostics не переносят NaN/Inf/non-object в downstream. Original source/risk gate остаётся решающим после common repricing. Нет заявления нового physical forecast или broker profit из технических tests. На A–C разрешены краткие checkpoints; окончательная readiness только после D.

## Progress

- Baseline local `0a3bec6` (документация), runtime tree соответствует main37a1fc.
- Task 1: код исправлен локально, `42f593a`. 29 regressions RED→GREEN,
  110 profile tests и real audit renderer smoke прошли. Full CI/release pending.
- Task 2: finite evidence pass выполнен по сохранённым approved receipts,
  нового acquisition/search/training нет. Source run37850534973 (main37a1fc,
  2026-10-08): intermarket source facts 13/13, forecasts0; остальные семь
  семейств source facts0/13, forecasts0. Это dated bundle, не отсутствие
  источников во всём архиве и не утверждение current live readiness.
  Private pipeline37841345602 (d63800d, 2026-10-08): archive40 episodes,
  exported32 reviews, models0; во всех 26 P3 и13 P4 cells complete costs,
  net labels и packaged models0. Scope — выбранная retained frozen history.
  Технический finite проход завершён; реальные семейные модели остаются
  входозависимым остатком. Следующий продуктивный вход — независимая frozen
  position/cost evidence для пригодных retained paths, не ещё один search.
- Task 3: существующий comparison `p2-input/unified_edge_comparison.json`
  (SHA f06eb8360bd786ddd8ee173171a16161a80e71ff, created_ts1791360066.686887)
  прочитан один раз. 32 reviews, 4 paired distinct trades; balanced/llm20/
  quant100/legacy_control на этой matched cohort mean Δ vs HOLD0,
  verified rollover quotes0. Другой старый отчёт с legacy gain не смешивается
  с этой когортой. Optimal weights не доказаны; действующие fixed priors
  не меняются. Проверка изменённого decision/weights/audit профиля выполнена;
  family/comparison профиль и final review текущего diff выполняются.
- Task 4: pending. Подготовленный локальный код не является production.

Final review первого diff: три Important воспроизведены в 24 новых RED cases:
JSON-compatible, но невалидные scalar targets; malformed collection/entries;
NaN в raw quant_evaluation при валидных parameters. Один fix pass проверяет
обязательные targets/fraction/deadline, изолирует повреждённые entries и
отклоняет непубликуемый evaluation payload. Повторный reviewer не запускается.
Deferred minor: dictionary глубиной порядка1100 уровней может вызвать
RecursionError; normal JSON/API input и обычная вложенность не затронуты.
Этот minor не включён в текущий fix pass.
Final изменённый профиль: **631 passed, 7.15s**, frontend audit smoke PASS.
Новых family forecasts, provider calls и реальных ордеров0.
В том же Important fix pass найден downstream consumer: повреждённый entry
перед корректным победителем падал при создании manual proposal. Regression
`test_operational_extended_choice_ignores_malformed_source_entries` воспроизвела
AttributeError RED; consumer теперь пропускает non-dict перед matched action.
Это завершение одного контракта, не новая задача. Начатый CI старого head
не является проверкой этого final diff; нужен final exact-head CI.
Consumer/API/manual execution profile: **31 passed**,37.95s; один известный
installed Starlette/httpx deprecation warning. Реальные orders/provider calls0.

Release checkpoint 2026-10-09: PR #431 merged as
`631422234eeb1612c8d4a7ee499129b05bda82ae`, tree
`71cc7f9b7c5c71eb85b43a0a133733f8a1e3e869`. Final-head CI37907396652:
3407 passed /5 skipped, WebKit and acceptance green; main CI37908352109 green.
Actual deploy37908743414 delivered that SHA and passed readiness, but failed
official macro smoke. The bundle was acquired09:03:24UTC; numeric refresh
09:26:37UTC exceeded its unchanged20-minute TTL. AI returned200 in6.2s;
this alone does not prove an accepted complete release. Public/publication
handoffs were skipped, so Task4 remains pending.

Bounded Task4 repair: move the single existing official bundle acquisition,
authenticated transfer and exact-SHA installation after cold start, core pause
and readiness, immediately before unchanged functional smoke. Preserve default
TTL, source/hash/owner validation, risk and research gates. One regression
observed RED→GREEN; relevant delivery/macro profile26 passed. No new model
search/training, deployment retry or test rerun without a changed final tree.

**Решение по объёму:** заменить старый порядок по явному указанию пользователя,
сохранить fixed weights и остановить неизменный поиск при пустых causal labels.
Цена: новые family forecasts пока отсутствуют, независимый scheduler и
оптимальные веса не появятся из одного технического выпуска. Эти остатки
видны и сохраняются в исходном ТЗ; они не блокируют основной ручной механизм.

### Task 4: существующий Yandex Object Storage, 2026-10-09

Пользователь поручил использовать уже настроенное облачное пространство вместо
добавления VPS-диска. Private storage access подтверждён job113959186284:
upload/read/HEAD/list/delete одного UUID-scoped disposable probe прошли,
credentials и содержимое торговой базы не публиковались.

Read-only production diagnostic113927582709: exact main51dea626, внешний HTTP200,
root29GiB/free207MiB; data17GiB, backups7.1MiB. Исторический cloud snapshot
уже выгружен штатным off-host путём. Перенос отсутствующих локальных копий
не освобождает активную базу и не снимает обязательный pre-fetch512MiB gate.

Bounded repair: manual/scheduled cloud restore drill требует legacy local
backup_id и поэтому отвергает современные LIVE_SQLITE_RSYNC manifests.
Добавлен opt-in snapshot drill: те же полные gzip/raw hashes, размеры и immutable
SQLite quick_check; валидные source/SHA/исходные clocks сохраняются. Отдельный
receipt не разрешает local retirement и не объявляет schema-complete recovery,
fresh RPO, production acceptance или прибыль. Legacy full restore и live-seed
контракты остаются отдельными. Восемь RED cases; профиль42passed/1 compiler skip.
Один scoped review: Critical/Important нет. Deferred minor: неожиданное поле
backup_id внутри live manifest переносится в drill receipt; retirement всё равно
запрещён отдельным contract/full_restore_verified=False. Этот minor не входит
в текущий fix pass. Обязательный final-tree CI и реальное cloud восстановление
выполняются перед окончательной отметкой результата.

Ruling: облачный byte/SQLite restore proof отделён от legacy retirement proof —
у live snapshot нет доказанного local backup identity/schema manifest; цена:
новый proof не может использоваться для удаления рабочей DB или старых строк.
Physical VPS headroom остаётся отдельной незакрытой зависимостью. Следующее
уменьшение рабочей базы требует конкретного проверенного archive/readback
контракта, а не переноса SQLite-файла на S3 mount или удаления истории.

### Task 4: off-host capacity checkpoint, 2026-10-09

PR433/main7af2ab9c: actual Yandex restore37972272891 passed byte/hash/SQLite
verification; main CI37973183721 green. Deploy37973604872 failed before
fetch/restart:285752KiB <524288KiB. Service remained active on51dea626.

Следующий bounded шаг использует ту же проверенную disposable cloud DB:
opt-in `--storage-report` измеряет page/freelist geometry и allocated bytes
таблиц/индексов через dbstat, без чтения private row values. Отчёт содержит
не более64 объектов, но полные агрегаты; scan180s, timeout/нет dbstat явно
сохраняют только geometry. Default/schedule не запускают scan: отдельный
boolean input применяется лишь к workflow_dispatch. Original source clocks,
SHA и verified raw digest публикуются отдельно; historical snapshot не
переименовывается в current production measurement. Нет новых ресурсов,
VPS копий/сканов, повторного fitting, удаления данных или новых authority.

- [x] Шесть capacity regressions RED→GREEN; workflow opt-in RED→GREEN.
- [x] Профиль49passed/1 existing compiler skip; diff check PASS.
- [x] Один final review: Important schema-name collision воспроизведён
  двумя RED cases (с/без auto_vacuum). Join учитывает только table/index;
  trigger не дублирует allocated pages. Один fix pass; re-review не нужен.
  Итоговый профиль51passed/1 existing compiler skip, exact-tree CI pending.
- [ ] Один manual cloud restore с capacity_report=true, прочитать actual
  geometry и крупнейшие объекты; выбрать следующий безопасный bounded шаг.
- [ ] Разрешённый merge после CI; actual deploy/readiness receipt.

Ruling: сначала измерить проверенный off-host snapshot — live база не имеет
места для второй копии и её структура ещё не измерена; цена: dated snapshot
не доказывает сегодняшние размеры, и capacity report не разрешает pruning.
