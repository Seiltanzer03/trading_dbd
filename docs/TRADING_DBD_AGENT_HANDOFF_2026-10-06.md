# ТЗ другому ИИ-агенту: автономное продолжение unified edge

Дата передачи: 06.10.2026, Москва. Проект: **Seiltanzer03/trading_dbd**.

## 0. Приказ на исполнение — начать сразу

Ты принимаешь разработку существующего проекта, а не проектируешь новый терминал.
Работай автономно по этому конечному пакету задач, пока есть доступ и лимит сеанса.
Не спрашивай «продолжать?» после каждого шага. Пользователь уже поручил продолжать
исходный план и отменил повторные паузы согласования design/spec/plan.

Сначала закончи начатый блок CFTC OI, затем выполни задачи 2 и 3 ниже. Если всё
успеваешь, выполни задачу 4. Не переключайся на косметику и посторонние баги.
Это не разрешение бесконечно расширять scope, ослаблять проверки или объявлять
ненайденное преимущество найденным.

**Передача результата:** подготовь проверяемые draft PR и краткий отчёт. Агент
предыдущего чата затем проверит твою работу. Для этого пакета не объединяй свои
PR сам до его review. После одобрения применяется обычный разрешённый выпуск:
mandatory green → merge → automatic GitHub deploy → exact-SHA production acceptance.
Пока review ожидается, можно работать над следующей независимой задачей в другой
ветке; нельзя менять уже проверяемый diff ради посторонней работы.

## 1. Где работать и что читать

- GitHub: https://github.com/Seiltanzer03/trading_dbd
- Production: http://94.241.171.182:8790/
- Главный источник требований: `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`.
  Это копия пользовательского `trading_dbd_unified_edge_plan_2026-10-01.md`.
- Далее: `AGENTS.md`, `docs/OPENBUILD_SUPERPOWERS_WORKFLOW.md`,
  `docs/UNIFIED_EDGE_DEVELOPMENT_ROADMAP.md`,
  `docs/UNIFIED_EDGE_REMAINING_IMPLEMENTATION.md`.
- Старый remainder-план: `docs/superpowers/plans/2026-10-02-unified-edge-remainder.md`.
  Его незачёркнутые checkbox НЕ доказывают, что код отсутствует: большая часть
  уже реализована последующими PR. Проверяй реальные модули и историю, не повторяй их.
- Текущий узкий блок: `docs/superpowers/specs/2026-10-05-cftc-open-interest-design.md`
  и `docs/superpowers/plans/2026-10-05-cftc-open-interest.md`.

Используй подключённый GitHub или обычный разрешённый git. Вход через GitHub
действительно есть. Отдельный браузер может быть не авторизован — это НЕ означает,
что GitHub-плагин недоступен. Не предлагай пользователю заново входить, пока не
проверил штатное подключение. Внешний приватный чат не видит локальные worktrees;
передаваемые изменения бери из draft PR/ветки, а не рассчитывай на чужой `/workspace`.

## 2. Принятая production-база

Последний подтверждённый релиз — [PR #400](https://github.com/Seiltanzer03/trading_dbd/pull/400).

| Объект | Подтверждённое значение |
| --- | --- |
| main SHA | `c85585f78afc6909796b1afbd12c2692ff1557d3` |
| tree | `8c1b469c3af1d6e8f549ec32fac18c0fc1c6e98b` |
| Official Python CI | 3020 passed, 4 skipped, 1 существующее warning |
| Deploy | `37360246698` |
| Source publication | `37362684451` |
| Mathematical publication | `37362681192` |
| Post-research acceptance | `37362687781` |

Все семь обязательных contexts для этой базы были success:
`ci/full-webkit`, `production/seiltanzer`, `g1e/storage-intelligence`,
`g1m/management-edge`, `g1s/short-horizon`, `g1m/local-feedback`,
`production/functional-smoke`. Перед новой интеграцией перепроверь свежий main:
эта запись фиксирует прошлый факт, не блокирует законные следующие релизы.

PR #400 исправил потерю точности r0/T/stop_r и пяти канонических цен; перестал
искусственно сдвигать достигнутый стоп в допустимую область; добавил ранний
`400 execution_barrier_reached` до provider/enrichment/таймера запроса/мутаций.
Это НЕ автоматически закрывает позицию: нужно подтверждение фактического брокера.

Живая проверка AI дала **503 authoritative_price_unavailable за 78 ms**,
без опубликованного решения. Точный защитный отрицательный контракт принят;
это не успешный содержательный AI-verdict. Новая ветка 400 подтверждена настоящими
producer→API тестами, но не наблюдалась в том живом запросе. Не скрывай эту разницу.
Изолированная проверка всех 12 действий прошла с 0 orders, 0 paid calls,
0 реальных изменений позиции. Семейных обученных моделей пока **0**.

## 3. Что уже есть — не писать заново

Есть единый selector и management_decision, registry экспертов, бюджеты весов,
frozen evidence, UI/audit/ablations, общий экономический evaluator, расширенные
действия, bounded source collector, causal history producers, model producer
pipeline, portable R-geometry, independent broker-position import boundary,
broker-cost import boundary и descriptive comparison. Готовый контракт импорта
не означает, что настоящий executing account подключён.

Набор инструментов брать из актуального `seiltanzer/config.py`, сейчас 13, а не
вшивать первоначальные 10. Действия: HOLD, CLOSE_10, CLOSE_25, CLOSE_50, EXIT,
MOVE_TO_BE, TIGHTEN_STOP, TRAIL_GAMMA_FLIP, REDUCE_TAKE, EXTEND_TAKE,
SCALE_OUT_ON_SPIKE, TIME_STOP. Конкретные параметры — часть candidate identity.

Основные файлы:

- `seiltanzer/edge_family_sources.py`, `edge_family_adapters.py`,
  `edge_family_source_runtime.py`, `edge_family_history.py`;
- `edge_family_event_reaction.py`, `edge_family_event_novelty.py`;
- `edge_family_archive.py`, `edge_family_dataset.py`, `edge_family_training.py`,
  `edge_family_geometry.py`, `edge_family_action_binding.py`;
- `position_execution_context.py`, `execution_cost_context.py`,
  `unified_edge_runtime_context.py`;
- `scripts/run_edge_family_pipeline.py`, `scripts/run_unified_edge_comparison.py`,
  `scripts/build_edge_family_sources.py`, `scripts/run_mathematical_edge.py`;
- `seiltanzer/app.py`, `ai_report_semantics_guard.py`, `ai_policy_base.py`,
  `ai_verdict_base.py` — менять только если выбранная задача действительно требует.

Недавняя цепочка: #395 portable geometry, #396 causal family history,
#397 observed event reaction (первоначальный release не полностью принят),
#398 latency/capture acceptance, #399 native FOMC lexical novelty,
#400 precision/barrier acceptance. Не переносить старые «pending» из документов
как утверждение о нынешнем production; учитывай финальное доказательство конкретного SHA.

## 4. Текущая checkpoint-ветка: CFTC OI, НЕ принятый выпуск

Ветка передачи: `feat/cftc-open-interest-20261005`. Смотри её draft PR.
Исходный local product commit: `95c6523c70f6ffd93c6889f9caa64f01204f5961`,
tree `d766f930728297ee09d67acf3b6b64301fbb5fc5`. Передаваемый checkpoint содержит
также этот документ, roadmap и новый RED-тест находки. Поэтому конечный head/tree
определяй через GitHub, а не используй старый product SHA как текущий.

Реализовано: из уже полученного Legacy Futures Only CFTC JSON извлекается
`open_interest_all` для новейшего отчёта и непосредственного предыдущего;
producer `parse_cot_open_interest` создаёт отдельную запись `observed_open_interest`.
Конечные nonnegative integer counts; missing/invalid OI отказывается отдельно,
исходная noncommercial net-position запись сохраняется. Дополнительного GET нет.
OI level/percentile/history_n отделены от positioning.net. Body hash, clocks,
futures identity и dependency group сохраняются. CFTC→CFD mapping не валидируется
по предположению. Primary field references записаны в spec.

Фактические проверки ДО нового finding-теста: baseline 77 passed; meaningful
RED 22 failed / 1 passed; GREEN 23 passed; related profile **563 passed за 7.23 s**.
Первоначальный import typo дал collection error, был исправлен до meaningful RED.
Это не было доказательством дефекта. Один старый complete-body test fixture
дополнен настоящим полем OI; отсутствующий OI отдельно покрыт new tests.

**Незавершённый Important finding:** исходная net-position и новая OI history
используют один source_id. `edge_family_training.py`, `_row_reason`,
`history_bindings` связывает source_id с tuple body/series/kind/unit/category/etc.
Поскольку kind/category различны, настоящий смешанный frozen training row
отклоняется с `FEATURE_HISTORY_PROVENANCE_INVALID`.

Root добавил точную регрессию:
`tests/test_cftc_open_interest.py::test_mixed_actual_body_histories_pass_trainer_identity_binding`.
Она действительно дала **1 failed / 23 deselected, 0.45 s**, именно с этим reason.
Тест использует явно искусственные cost/mapping fixtures: это не реальные broker inputs.
Таким образом, checkpoint сейчас намеренно содержит RED, **не готов к merge**.
Первая запись checkpoint имеет `[skip ci]`, чтобы не тратить полный CI на известный
RED. После исправления сделай обычный новый commit без skip: весь required CI обязателен.

Fresh reviewer успел сообщить этот конкретный Important gap, затем завершился
по лимиту без final verdict. Независимого одобрения НЕ получено. Не выдавай
частичное сообщение или 563 старых green tests за финальную приёмку ветки.

## 5. Задача 1 — закончить OI без ослабления provenance

1. Возьми переданную ветку, не повторяй producer с нуля. Запусти только точный RED.
2. Исправь различение source observation / derived series. Предпочтительно дать
   OI отдельную детерминированную series-specific identity, сохранив связь с тем же
   original raw body/hash/receipt и общей `cftc:legacy-futures:<contract>` dependency.
   Допустим другой минимальный согласованный контракт, но НЕ отключение
   `history_bindings`, НЕ игнорирование kind/category и НЕ случайный UUID.
3. Убедись, что net и OI вместе проходят adapter → frozen runtime → dataset →
   trainer admission, а настоящий конфликт body/series/receipt всё ещё отказ.
   Отдельные IDs не делают одну CFTC публикацию двумя независимыми голосами.
4. Сохрани missing immediate predecessor, bool/nonfinite/negative/fractional,
   future receipt, invalid mapping и mixed-order coverage. Старые signed net
   positions, в том числе отрицательные, должны работать как раньше.
5. Зелёный scoped run по команде ниже, diff check, итоговый короткий отчёт в PR.
   Один независимый итоговый review после готовности; не запускать каскад reviewers.

```sh
python -m pytest -q tests/test_cftc_open_interest.py \
  tests/test_edge_family_sources.py tests/test_edge_family_history.py \
  tests/test_edge_family_adapters.py tests/test_edge_family_source_runtime.py \
  tests/test_edge_family_training.py tests/test_edge_family_dataset.py \
  tests/test_edge_family_pipeline.py
```

Задача завершена технически, когда новая регрессия GREEN, основной scoped suite
GREEN, происхождение корректно и draft PR готов к внешней проверке. Это не
автоматически означает live CFD applicability или пригодную прогнозную модель.

## 6. Задача 2 — актуальность реальных событийных источников

Цель §7: работающий prospective путь реальных macro/event наблюдений, а не только
пройденные синтетические contract tests.

Короткая инвентаризация существующего пути: официальный fetch/import → native
store first receipt/materialization → source bundle → frozen capture → adapter.
Ветку делать отдельно от OI. На инвентаризацию не тратить часы: сначала выясни
актуальные producer/workflow files через rg и предыдущие novelty/reaction specs.
Проверенный раньше остаток: daily off-host generation/hourly polling может не
дать своевременного native release pair. Проверь нынешний код, а не принимай
это как доказанный нынешний timeout/root cause.

Результат: implement минимальную bounded off-host цепочку, которая при фактической
новой публикации сохраняет её своевременно и доставляет в существующий frozen
capture. Переиспользуй доступные официальные данные и штатные workflows, без
provider calls/network/training в пользовательском POST /api/ai/request.
Обновление календаря без нового события само по себе не создаёт event features.

Обязательно: original URL/document/release identity, original body hash,
economic publication time отдельно от actual first HTTP receipt и local
materialization; deterministic newest/predecessor selection, bounded body/text,
revisions/vintage semantics; no fallback to older valid publication when newest
invalid. Существующая novelty — unsigned lexical token-set distance только
decision sentence, НЕ смысловой forecast. Reaction — фактически наблюдённое
движение после события, НЕ будущая прибыль и НЕ брокерная цена.

Не увеличивай event freshness window, чтобы «зажглась availability». Не
выдумывай pre-release consensus, backdated first receipt или broker mapping.
Не превращай редкость FOMC-событий в программный баг: если события в окне нет,
правильно показывать отсутствие. Новые endpoints/cadence должны иметь явный
bounded request/time/byte бюджет, штатные легальные пути, без обхода denial.

Проверки: actual producer→store→frozen capture happy path на контролируемых
реальных-схемах fixtures; late/future/revision/missing predecessor; no network
in request; late optional worker cannot mutate frozen snapshot. Не проводить
платный live эксперимент. Реальная observation может отсутствовать — тогда
публикуй техническую готовность и точный live blocker раздельно.

Не начинай заново режимные модели/семантический LLM news scorer. Если выяснится,
что существующая acquisition уже полная и единственный остаток — отсутствующий
реальный feed/право, не пиши пустой новый адаптер; фиксируй результат и переходи к задаче 3.

## 7. Задача 3 — честная матрица готовности и пользовательский аудит

Цель §7/§10/§12: один понятный машиночитаемый и пользовательский отчёт по всем
configured instruments × 8 families, а не перечень общих «нужны данные».

Строй его на существующих frozen source/runtime/model/comparison outputs.
Различай как минимум: реализован producer; фактически получен admissible input;
freshness; target mapping; накоплена независимая выборка; validated model;
runtime active forecast/vote. Не подменяй эти статусы единым available=true.

Для каждой отсутствующей ячейки: конкретный missing field/source либо actual
rejection reason, последний causal timestamp/hash, технический следующий шаг
или external dependency. Для model absence: проверенные independent group
counts/validation reasons, а не обещание «обучим позже». Номinal/applied weights,
dependency families и abstention должны оставаться согласованы с общим selector.

Встроить краткое объяснение в существующий audit/UI путь, без редизайна терминала
и второго management command. Обзор «модель отсутствует» не должен выглядеть как
нейтральный прогноз; null/UNAVAILABLE отличается от доступного нулевого сигнала.
Экономический Expected/CVaR не пересчитывать LLM и не переписывать ради баллов.

Отчёт должен воспроизводиться из одного сохранённого bounded input и exact SHA.
В public reports/logs нельзя раскрывать account IDs, private snapshots или
broker credentials. Никаких новых LLM вызовов для ablations/таблицы статусов.
Используй уже имеющиеся comparison keys, сохраняй обратную совместимость.

## 8. Задача 4 — конечная сверка всего плана, если задачи 1–3 готовы

Собери один consolidated completion report по восьми этапам исходного ТЗ.
По каждому инструменту/math target/horizon — фактический результат существующего
конечного поиска, источник, ограничения и runtime status. По всем восьми
families — реальные producer/feature/model статусы. По 12 действиям — итоговый
selector/applicability/audit без повторного написания уже сделанных компонентов.

Исправляй только конкретные проверяемые пробелы реализации, которые обнаружены
этой сверкой и используют реальные доступные поля. Если inputs нет, явный
dependency — корректный результат, но не «всё ТЗ завершено». Не подбирай веса
бесконечным перебором и не обещай edge для каждого инструмента заранее.

Не распыляйся: сначала один полезный producer/consumer разрыв, профильная проверка,
готовый PR, затем следующий независимый блок. Работы, требующие licensed source,
платного доступа, настоящего executing account или пользовательского выбора,
оставляй в конкретном blockers list и продолжай доступную независимую часть.

## 9. Неизменяемые ограничения

- Единственный management_decision; HOLD полноценный кандидат.
- Hard risk/cost/geometry gates действуют при любых весах; отключение quant vote
  не отключает расчёт риска и издержек. Не расширять стопы ради допуска.
- No fabricated prices/sources/consensus/fills/costs/profit. Неизвестные costs
  остаются unknown, ACK receipt — не broker fill/execution time.
- Без модели голос 0. Feature availability/пройденный тест не доказывает edge.
- Source publication/receipt/materialization ≤ T0 по соответствующему контракту;
  exact instrument/horizon/identity/proxy binding, no historical future leakage.
- Не ослаблять OOS minimum 20 validation observations, 2 folds, 10 distinct
  trade groups per validation fold; existing grouped purge и positive proper-score
  gain. Пара десятков reviews одной сделки — не независимые группы.
- Никакого network/training внутри API. Capture budget 0.25 s сохраняется;
  provider guard/functional budget не увеличивать ради зелёной проверки.
- Существующие bounds сохранять: archive 512 episodes/96MB; export 32 reviews,
  snapshot 2MB, path 6000, transport32MB; runtime contexts48KB; selected facts8KB.
  Источник collector:16GET/16MB/120s, production bundle<1MB. Проверяй точные
  нынешние константы, не увеличивай их без конкретной необходимости и анализа.
- Exchange/ETF/CFD связи не объявлять точными без validated mapping. Q/IV/OI
  не доказывают physical/VRP/dealer positioning. Crypto USD proxy не Binance USDT.
- Synthetic fixtures только tests; они не входят в реальные datasets/model artifacts.
- Не включать paid providers; не делать реальные ордера/закрытия/позиционные мутации.
- Не удалять БД, backups, worktrees/old evidence; не менять existing secrets.
- Не обходить HTTP403/451/429/security restrictions/permissions, не использовать
  alternate IP/native tools для обхода запрета. Сообщать blocker, продолжать безопасное.

## 10. GitHub → PR → GitHub Actions → production: как мы выпускаем

Никакого прямого редактирования production вместо релиза. Один integrator owns Git
и shared contracts; concurrent writers только в разных worktrees/ветках.
Сначала git status, текущие main/head/tree, AGENTS. Preserve dirty user changes.
Не reset --hard, не checkout -- чужих файлов и не git clean -fdx.

Обычный цикл: separate feature branch → осмысленный commit → push → **draft PR**
в main → профильная проверка → mandatory official CI на exact final revision →
внешний review → ready → merge → automatic deploy через `.github/workflows/deploy.yml`.
Deploy может запускаться workflow_run после CI; изучи фактические dependencies,
не дублируй ручным dispatch уже идущий automatic run.

Актуальные workflows: `ci.yml` (название проверь), `edge-family-sources.yml`,
`edge-family-training.yml`, `unified-edge-comparison.yml`, `mathematical-edge.yml`,
`production-sha-stage.yml`, `production-readiness.yml`,
`production-functional-smoke.yml`, `production-post-research.yml`, `deploy.yml`.
Path filters важны: отсутствие trigger — не success запущенного workflow.
Применимые new tests должны запускаться full CI и нужным профильным workflow.

Перед merge root сверяет свежий main, PR final head, synthetic merge commit/tree,
mergeability, обязательные contexts и review. Совпавший tree может иметь разные
SHAs из-за squash/API истории: фиксировать это явно, не путать content с ancestry.
Green относится только к проверенному revision; новый product commit требует
проверок изменённой области и нового exact-tree CI.

После merge проверять **тот же main SHA**: delivery/installed HEAD, readiness,
functional smoke, public terminal HTTP, post-research acceptance, публикации source
и math. Живой защитный 400/503 допускается только по существующему строгому
контракту, без decision publication и в budget; произвольный 422/503/timeout — не PASS.
Изолированная позитивная 12-action проверка не заменяет проверку реального ответа.

Source/Math success и models=0 совместимы; «всё работает» без оговорок тут неверно.
Не ждать необязательное расширенное исследование EDE до бесконечности после
готовой обязательной acceptance. Не начинать ещё один PR только для косметики.

Read-only production diagnostics выполняем через существующие reviewable
GitHub Actions SSH channel, exact expected SHA/owner guards/малые budgets.
Не доставать SSH_PASSWORD из secrets или logs и не перепрофилировать credentials.
Пользователь разрешил чтение; это не разрешение менять состояние позиции.
На сервере HTTP; HTTPS ранее дал TLS failure — не считать это GitHub access blocker.

Raw actual training workflow разрешён только для private repository. Если repo
public, не снимаем private guard и не выгружаем private snapshots/account context
в публичные Actions artifacts. 14-day artifacts — НЕ постоянный longitudinal archive;
existing restore contract должен быть учтён, никакой фиктивной истории из latest.

## 11. Быстрое исполнение и экономия лимитов

**Сокращаем повторные проверки и церемонии, не доказательства безопасности.**

1. Один writer на coupled block. Не создавай 7 аналитиков перед 30 строками кода.
2. Одно короткое design/plan уточнение из этого ТЗ, сразу реализация. Нет новых
   «одобрите документ»/«выберите метод» на каждом шаге.
3. Критический behavioral regression сначала RED→GREEN; затем relevant tests
   всей изменённой области. Не пишем tests на текст README/банальные constants.
4. Один official full Python3.11 + frontend/real WebKit CI для финального PR tree.
   Используем exact-SHA результаты, caches, уже сохранённые fixtures/scenarios.
   Не гоняем одинаковые тяжёлые suites после doc-only правки без причины.
5. Один итоговый независимый review coupled feature. Исправления Critical/Important
   test-first; проверка затронутого пути. Minor style/nice-to-have записываем, не
   раздуваем release. Если reviewer остановлен лимитом, approval не выдумываем.
6. Python3.12 здесь раньше повторял unchanged baseline failure
   `tests/test_storage_sparse_backup_guard.py::test_quiescent_sparse_clone_replays_wal_only_into_backup`
   с `authoritative database changed during quiescent sparse clone`. Не подавлять,
   не ослаблять storage guard. Official Python3.11 был green. Не повторять этот
   дорогой local broad run без изменения/причины, не заявлять local all-green.
7. Длинные logs сохранять и читать tail/точный failure, а не печатать весь registry.
   Один status poll по достаточному интервалу, не каждую секунду.
8. Пока CI работает, независимая задача в другом tree или подготовка PR report.
   Нельзя менять проверяемый head и считать старый green результат новым.
9. Нормальные обязательные проверки дождаться. Если прогресса нет 30–45 минут
   сверх обычного времени — diagnose infra, без blind restart цикл/увеличения
   timeout. Без удаления DB/backup/защитных gates ради PASS.
10. Не останавливайся только потому, что один внешний источник отсутствует:
    запись точного blocker → следующая независимая доступная задача из пакета.

## 12. Критерий окончания автономного пакета и отчёт для моего review

Задача 1 исправлена и PR готов к review; задачи 2–3 реализованы либо имеют
проверенные конкретные external blockers; задача 4 не породила бесконечную
переработку готового кода. Все изменения доступны через GitHub, не только локально.

В финальном сообщении дай:

- ссылки на draft PR/ветки, exact base/head/tree и порядок integration;
- что изменилось относительно исходного плана, какие поля/consumer paths;
- new RED→GREEN evidence и actual scoped/official CI counts/run URLs;
- findings/fixes, unresolved Important issues, deferred Minor без сокрытия;
- live verification отдельно от contract fixtures; missing models/inputs честно;
- bounded autonomous-work checkpoints и конкретный следующий шаг;
- если релиз уже разрешён root review и выполнен — exact merged SHA и все
  deploy/readiness/smoke/source/math acceptance IDs, включая реальные ответы API.

Не говори «готово» за весь unified plan, если закончены только контракты или один
source feature. Не обещай работу в фоне после окончания сеанса: сохраняй checkpoint
в GitHub и сообщай статус. Если execution окружение/лимит прервал работу, оставь
достаточный handoff, чтобы следующему агенту не приходилось начинать с нуля.

**Ожидаемый результат для пользователя:** несколько конечных полезных изменений
по исходному плану с доступными reviewable PR, а не ещё один круг бесконечных
аудитов. Приоритет — работающая реализация, короткие релевантные доказательства,
однократный обязательный release CI и честные остатки данных.
