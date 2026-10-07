# P1: независимый FOMC scheduler

## Состояние на 2026-10-07

Подготовлен отдельный systemd oneshot + timer для существующего bounded
collector, с доставкой в production через dedicated forced-command SSH key.
Это транспорт и конфигурация, не подтверждение установки или cadence.
Production baseline: `79a555748c61ea2bdab1ffd468f0d083344c5674`.

Пользователь предложил запасной host `212.193.24.125`, на котором уже есть
другой проект. Единственная попытка SSH port 22 из рабочего окружения завершилась
`Network is unreachable` до аутентификации. Пароль не использован и не сохранён.
Инвентаризация ресурсов, создание пользователя/ключа, установка и запуск
**не выполнены**. P1 остаётся открытым до реальных доставок и квитанций.

## Контракт и границы

- Collector сохраняет существующие 4 HTTP requests / 60 seconds / bounded body
  limits. Не переносит research, production DB или trading на запасной host.
- Scheduler запускается пользователем `seiltanzer-fomc` в отдельном
  `/opt/seiltanzer-fomc`; state — `/var/lib/seiltanzer-fomc`. Unit ограничивает
  CPU до 25%, memory до 512M, длительность до 190 seconds. Имена не совпадают
  с чужими service/директориями. Никаких новых listening ports.
- Таймер задаёт UTC target каждые 600 seconds, AccuracySec=1s, без backfill.
  Конфигурация не гарантирует фактическое расписание; задержки учитываются
  по реальным `received_ts`, а не по времени таймера.
- На production ключ допускает только команду
  `fomc-publish-v1 <40-char-sha> <bounded-run-id>`. Нет shell, SFTP, forwarding,
  PTY или управляемого клиентом destination. stdin ограничен 950000 bytes и
  20 seconds. Receiver пишет только `data/research/fomc_prospective_latest.json`
  через atomic replace после проверки exact SHA, HTTP health и всего capture.
- GitHub FOMC publisher использует тот же receiver. Общий flock и строго
  возрастающий `captured_ts` в рамках SHA запрещают задержавшемуся capture
  перезаписать более новый capture или известный отказ; equal-clock replay
  также отказывается. Новый принятый SHA начинает свою последовательность.
- Свежий `UNAVAILABLE` marker также доставляется, чтобы известный отказ источника
  не сохранял прежнюю admission. Builder failure без нового файла не приводит
  к отправке старого. TTL остаётся 3600 seconds; cadence target остаётся 600.
- Sender принимает только pinned SSH host key (`StrictHostKeyChecking=yes`),
  отдельный delivery key и ответ с точным SHA, run id и hash отправленных bytes.
  Root passwords и GitHub credentials на spare не требуются.
- Квитанция подтверждает запись файла (`materialization_observed=false`), а не
  native DB ingest, model validation, authority или полезность прогноза.
  Native materialization проверяется отдельно существующим status contract.

## Активация после восстановления доступа

1. Read-only inventory spare: RAM/disk/load, active services, Python >=3.11,
   synchronized UTC clock, outbound Federal Reserve и production SSH. Сохранить
   чужой проект, его users, ports, directories и services без изменений.
2. Создать отдельного system user `seiltanzer-fomc` и root-owned checkout
   `/opt/seiltanzer-fomc` на **принятом production SHA**, с отдельной venv
   (`pip install -e .`). Использовать этот документ из уже принятого release,
   а не захардкоженный baseline выше. Код не изменяется самим service user.
3. Создать отдельный Ed25519 delivery key. На spare каталог
   `/etc/seiltanzer-fomc` root-owned, group `seiltanzer-fomc`, mode 0750;
   private key root-owned, group `seiltanzer-fomc`, mode 0640. Не сохранять key
   в Git, пользовательских документах или logs. Подтвердить production SSH
   fingerprint через уже доверенный канал; сохранить pinned known_hosts.
4. На production добавить **только новый** public key в authorized_keys с
   ограничениями, не меняя существующие записи:

   ```text
   from="212.193.24.125",restrict,command="cd /opt/seiltanzer && exec /opt/seiltanzer/.venv/bin/python scripts/fomc_scheduler_transport.py receive" ssh-ed25519 <DEDICATED_PUBLIC_KEY>
   ```

   Проверить root ownership checkout и запрет user environment в sshd.
   Shell `id`, SFTP и другой remote name должны отказать на новом ключе.
5. Создать `/etc/seiltanzer-fomc/scheduler.env` без секретов:

   ```text
   EXPECTED_SHA=<ACCEPTED_PRODUCTION_SHA>
   PRODUCTION_HOST=94.241.171.182
   ```

   Установить только `deploy/seiltanzer-fomc-spare.service` и `.timer` в
   `/etc/systemd/system`. Проверить `systemd-analyze verify`, одну bounded
   service delivery и native materialization; затем enable/start только этот
   timer. `OnCalendar` интерпретируется в host timezone; inventory должен
   подтвердить UTC, не менять timezone чужого host ради установки.
6. Снять последовательные реальные квитанции через journal **этого** service:
   `journalctl -u seiltanzer-fomc-spare.service -o cat`. Сопоставить SHA,
   capture_ts, received_ts, hash и native ingest. Зафиксировать фактические
   intervals, failures и gaps; не выводить 600-second guarantee из unit-файла.

После каждого production release checkout и `EXPECTED_SHA` на spare должны
обновляться на принятую generation. Пока это не сделано, delivery отказывает
при SHA mismatch; автоматическое обновление кода данным service не разрешено.
GitHub capture workflow остаётся существующим release/fallback путём и не
доказывает регулярность независимого timer.

Откат изолирован: disable/stop **только** `seiltanzer-fomc-spare.timer`, удалить
только dedicated public key с production. Чужой проект не останавливается.

## Проверки подготовленного изменения

Transport tests проверяют rejection shell/path injection до чтения stdin,
SHA/authority/envelope/body/stale rejection с сохранением прежних bytes,
health refusal, generation change во время приёма, input bounds, свежий отказ
источника, pinned-key SSH invocation и receipt hash mismatch.
Они используют искусственные данные и не являются production cadence receipts.
Профильный набор transport + existing capture + exact-SHA publication:
**37 passed** (включая release workflow contract). Локальный systemd verify прочитал units, но отсутствующий
`/opt/seiltanzer-fomc/.venv/bin/python` не позволяет заявить host installation.
