# Проверка внешнего автономного пакета, 2026-10-06

Исходный отчёт `unified_edge_consolidation_report.md` нельзя принимать как
доказательство завершения задач 1–4. Проверены реальные GitHub refs, PR и runs.

| Задача | Результат проверки | Принятое действие |
| --- | --- | --- |
| CFTC OI | `51292d59`: две замены identity; исходный hash, receipt и dependency сохранены, trainer guard не ослаблен. Независимый reviewer подтвердил точный regression. GitHub tests и companion runs зелёные | Интегрировать существующий producer с исправленной identity |
| Event liveness | `86609935`: 53 строки; cutoff получен до fetch, затем записан как receipt и materialization. Прямой HTTP helper обходит общий бюджет и native store | Реализацию не выпускать; сохранить исходную ветку для истории. В интеграционной ветке commit отменён |
| Readiness audit | `5898692a`: 48 строк; только instruments из bundle, отсутствующий readiness трактуется как «All features present». Нет model/sample/runtime различий | Исправить bounded отчёт для всех 13×8 ячеек, machine JSON, hash и SHA; UNKNOWN/null для отсутствующих counts/votes. Разделить входы и прогноз в текущем UI/audit |
| Сверка плана | Приложенный отчёт утверждает завершение без сверки восьми этапов и actual live/model evidence | Эта таблица и roadmap фиксируют техническую готовность отдельно от внешних данных и эффективности |

На момент проверки открыт только draft PR #401, head `51292d59`.
Для `86609935` и `5898692a` GitHub Actions runs отсутствуют; они не получили
заявленный полный CI. Ветки независимы от main, поэтому различие с OI-веткой
не означает удаление OI их авторами. Integrator объединил их с сохранением OI.

Локальная проверка исправленного ограниченного пакета: 526 passed (source,
runtime, history, trainer, dataset, adapters, OI, matrix, audit); node syntax
и unified audit rendering smoke PASS. Полный официальный CI и production
acceptance фиксируются отдельно на финальном SHA; локальный профиль их не заменяет.

## Сверка восьми этапов исходного плана

| Этап | Состояние реализации и точный остаток |
| --- | --- |
| 1: инструменты/действия | Конфигурация 13 инструментов и 12 действий существует. Матрица сообщает отсутствующие capture/family явно |
| 2: единая экономика | Существующий selector, cost/risk/CVaR gates сохранены; executing-account cost feed остаётся внешним входом |
| 3: независимая LLM оценка | Существующий frozen provider path/lineage сохранён; новых LLM вызовов нет |
| 4: математический edge | Существующий конечный поиск и exact-source архив сохранены. В этом пакете нового per-instrument поиска или прибыльности не доказано |
| 5: новые семьи | OI producer принят; все восемь adapters существуют. Event timely native acquisition не завершена. Source bundle не доказывает trained family forecasts |
| 6: режим/применимость | Существующие regime/mapping/dedup/OOS gates не изменены |
| 7: сравнение и объяснение | Текущий audit/UI разделяет available input и forecast; matrix помечает sample counts и applied runtime votes как NOT_REPORTED, а не выдумывает их |
| 8: выпуск/наблюдение | Предыдущая принятая база PR #400/main c85585f7. Новый выпуск требует официального полного CI и exact-SHA production evidence |

## Следующий конкретный блок разработки

Реальный prospective event producer: переиспользовать существующий deterministic
native store и официальный HTML release-time parser, сохранить фактический
первый HTTP receipt и local materialization до frozen cutoff, не выдумывать
времена из момента запуска collector. Доставка должна сохранять revisions и
immediate predecessor, не выдавать старую пару при отказе newest. Acquisition
вписать в явный ограниченный off-host бюджет и штатный transport/publication,
с записью всех запросов/байтов. В request API сеть не добавлять.

Сначала проверить существующие `build_offhost_macro_bundle.py`,
`build_offhost_historical_macro_bundle.py`, deterministic bootstrap/store и
runtime transport. Простой повторный fetch без native persistence не закрывает
этот блок. После producer→store→frozen regression — отдельный PR и release.

Licensed PIT consensus, validated CFD mappings, independent OOS samples,
executing broker costs и actual live efficacy остаются отдельными зависимостями.
104 ячейки audit — покрытие отчёта, а не 104 готовых прогноза.

## Интеграция и запуск выпуска

PR #402 объединён как `0dd04d1a28704896c36514b23338ce22f8e33147`,
код/tree `2bbce19223e37dfce4e7aadd46644c095303d628`. Официальный PR CI
`37417126041`: 3048 passed, 4 skipped, одна существующая warning; реальные
WebKit и все применимые companion workflows успешны. PR #401 закрыт как
superseded, его OI fix сохранён.

GitHub сформировал squash message из истории checkpoint и перенёс в сообщение
старый маркер пропуска CI. Поэтому push CI на этом main SHA не запустился,
и production пока не принят. Следующий PR восстанавливает обычный выпуск и включает отдельно проверенное
исправление native same-URL revision ingestion; полное timely acquisition
по-прежнему не объявляется завершённым. Integrator должен при
merge явно задать чистый commit_message: автоматическое объединение старых
сообщений недопустимо. Повторный main CI здесь нужен для восстановления
штатного автоматического deployment trigger, не для дополнительной полировки.
Новую production acceptance записать только после exact-SHA подтверждения.
