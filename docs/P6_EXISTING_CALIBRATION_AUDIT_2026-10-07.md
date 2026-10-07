# P6: опционная калибровка и instrument identity, 2026-10-07

Конечный scope: использовать опубликованный mathematical result и текущий
bounded calibration cache; не повторять поиск/обучение без новых inputs.
База b41a5a1, math run37617807999/artifact11480028705, ZIP digest проверен.
Численная ограниченная матрица и digests — соседний JSON; raw records не включены.

## Сохранённая калибровка

Один read-only GET calibration сообщает LIVE_CACHE: Q captured9775,
resolved3729, reported eligible/effective718, positive400/negative318.
Но fit_run_n=0, frozen_model_n=0, prospective predictions0, models.items=[].
Physical probability published=false, OOS validated=false, production authority=false.
Эти reported counts получены до исправления materialized summary и не являются
новой независимой валидацией718 training records.

Найден воспроизводимый дефект: materialized threshold называл достаточно
наполненную выборку FITTED_UNVALIDATED без модели, жёстко подставлял temporal
periods/expiry clusters0, опускал147 зарегистрированных contract errors при
расчёте G1D readiness. Он также использовал иной nested terminal label,
приближённую CDF интерполяцию и distinct dependency groups вместо authoritative
nonoverlap effective N. Это препятствует честной оценке готовности P6.

Исправлен владеющий слой research_scalability: существующая SQL проекция
только eligible rows дополнена clocks/instrument/dependency group; source-mutated
и forecast-eval-ineligible записи исключаются как в G1C. Статистика использует
те же canonical labels, CDF и effective-N функции; fit gate показывает
READY_TO_FIT, а G1D использует actual periods/expiry clusters и critical errors.
Полная baseline/model scan на HTTP path не добавлена; обучение не запущено.
Raw→effective sample gates, native expiry, Q/P separation и полномочия неизменны.

Четыре regression cases воспроизвели отказ до изменения; профильные tests19
passed после него (один installed Starlette/httpx deprecation warning).
Финальная обязательная CI/release проверка выполняется отдельно на exact revision.
Обученная physical P/model этим bugfix не создаётся. P6 EVIDENCE-GATED до
реальных пригодных causal cohorts/артефактов и устранения contract blockers.

## Неподдержанные identity

| Target | Существующий finite search | Management scope |
| --- | --- | --- |
| JPY100 | NO_SUPPORTED_ADVANTAGE_YET, selected30m | NO_WEIGHT |
| BTCUSD | WORKING_SUPPORTED, selected120m | DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED |
| ETHUSD | WORKING_SUPPORTED, selected30m | DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED |
| SOLUSD | WORKING_SUPPORTED, selected15m | DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED |

Crypto archive — Coinbase USD; configured series — Binance USDT. Это разные
price identities/currency basis, не executing-broker archive. Supported
математическая цель не равна broker-management advantage/net-profit proof.
Option history не использовалась, GEX unsupported/missing archive contract.
JPY100/crypto не активируются через этот пакет. Повторного model search нет.

## Граница глобальной карты

P1 BLOCKED: отдельная scheduler площадка отсутствует по ответу пользователя.
P2–P5 имеют сохранённые finite evidence-gated/unavailable результаты выбранных
входов; P6 сохраняет отрицательную readiness и исправляет её достоверность.
Дальше — существующее накопление causal/mature evidence по G.1 и устранение
конкретных source/mapping/contract blockers, без новой ветки функций и без
ожидания прибыльности. G.2 policy promotion — отдельное решение за пределами
P1–P6. Этот отчёт не объявляет всё исходное ТЗ эмпирически выполненным.
