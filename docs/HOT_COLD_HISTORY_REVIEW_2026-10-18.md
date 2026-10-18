# Аудит Hot/Cold и исправленный порядок продолжения — 18.10.2026

Статус: **исходная ветка не готова к production cutover**. Пользователь поручил
проверить работу другого агента и продолжать исходную разработку. Это дополнение
к исходному ТЗ, не его замена. Uploaded handoff-файлы не изменялись.

## Проверенная база и полезный результат

- Main при проверке: `5fe2e56d6a5d1f8da20c9a918fe3592d3e6eb5d4`.
- Проверена внешняя ветка `feat/hot-cold-cutover-and-fk-closure`, HEAD
  `58d6dc9e5c753059992084bce5b64cd21692232f`, tree
  `e2afea36a62ea88a7b5c0799120ab9de3e714768`. Локальная копия до исправлений
  имеет ровно этот tree; исходная ветка другого агента не переписывается.
- [Stage 1](https://github.com/Seiltanzer03/trading_dbd/actions/runs/39061768722):
  проверены 12 FK-таблиц; 11 чистые, два отсутствующих родителя только у ACK.
  Реальные оба ACK имеют статус `executed`; статусы и времена в двух handoff
  противоречат фактическому логу. Источник для миграции — настоящие строки,
  не пересказ handoff.
- [Stage 2](https://github.com/Seiltanzer03/trading_dbd/actions/runs/39079437415),
  job `117533194065`, SUCCESS, checked-out code `58d6dc9e`:
  источник `18,784,505,856` B → hot `14,978,297,856` B;
  удалено из копии `62,183` строки; экономия `3,806,208,000` B / `3.545 GiB`.
  Сборка `608.62` s, гидратация `985.53` s. Все 439 частей восстановлены,
  исходные selected roots подтверждены, FK после миграции чистые;
  `MAX(rowid)=132923`, следующий тестовый rowid `132924`.
  `production_connection=false`: на VPS место этим заданием не освобождено.
- Сохранение явных rowid при пересборке — правильное направление. Обычный
  VACUUM не является допустимым доказательством сохранения скрытых rowid.
- Lifecycle identities сохраняют подлинные ACK без фиктивных сделок. Но это
  **смена целевого FK**, а не восстановление потерянных исходных `trades`.

## Исправленные воспроизводимые дефекты

1. `build_hot_cutover_snapshot` принимал отсутствующие source proof и catalog
   pin; `verify_source_catalog_roots=False` позволял выдать `cutover_verified`
   без обязательной сверки корней. Теперь все три обхода запрещены.
2. При существующей таблице зависимостей с неизвестной колонкой функция молча
   пропускала защиту и могла вывести связанные наблюдения в cold. Теперь
   неизвестная схема именованной зависимости вызывает отказ без публикации.
3. ACK-trigger запрещал только `UPDATE OF trade_id`: status, policy, note,
   timestamp и decision_id исторического ACK можно было переписать без trade.
   Теперь проверяется любой UPDATE; старый trigger обновляется идемпотентно.
   Обычная работа с ACK существующей сделки сохраняется.
4. Receipt явно имеет scope `OFF_HOST_PHYSICAL_COMPACTION_ONLY`,
   `production_authority=false`, `data_retirement_authorized=false`,
   `full_production_reader_equivalence_proven=false`. Физическая проверка
   не должна автоматически давать разрешение на удаление live-истории.
5. В контракте исправлены неверные typed roots, завышенная экономия и заявление
   о точном порядке схемы. Историческая экономия не выдана за текущий VPS reserve.

RED: 9 отказавших защитных регрессий, затем отдельная регрессия неизвестной
схемы тоже отказала. GREEN: **108 passed, 1 existing warning, 2.48 s** в профиле
cutover/ACK/lifecycle/catalog/union/partition/selected restore. Тяжёлое
восстановление 19 GB и fit не повторялись. Полный обязательный CI нового
исправленного tree и заключительный независимый review ещё не выполнены.

## Оставшиеся блокеры, которые нельзя скрыть зелёным Stage 2

### 1. Изменение существующих hot-строк после эпохи каталога

Воспроизведено на реальном коде: после compaction сохранённая строка
`pending` становится `resolved`; новый корректный source SHA/manifest
построен; `hydrate_snapshot` отказывает `Conflicting hot/cold history rowid`.
Добавленная поддержка suffix `rowid > catalog_max` этого не решает.
Проверка безопасно не публикует результат и оставляет источник неизменным.

**Критерий следующего пакета:** версионированный контракт согласования
актуального hot-состояния и immutable cold. Сохраняются текущие исходы,
исторические raw inputs, исходные rowid и provenance; новые исходы не
объявляются старыми typed roots. На фикстурах отдельно проверить завершение
pending внутри старого префикса, изменение допустимых outcome-полей,
добавление suffix и отказ при изменении неизменяемых capture-полей/identity.
Нельзя решить конфликт отключением hash-проверок или перезаписью свежего
исхода старой cold-строкой. Только append новых rowid недостаточен.

### 2. Реальная интеграция полной истории и читателей

В `58d6dc9e` нет production-вызовов `hydrate_snapshot`, хранения catalog pin
в штатных backup manifests или реализованного `cutover_metadata`, заявленного
в handoff. Сейчас pin существует в возвращаемом off-host proof. После live
pruning штатный off-host EDE не должен ошибочно получить hot-only выборку.

Кроме того, список пяти исключений не доказывает все зависимости. Например,
`g1_dataset_runtime.g1_dataset_status` считает `raw_source_n/current_source_n`
непосредственно по hot-таблице; после её сокращения эти величины изменятся.
Существующий consumer inventory содержит 158 literal refs в 53 файлах и
прямо помечен как неполный. Отсутствие SQLite FK не доказывает отсутствие join.

**Критерий:** сохранить и проверить привязку каталога в backup/restore/export,
направить полные исследования на verified union, а runtime — на доказанные
bounded/materialized данные. Сравнить настоящие выборки/joins/cohorts/model
inputs и объяснимые UI счётчики до/после, включая raw/hash validation,
G1S source_rowid/watermark, dataset/cut membership и replay. Список counters,
которые показывают hot-only, должен иметь явную область; молчаливое изменение
исторических знаменателей неприемлемо. G.1 gates не ослабляются.

### 3. Безопасное физическое переключение

Прямая потоковая перезапись единственного файла базы не является atomic swap.
Обещание, что runner «сразу вернёт backup», не покрывает потерю runner/SSH,
переполнение диска, отмену job или запуск auto-recovery во время передачи.
Предложенный `trades.db.maintenance.lock` не захватывается существующими
production workflows. Фактическая общая очередь — `production-seiltanzer`;
отдельно есть `/opt/seiltanzer/.deployment-git-maintenance.lock`.

**Критерий:** проверенный протокол maintenance/recovery, согласованный со всеми
писателями/deploy/health recovery; durable pre/post identities и restore
receipt; проверка interruption на disposable файлах; ни один незавершённый
файл не открывается сервисом. До первого destructive write должны быть
подтверждены backup, восстановление, owner run и состояние всех писателей.
Учитывать новые записи и их сохранение при rollback после запуска.
Наличие облачного backup само по себе этот протокол не реализует.

## Каноническая инфраструктура и исходный scope

- VPS `94.241.171.182`, service `seiltanzer`, code `/opt/seiltanzer`,
  live DB `/opt/seiltanzer/data/trades.db`, HTTP `:8790`.
- Yandex endpoint `https://storage.yandexcloud.net`, bucket
  **`trading-dbd-backups-2026`**. Использовать существующие GitHub secrets:
  `SSH_PASSWORD`, `YC_STORAGE_ACCESS_KEY_ID`, `YC_STORAGE_SECRET_ACCESS_KEY`.
  Секреты/сырые торговые данные не печатать. Live SQLite не переносить на S3 mount.
- Другие IP, `/var/lib/trading-dbd/trading_data.db`, другие systemd units,
  новый bucket и оценки 38/150 GB в одном из handoff не подтверждены и не
  являются инструкциями для этой инфраструктуры.
- Исходные восемь семейств: `macro`, `event`, `order_flow`, `intermarket`,
  `positioning`, `value_carry`, `option`, `session`. G1S/G1M/EDE — не их замена.
- Не запускать отсутствующие workflow по придуманным именам и не запускать
  заново все fits без новых пригодных входов. Текущий LLM, математический
  head, Active Edge и исторические LLM имеют разные допуски и provenance.
- Reserve worker `1107296256` B и source snapshot `1073741824` B неизменны.
  Не освобождать место удалением ACK, не создавать фиктивные broker fills и
  не превращать proxy в direct production authority.

## Конечный порядок продолжения

1. Закрыть mutable-prefix/reconciliation и интеграцию catalog pin/полных
   читателей как один согласованный пакет с профильными тестами.
2. Доказать протокол cutover/recovery на disposable копии; не повторять уже
   пройденный unchanged 19 GB restore ради нового статуса.
3. Один независимый final review, обязательный Python/SQLite/WebKit CI на
   final tree. Только затем разрешённые merge и штатный exact-SHA deploy.
4. Выполнить доказанный cutover, измерить фактический устойчивый резерв,
   подтвердить новые rowid/исходы и штатные читатели. Не объявлять VPS ready
   по размеру исторической копии.
5. После реального headroom продолжить заблокированный EDE, доступные
   текущие прогнозы/вклады и оставшийся UI-first-build-error фикс, если дефект
   воспроизведён. Обновить восемь семейств по исходному ТЗ; нет входа — явный
   блокер, без бесконечного переобучения и искусственно ненулевых весов.

На этом аудите ни одна production строка, ACK или позиция не изменялась.
