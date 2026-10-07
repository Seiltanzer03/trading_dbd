# P6: semantic readiness — рабочая точка 2026-10-07

Принятый production baseline: main
`89593d761c1de2a7f3ee1d10859a3715679fd7db`, PR #413,
tree `eab83f65fc59ed1cec65b5b99ecefd7bf4fedda5`.

Пользователь подтвердил, что запасной сервер недоступен для установки.
На него ничего не установлено; другой проект не затронут. Независимый P1
scheduler подготовлен, но не активирован. GitHub delivery работает; её
квитанции не доказывают независимый 600-second cadence.

## Найденный контрактный дефект

Materialized G1C status считал fit readiness по общей смеси Q-наблюдений,
хотя установленный `g1_shadow_refinement` обучает отдельно semantic scopes
по q_relation/proxy_transform. Fixture с двумя группами по 40 наблюдений
показывал READY_TO_FIT при aggregate=80, хотя ни одна группа не удовлетворяла
raw_n>=60. Второй дефект: сводка называла все g1c_contract_errors критическими,
вместо установленной authoritative классификации.

В `research_scalability` SQL projection eligible rows дополнена существующими
base_cohort_id/base_cohort_json. Readiness использует общий
`_scope_fit_readiness`; critical count — `_critical_error_count` из refinement.
Общее число ошибок сохраняется отдельно. Metadata scopes и critical types
возвращается без подмены модели состоянием READY_TO_FIT.

Данные, пороги, native expiry, исключение SOURCE_MUTATED membership, модели,
OOS и production authority не изменены. Обучение/поиск не запускались.
Это исправление представления действующего контракта, не допуск модели.

## Проверки и выпуск

Два новых behavioral regression cases failed до исправления.
Materialized readiness / semantic integrity / artifact integrity / T0 admission /
shadow calibration: **19 passed**, один installed Starlette/httpx deprecation
warning. Независимый read-only review: блокирующих замечаний нет.

Сохранённая рабочая ветка: `fix/p6-materialized-semantic-readiness`.
Этот diff **не выпущен**. Следующий общий выпуск требует final review,
обязательный full CI на exact final tree, затем merge и automatic
deploy/readiness/smoke/public HTTP/exact-SHA publications. Успешные проверки
baseline #413 не считаются проверками данного нового diff. Повторный полный
CI и production restart ради текущего checkpoint не запускались.

P2–P6 сохраняют evidence-gated ограничения. Старые audit counts не выдаются
за свежие production observations. Оценка сроков технической доводки является
планировочным диапазоном; недоступные источники, независимая площадка P1,
реальные сделки и созревание expiry не обеспечиваются скоростью модели.
