# Конечная приёмка основного unified edge — 13.10.2026

Этот checkpoint следует основному A→B→C→D и не открывает новый цикл улучшений.
Принятый production: b96cca445056297f43fd8978d2f4751d6cbff44c, tree8dbc01df.
Main CI38398238486:3932passed/5skipped/13subtests, pinnedSQLite/frontend/WebKit.
Deploy38398804577: delivery/readiness/functional/public/orchestration success;
все7контекстов зелёные. Проверки относятся к этому SHA, а не старому baseline.

## A — менеджмент и единственное решение

| Действия | Параметр / смысл | Фактическая граница |
| --- | --- | --- |
| HOLD | Сохранить текущий остаток | Полноценный контроль, без команды исполнения |
| CLOSE_10 / CLOSE_25 / CLOSE_50 | Доля текущего остатка | Повторная фиксация считает уменьшенный остаток; ACK идемпотентен |
| EXIT | Закрыть текущий остаток | Журнал и остаток обновляются после ручного подтверждения |
| MOVE_TO_BE | stop_price = entry | План BE не превращается в исполненный стоп без подтверждения |
| TIGHTEN_STOP / TRAIL_GAMMA_FLIP | Проверенный stop_price | Не расширяют риск; нужна допустимая геометрия и источник |
| REDUCE_TAKE / EXTEND_TAKE | take_price | Проверенные текущая цена и границы, опционный anchor не становится live при задержке |
| SCALE_OUT_ON_SPIKE | trigger_price + close_fraction | Установка условия не закрывает позицию; фактическое исполнение отдельное |
| TIME_STOP | deadline_ts | Установка условия не считается выходом; deadline и реальное исполнение проверяются |

Серверный smoke12:56:02UTC: UNIFIED_MANAGEMENT_12_ACTIONS applied=True,
ISOLATED_UNIFIED_MANAGEMENT PASS,12оценённых действий/7расширенных,
выбран TIME_STOP; orders0/paidcalls0/realpositionmutations0. Это изолированный
контрактный снимок на установленном коде, не доказательство торговой прибыли.

Путь: исходные котировки/фактическая геометрия → общий банк/издержки/CVaR →
допуск кандидатов → независимые оценки → единый winner → повторный live guard →
атомарная публикация одного decision → ручной ACK → следующий review.
Даты источников сохранены через compaction; late builder не переименовывает
старую цену в свежую. При изменении позиции/цены/барьеров публикация отвергается.
Provider fallback prose не подавляет независимое структурированное мнение.

Владеющие регрессии уже прошли обязательный CI:
test_ai_quote_snapshot_continuity.py, test_ai_execution_barrier_preflight.py,
test_ai_management_api.py, test_decision_publication.py, test_position_state.py,
test_llm_shadow_action_execution.py, test_management_execution_confirmation.py,
test_unified_candidate_economics.py, test_management_completion.py.
Новые одинаковые прогоны не запущены.

## C — веса и пользовательский аудит

| Компонент | balanced | llm20 |
| --- | --- | --- |
| Количественная база | 40% | 40% |
| Математический edge | 15% | 13,5% |
| Active Edge | 15% | 13,5% |
| Исторические LLM-гипотезы | 15% | 13% |
| Текущий LLM | 15% | 20% |
| Итого | 100% | 100% |

quant100 остаётся контролем. Реальные веса пересчитываются по доступности,
возрасту, качеству и общему происхождению; пропущенный score остаётся null.
Отказ конкретного эксперта для действия перераспределяет бюджет среди
имеющих оценку. Самооценка уверенности LLM не задаёт вес.
Нулевая доля количественного голоса не отключает риск и издержки.
Никакие проценты не возвращают запрещённого кандидата в выбор.

UI показывает nominal/applied, оценки и contributions выбранного кандидата,
Expected/CVaR/costs против HOLD, все12строк с причинами, решения без каждого
компонента/семейства, сравнение схем. Сохранённый аудит читается отдельно от
prose и не предлагает повторное исполнение старого решения.
Регрессии: test_unified_edge_ensemble.py, test_expert_registry.py,
test_unified_edge_audit.py, test_edge_family_working.py,
test_unified_edge_comparison.py; frontend/archive/unified smokes и WebKit green.
Сохранённый finite comparison32разбора/4парные разные сделки не пересчитывается
ради новых процентов; оптимальные веса и прибыль этим не доказаны.

## B — фактическая модельная матрица

Math38400999540/artifact11815970416 опубликован для b96cca4,385800bytes.
Исходный SHA256 a7aaa1a6434e2a29a326761120f0d86b94cb40e458a9412a4d8ee9472a0eb002.
25реальных fits:21current +4retained option с исходными неизменными clocks/hashes.
Runtime validator проверил все25, ошибок0. Исторически поддержаны14сочетаний.
Полные104ячейки в UNIFIED_EDGE_ACCEPTED_MATRIX_2026-10-13.json; текущий
forecast/contribution остаётся null без актуального active-position снимка.

| Семейство | Реальный результат конечного прохода | Что остаётся |
| --- | --- | --- |
| Межрыночное | Обученные/поддержанные отдельные головы | Текущие синхронные признаки и применимость к конкретному действию |
| Сессионное | Обученные/поддержанные отдельные головы | Текущая сессия/календарь/горизонт |
| Опционное |5fitted, одна поддержанная группа XAG direction15m | Остальные OOS отрицательные/недостаточные; Q/P не смешиваются |
| Макро | HEAD_TRAINING_UNAVAILABLE / NO_HEAD_WITH_CAUSAL_VALIDATION | Официальные причинные CPI/NFP и достаточные независимые ISM folds; proxy-regime не заменяет их |
| Событийное | CAPTURED_EVENT_FEATURE_HISTORY_MISSING | Причинные event features/outcomes и сохранённый до события consensus |
| Поток | CAPTURED_ORDER_FLOW_FEATURE_HISTORY_MISSING | Последовательная реальная история и подтверждённый transfer к выполняемому инструменту |
| Позиционирование | CAPTURED_POSITIONING_FEATURE_HISTORY_MISSING | PIT COT/OI feature history с проверенным mapping |
| Value/carry | CAPTURED_VALUE_CARRY_FEATURE_HISTORY_MISSING | PIT valuation/carry; новый broker-cost модуль исключён пользователем |

Crypto configured-series mapping не подтверждён; они не получают выдуманный
family forecast. SOURCE_UNAVAILABLE и fitted-but-unsupported различаются.
Sources38401003724 доставил реальные факты13инструментов,models_produced0;
это не ещё13обученных прогнозов.

## D — граница и точное продолжение

A/C и техническая поставка приняты. Полная прогнозная готовность B остаётся
частичной по перечисленным входам, а фактический live LLM-вклад не наблюдался:
production read13:34UTC не содержал trade_id/position_state.
Не открывать тестовую production-позицию и не создавать broker confirmation
ради демонстрации всех процентов. Валидный эксперт влияет на применимые решения;
объективно отсутствующий прогноз не заменяется синтетическим.

Yandex migration377496272registry bytes +303435330capacity bytes полностью
проверена; live preflight EDE38404251655 READY,free1438126080bytes,
резерв1GiB сохранён. Inventory83956observations/23725resolutions,
65DATA_READY из96 — покрытие, не прибыль/допуск политики.
EDE export/research/publication ещё в работе; новый fit/result не заявлен.
Не запускать дубликат, не менять expected b96cca4 до его финальной публикации.
Затем сверить доступные результаты и их включение в текущие matcher/registry;
исправлять только конкретный воспроизводимый разрыв основного пути.
Новый источник/новая модель/оптимизация вне frozen scope не добавляется.

Побочный narrow PR464 FOMC HTTP уже имеет green3936CI/review, но остаётся
изолированным и не является новой задачей основного контура. Его выпуск можно
совместить после текущей exact-SHA цепочки; новые улучшения вокруг него не искать.
