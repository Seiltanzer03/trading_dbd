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
  Итоговый профиль51passed/1 existing compiler skip; PR434 final CI37975077820
  3424passed/5skipped, real pinned SQLite/WebKit/acceptance green.
- [x] Один manual cloud restore с capacity_report=true, прочитать actual
  geometry и крупнейшие объекты; выбрать следующий безопасный bounded шаг.
- [x] Разрешённый merge: PR434/main208ed35e67c039939f33e1755e2415fadf7322dd;
  main CI37975605651 green. Deploy37975874469 failed at pre-fetch headroom;
  recovery37975945289 delivered exact SHA but could not rebuild a local slot.
  EXIT recovery restarted service. Diagnostic37977266534 attempt2 proves
  external/local HTTP200 at19:16UTC; startup completed19:09:14UTC in555s.
  This is availability, not completed readiness/smoke/publication acceptance.

Ruling: сначала измерить проверенный off-host snapshot — live база не имеет
места для второй копии и её структура ещё не измерена; цена: dated snapshot
не доказывает сегодняшние размеры, и capacity report не разрешает pruning.

Actual cloud restore37975291324: raw hash/SQLite verification PASS; source
snapshot2026-10-08 09:13–09:20UTC, sourceSHA77407e8e, database17117564928B,
4179093pages ×4096B, freelist19pages (77824B). Capacity status
TABLE_SCAN_TIMED_OUT at180s: table allocation is unknown. No repeat table scan
or restore of the same unchanged snapshot is needed.

### Autonomous recovery and return to global development, 2026-10-09

User explicitly asks to resolve the release blocker and continue autonomously.
Finite queue: (1) recover real deploy headroom; (2) prevent Git-history regrowth;
(3) complete existing A/C manual-decision release through exact-SHA acceptance;
(4) retain the finite B input matrix and define the next concrete usable input.
Do not restart unchanged model searches or redo shipped decision/management code.

Diagnostic37979493253/job113986098599 confirms liveSHA208ed35e, HTTP200,
free219MiB, .git979MiB, venv211MiB, research361MiB; liveDB17727135744B,
WAL8726192B. The research registry is retained evidence, not disposable cache.
Other projects and live SQLite remain outside cleanup scope.

Bounded fix: reconstruct only .git from canonical depth-one roots. Preserve
every ref, HEAD, original config and clean tracked files; refuse linked metadata,
dirty files, unpublished refs, concurrent locks or insufficient staging space.
Validate the complete staged Git repository, exchange metadata atomically,
validate again, then remove only the verified reconstructible old metadata.
No service stop, code reset, data deletion or integrity-gate weakening.
Both network deployment fetch and offline SHA staging retain shallow roots;
otherwise the existing full-history staging bundle would undo the repair.
Opt-in manual repair shares the production concurrency group and requires
current green main SHA. Default diagnostics remain read-only.

Eight actual-Git regressions observed RED→GREEN. One final scoped review found
two Important: interrupted bundle import rolled back only shallow metadata,
and incoming deploy could cancel a serialized repair. Two-revision real bundle
interruption reproduced broken parent links RED; retain imported root boundary,
use noncanceling production workflows and a shared host lock GREEN. Deferred
minor: remote symbolic HEAD is rebuilt as a direct ref to the same object;
running HEAD and every resolved ref/object remain unchanged. No re-review.
Final-tree CI37981463914 green:3432passed/5skipped, pinned SQLite/WebKit/acceptance
green. Actual repair37981962155 preserved all4 resolved refs and runningHEAD208ed35:
Git1025622016B→38313984B, freed987308032B; root210MiB→1.2GiB. Post-repair
diagnostic113994601427: external/local/public HTTP200, NRestarts0. LiveDB and
application history were untouched. PR435 merged as14b2fe9d3bb79138421854a83e24574c2bc6bd1d.

Actual deploy37982521624 accepted that SHA: delivery/readiness/smoke/public/
orchestration success, all seven status contexts green. Math37984728775 and
sources37984731678 published for the same SHA. FOMC37984734727 transport receipt
does not prove native materialization or continuous600s cadence. Task4/D technical
release is complete. Durability remains explicitly degraded: no local verified
full backup; readiness used the previously user-authorized low-disk startup and
SKIPPED_USER_AUTHORIZED_LOW_DISK restore. No fresh full-backup/RPO claim.
Management smoke assessed12 actions in isolated temporary fixtures, real orders0;
this is functional acceptance, not empirical trading profit. Future liveDB growth
remains a capacity dependency; repeating the same Git repair is not the next task.

Ruling: production Git history is fully reconstructible only after each retained
ref was fetched and verified from canonical remote; unpublished local commits
fail closed. Tradeoff: local ancestry queries are shallow; complete code history
remains in GitHub. All application history, research evidence and risk gates stay.

### Next B input-chain correction: stated FOMC target ranges

One reproducible existing-input defect, not a new source or model: the official
2026-09-16 policy sentence inserts `by 1/4 percentage point` before the target.
The parser skipped the stated3.75–4.00 range and immutable cached payloads kept
null rates. Extend only this exact grammar; do not search unrelated later numeric
sentences or use the stated change as a substitute for an exact previous range.

Retained releases use a labelled rate-only read projection from at most two
existing exact bodies (64KiB each), verifying hashes, exact predecessor identity,
publication ordering and prospective receipt cutoff. Existing non-null values,
semantic/text measurements, rows, frozen observations and original clocks stay.
No network acquisition, training, missing consensus/cost fabrication or new votes.
Source contract remains v1; the measurement parser has a separate version tag.
Six regressions observed RED due missing target values; relevant macro/source/
receipt/reaction/novelty/overlay profile229passed2.02s. One final review:
Critical/Important none; reviewer independently verified204focused tests.
Deferred minor: flattened frozen EDE feature provenance omits the two parser/
projection labels, while the full frozen payload and historical feature provenance
retain them. No repeated review or unrelated changes. Full exact-tree CI follows.
New correction is not declared production until final CI and actual acceptance.

Ruling: correct the existing deterministic measurement at read time because
immutable cached releases are intentionally never re-ingested or rewritten —
cost: historical measurements are explicitly projections from dated stored texts,
not first-published versioned documents or newly received market evidence.
