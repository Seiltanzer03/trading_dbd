# Seiltanzer Terminal — Деплой на VPS ✅

## Рабочий URL

### 🌐 **http://94.241.171.182:8790**

---

## Статус

| Компонент | Результат |
|---|---|
| **SSH подключение** | ✅ порт 22 открыт |
| **Сервис systemd** | ✅ `active (running)`, enabled (автозапуск) |
| **API /api/state** | ✅ HTTP 200, живые данные |
| **Веб-страница** | ✅ HTTP 200, заголовок «SEILTANZER TERMINAL» |
| **Swap 1G** | ✅ создан, работает |
| **UFW** | Неактивен — порт 8790 доступен без ограничений |
| **Хостинг фаервол** | ✅ порт 8790 НЕ в списке закрытых портов |
| **Режим** | `stream` — живой WS-стрим цены ^NDX |
| **Текущая цена** | NAS100: ~28454.81 (live) |
| **VIX** | 18.70 (live) |

## Что было сделано

1. ✅ Подключился к серверу `root@94.241.171.182` по SSH (через plink)
2. ✅ Запустил install.sh с `PORT=8790 MODE=stream`
3. ✅ Скрипт установил:
   - Python 3 + venv + зависимости (уже были)
   - Swap 1G (для защиты от OOM)
   - Клонировал репозиторий в `/opt/seiltanzer`
   - Создал venv и установил зависимости
   - Настроил systemd-сервис с автозапуском и рестартом
4. ✅ Проверил работоспособность — все 3 проверки пройдены
5. ✅ Клонировал актуальную версию с GitHub в локальную папку `C:\Users\Huawei\Desktop\Claude trading_dbd`

## По поводу 1.67 ГБ кеша

> **Не помешает!** Это нормальный `buff/cache` Linux — он автоматически освобождается, когда приложению нужна память. Диск: 29 ГБ всего, 11 ГБ использовано, **18 ГБ свободно** — места более чем достаточно.

## Команды управления

```bash
# Статус сервиса
systemctl status seiltanzer --no-pager

# Логи (последние 50 строк)
journalctl -u seiltanzer -n 50 --no-pager

# Логи в реальном времени
journalctl -u seiltanzer -f

# Перезапуск
systemctl restart seiltanzer

# Остановка
systemctl stop seiltanzer

# Обновить код с GitHub и перезапустить
cd /opt/seiltanzer && git fetch origin main && git reset --hard origin/main && systemctl restart seiltanzer
```

## О режиме «нет данных»

> [!NOTE]
> Если live-режим показывает «нет данных» — это **нормально** вне часов торговой сессии US (NYSE/NASDAQ: 09:30–16:00 ET, ~16:30–23:00 МСК). В нерабочие часы стрим-источник не транслирует котировки. Сейчас (ночь по ET) данные могут идти с задержкой или отсутствовать — при открытии US-сессии всё заработает в полном объёме.

## Последний коммит на сервере

```
2fb14e5 feat(cone): настоящий 3D (WebGL/Plotly) вместо 2.5D-изометрии
```

## Systemd unit-файл

```ini
[Unit]
Description=Seiltanzer Terminal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/seiltanzer
ExecStart=/opt/seiltanzer/.venv/bin/python -m seiltanzer --host 0.0.0.0 --port 8790 --data-dir /opt/seiltanzer/data --stream
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
