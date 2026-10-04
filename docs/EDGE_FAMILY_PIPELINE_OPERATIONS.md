# Семейные модели: эксплуатация и границы готовности

Источник требований — исходное ТЗ `TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`.
Очередь и evidence — `superpowers/plans/2026-10-02-unified-edge-remainder.md`.
Этот документ описывает технический путь, не доказанную эффективность.

## Off-host запуск

На доверенном runner с зависимостями проекта:

```bash
python -m scripts.run_edge_family_pipeline \
  --reviews /secure/input/unified_actual_reviews.json \
  --archive /secure/input/archive.json \
  --expected-sha EXACT_40_HEX_DEPLOYMENT_SHA \
  --output-dir /secure/output/family-pipeline
```

`--archive` опускается при первом запуске. Указанные secure paths и SHA — пример,
не существующий источник данных. Reviews создаются существующим read-only
exporter; архив дополняется только совместимыми реальными наблюдениями.

Результат: archive, dataset и diagnostics. Runtime context создаётся только
если хотя бы одна модель действительно прошла admission; при отсутствии
данных он не создаётся, а прежний output в той же директории удаляется.
Недостаточная выборка, отсутствие причинных features, неизвестные costs или
geometry mismatch — отказ с причиной, не разрешение ослабить критерии.

Данные snapshots/identity/units и training records содержат приватную информацию.
Не коммитить их в публичный Git и не загружать в публичные workflow artifacts.
`edge-family-training` проверяет CI-контракты без credentials. Реальный workflow
с restore предыдущего archive допускается только для private repository;
public repository сообщает `PRIVATE_DATA_STORAGE_REQUIRED` без SSH-export.
Это открытая эксплуатационная зависимость, не действующее закрытое хранилище.
Новый warehouse, bucket или доступ данным этим изменением не создаётся.

Workflow artifact retention — 14 дней; это не постоянный архив. Для следующего
запуска указывать точные `archive_run_id` и `archive_artifact_name` до истечения
retention либо использовать существующий доверенный private off-host архив.

## Runtime и данные исполняющей позиции

Существующий runtime consumer читает pinned local context, проверяет hash,
deployment SHA, причинные clocks и каждую модель. Он не обучается и не вызывает
provider. Forecast для другого instrument/horizon/geometry не получает голос.
Наличие artifact само по себе не меняет риск-ограничения, веса или ордера.

Независимое executing-position evidence подключается через:

- `SEILTANZER_POSITION_EXECUTION_CONTEXT_PATH`
- `SEILTANZER_POSITION_EXECUTION_CONTEXT_SHA256`

Только реальный bounded документ `broker-position-context-v1`: source provenance,
отдельный pin, broker/account/position-to-local-trade binding, реальные единицы,
currency/risk per unit, точные entry/original stop/remaining fraction и причинные
observed/received clocks. Максимальный возраст — 60 секунд. Изменение позиции
делает старый документ неприменимым; quantity автоматически не масштабируется.
Плановый risk percent и public venue quote не заменяют эти данные.

Отдельный execution-cost context требует собственный pin и все компоненты
immediate/deferred costs. Он не назначает свою broker identity. Некорректный
выбранный position context запрещает cost admission даже если старые snapshot
поля совпадали. ACK — receipt пользователя, не broker fill.

## Что остаётся внешним входом или дальнейшей реализацией

- Настоящий read-only source позиции и издержек executing account; файл-пример
  не активируется и не выдаётся за подключение.
- Достаточные независимые причинные наблюдения и full-cost labels; небольшой
  исторический cohort не становится `OOS_VALIDATED` от успешного CI.
- Законные consensus vintages, earnings expectations, fund flows, forwards,
  применимые CFD calendars/book/mappings и physical/VRP evidence.
- Forecasts для иных geometry cohorts, где проверенного conditional context
  ещё нет; unsupported actions остаются явно unavailable.
- Закрытая эксплуатационная среда накопления данных и утверждённая публикация
  реальных моделей. Автоматическая production activation не включена.

Подробная матрица источников — `EDGE_FAMILY_INPUT_REQUIREMENTS.md`. Полный
остаток исходного ТЗ не заменяется фактом готовности этих механизмов.
