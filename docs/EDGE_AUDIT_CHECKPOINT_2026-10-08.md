# Фактический результат выгрузки и математического аудита — 2026-10-08

Принятая ревизия PR422: `77407e8e7c987a3639edd0e88467769546e299e6`.
Все обязательные CI, delivery/readiness/smoke/public и math/source/FOMC receipts
зафиксированы в PR422. Автоматический аудит `37753286968` успешно завершил
resolve, snapshot, audit, publish и handoff-active-edge; это новые результаты,
повторный ручной поиск не запускался.

## Проверенная выгрузка

На worker проверен существующий private snapshot: `OFFHOST_LIVE_SEED_VERIFIED=1`
в09:09:57Z. Origin sourcefree1138266112bytes/WAL4305432bytes в09:13:03Z.
В314s worker записал75370496bytes при residentfile17088598016bytes; это
метрика worker I/O, не размер сетевого трафика. Origin reader exit подтверждён
в09:18:50Z; после cleanup sourcefree1137352704/WAL4367232bytes.
Неизменённый1GiB reserve соблюдён. Новый immutable input17117564928bytes
получен как LIVE_SQLITE_RSYNC в09:20:53Z. Старый seed не стал новым T0.
Один успех подтверждает совместимость сохранённой копии и этой выгрузки,
а не гарантирует запас диска при любой дальнейшей нагрузке.

## Что математически получилось

| Проверка | Фактический результат | Практический смысл |
| --- | --- | --- |
| Discovery | 3960 гипотез,8126 eligible resolved rows;116 outer evaluations;0 passed_all_historical_gates | NO MATERIAL SELECTIVE EDGE FOUND |
| Discovery maturity |1 EARLY_CONTEXT,115 INSUFFICIENT_DATA;0 provisional/research/robust | Ранний контекст не является допущенным прогнозом |
| Transition |1392 гипотез;8126 resolved;21 inner FDR passes;0 stability passes | Внутренний сигнал требует canonical review; устойчивое преимущество не подтверждено |
| Transition maturity |2 EARLY_CONTEXT,93 INSUFFICIENT_DATA | shadow и production authority остаются false |

Discovery fingerprint:b24aa04ccb835877e455b946ea649197d0423c582fa6ac1e8383fe0645f88dac.
Transition fingerprint:da8f202e15f149c13615f41af5121cf26c823513a7f8a78447f60313e1f1abe6.
Publication: EDE_OFFLOAD_PUBLISH_OK=1 в10:07:37Z. Автоматический следующий
active-edge run37761416181 ещё выполняется; его результаты не утверждаются.

## Следующий порядок глобального плана

P1 delivery работает; независимый scheduler,600s cadence и native
materialization остаются неподтверждёнными. P2 требует настоящего pre-release
consensus, независимых событий и causal position/cost/path joins; P3 требует
проверенной instrument identity/calendar/DST и net-action OOS. P4 venue proxy
не становится executing CFD book. P5 реальные executing quotes/carry/units
остаются необходимыми. P6 требует mature native expiry и physical OOS identity.
Новые sources/models/budgets и ослабленные gates не добавляются.

Существующий edge-family-training разрешает raw frozen archive/export только
для private repository. Репозиторий сейчас public; этот guard сохраняется.
Следующая инфраструктурная часть P2 должна опираться на уже разрешённое private
хранилище, сохранять source clocks/units и публиковать только aggregate diagnostics;
никакая private raw выгрузка в public GitHub artifact здесь не разрешена.
Оптимальные веса и прибыльность не установлены; сбор новых независимых данных
продолжается по действующему контракту, неизменённый поиск не повторяется вручную.
