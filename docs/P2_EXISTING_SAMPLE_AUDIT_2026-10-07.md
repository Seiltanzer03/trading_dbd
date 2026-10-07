# P2: аудит существующей причинной выборки, 2026-10-07

Конечный результат: **P2 EVIDENCE-GATED**, существующая ограниченная выборка
проверена; новая family-модель/прогноз/вклад не созданы. Это конкретный
отрицательный результат готовности данных, не поиск, доказывающий отсутствие
прогнозного сигнала. P1 сохраняется BLOCKED: пользователь подтвердил отсутствие
отдельной площадки для независимого scheduler и выбрал продолжение доступной
работы. Новые облачные ресурсы/права не создаются.

## Проверенная база

Код main b41a5a1590e84c4146279104ffa67c0a7da7712c, tree
 a4385f5763e916e402bb94ed78562671303963eb: содержимое совпадает с head PR #410
f06eb8360bd786ddd8ee173171a16161a80e71ff. Использован существующий artifact
11468461697, CI run 37590901260. Digest ZIP проверен. Экспорт получен в 11:00:59
по Москве; 32 снимка выбраны из 271 строки metadata. Более поздние данные и
остальной исторический архив не исследованы. Сырые записи повторно не экспортированы.

Существующий `scripts.run_edge_family_pipeline` один раз выполнен off-host
на этих байтах с текущим кодом. Production/DB writes=0, network calls=0,
LLM calls=0. Профильные pipeline-контракты: 47 passed. Полный CI повторно ради
этого read-only результата не запускался; runtime-код не изменялся.

## Цепочка готовности

| Звено | Проверенный результат | Ограничение |
| --- | --- | --- |
| Источник | Два frozen снимка содержат официальные macro features: CPI6, NFP6, ISM manufacturing14/services14, FOMC8 distinct names | Имена признаков не являются независимыми release/trade observations |
| Историческая выборка | 32 archive episodes, 15 разных сделок; 32/32 digest корректны | Только bounded export, не весь архив |
| Причинные action labels | 0 admitted dataset rows | Геометрия, position evidence и price/R ниже |
| Обученная модель | 0, NO_VALIDATED_MODELS | Нет пригодных строк, не вывод об отсутствии сигнала |
| Новый runtime forecast | 0; runtime_context.json не создан | Источник сам по себе прогноз не создаёт |
| Новый фактический вклад | 0; activation не выполнялась | Существующие иные компоненты/голоса этим не переоцениваются |

31/32 снимка проходят структурную валидацию. Это **не** 31 пригодная обучающая
строка: дальнейшие обязательные проверки дают ноль. Причины dataset exclusion:

| Причина | Снимков |
| --- | ---: |
| EXECUTION_GEOMETRY_INCOMPLETE | 26 |
| INDEPENDENT_EXECUTING_POSITION_EVIDENCE_REQUIRED | 4 |
| FROZEN_EXECUTION_DIRECTION_OR_RANGE_INVALID | 1 |
| RETAINED_OBSERVATION_PRICE_AND_R_MISMATCH | 1 |

Две macro записи содержат фактический официальный контекст. Они не получают
surprise-признак без prepublication consensus. Event feature packets отсутствуют:
reaction audit отдельно отказал дважды с REACTION_CONFIGURED_DIRECT_CONTEXT_UNSUPPORTED,
novelty — один раз с NOVELTY_CURRENT_PUBLICATION_STALE. Отсутствие consensus не
выдаётся за универсальную причину отказа всех макро/событийных признаков.

Сохранённые deterministic FOMC имеют собственный historical-reconstruction,
research-only provenance; semantic записи — собственные publication/receipt clocks.
Исторические read overlays не подмешивались; исходные frozen source paths и
байты остались неизменными. Старые snapshots не дополняются текущими фактами,
геометрией или неподтверждённой broker-позицией.

## Дальнейшая граница

Для нового обучения нужны фактически сохранённые causal position/cost/outcome
joins и mature independent cohorts. Existing trainer требует ≥40 trade groups,
20 train после purge и два хронологических OOS блока по10, положительный OOS
MSE gain. Даже полное исправление геометрии выбранных 15 сделок не достигает
этого порога. Порог не ослабляется, искусственная модель не создаётся.

P2 остаётся с конкретной готовностью этой выборки; P3–P6 этим аудитом не
объявляются завершёнными. Их существующие источники и модели проверяются
отдельно по LOCKED_GLOBAL_PLAN_2026-10-07.md. Повтор того же pipeline без новых
входов не нужен. Численные доказательства и исходные digests — соседний JSON;
raw snapshots, broker/account records и персональные identity в отчёт не включены.

## Следующий P3: ограниченная проверка тех же сохранённых входов

Одна frozen intermarket запись содержит три завершённые исторические серии и
три linked-return записи, 16 допущенных признаков: Coinbase returns/relative
returns, lag и related-crypto breadth. Adapter сообщает RECEIVED_HISTORY_FEATURES,
отказов source/applicability нет. Parent identity соответствует reviewed target;
это factual source context, не доказательство соответствия executing broker,
не проверка полезности и не готовая модель. Каждый признак встречается на одном
снимке; frozen intermarket forecasts=0, common joined training rows=0.

Session packets/features/forecasts отсутствуют. Derived session context имеется
в 31 структурно корректном снимке, но все 31 сообщают
HOLIDAY_AND_EARLY_CLOSE_CALENDAR_REQUIRED. Complete calendars=0, known market
open=0. New York/London/UTC clock context встречается на 8/8/15 снимках;
это не calendar evidence и не проведённый DST-transition test.

Результат P3 для этой выборки — EVIDENCE-GATED: factual intermarket input имеется,
нужных frozen calendar inputs и причинных training joins нет. Не утверждается,
что полный внешний архив или другие настроенные инструменты проверены. Новая
выгрузка, overlay, fitting, network и writes для этой проверки не выполнялись.
