# GitHub Actions Self-Hosted Runner & Production Deploy Guide

Руководство по обслуживанию self-hosted runner'а GitHub Actions и инфраструктуры автоматического production-деплоя терминала Seiltanzer.

---

## 📌 Основная информация

| Параметр | Значение |
| :--- | :--- |
| **Production Server** | `94.241.171.182` |
| **Репозиторий** | `Seiltanzer03/trading_dbd` |
| **Каталог Runner'а** | `/opt/actions-runner` |
| **Системный пользователь** | `github-runner` (без прямого доступ к sudo) |
| **Имя Runner'а** | `seiltanzer-prod-1` |
| **Labels** | `self-hosted`, `linux`, `x64`, `seiltanzer-prod` |
| **Systemd Service Runner'а** | `actions.runner.Seiltanzer03-trading_dbd.seiltanzer-prod-1.service` |
| **Deploy-скрипт** | `/usr/local/sbin/deploy-seiltanzer` (владелец: `root:root`, права `0755`) |
| **Права Sudo** | `/etc/sudoers.d/seiltanzer-runner` (`github-runner ALL=(root) NOPASSWD: /usr/local/sbin/deploy-seiltanzer`) |
| **Служба Приложения** | `seiltanzer` (`/etc/systemd/system/seiltanzer.service`) |
| **Локальный API** | `http://127.0.0.1:8790/api/state` |

---

## ⚙️ Проверка состояния Runner'а

### 1. Проверка через systemctl
```bash
systemctl status actions.runner.Seiltanzer03-trading_dbd.seiltanzer-prod-1.service
```

### 2. Проверка через скрипт `svc.sh`
```bash
cd /opt/actions-runner && ./svc.sh status
```

### 3. Проверка через GitHub API / UI
В интерфейсе GitHub: **Settings -> Actions -> Runners**. Имя `seiltanzer-prod-1` должно отображаться со статусом **Idle** (Online).

---

## 🔄 Перезапуск Runner'а

```bash
sudo systemctl restart actions.runner.Seiltanzer03-trading_dbd.seiltanzer-prod-1.service
```
Или через сервис-скрипт:
```bash
cd /opt/actions-runner && ./svc.sh restart
```

---

## 🔑 Замена регистрационного токена (Re-registration)

Если токен отзывается или нужно перерегистрировать runner:

1. Получите новый токен в GitHub UI (**Settings -> Actions -> Runners -> New self-hosted runner**) или через GitHub API:
   ```bash
   curl -X POST -H "Authorization: Bearer <GITHUB_PAT>" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/Seiltanzer03/trading_dbd/actions/runners/registration-token
   ```
2. Остановите службу runner'а:
   ```bash
   cd /opt/actions-runner && sudo ./svc.sh stop
   ```
3. Выполните перерегистрацию от имени пользователя `github-runner`:
   ```bash
   sudo -u github-runner /opt/actions-runner/config.sh \
     --url https://github.com/Seiltanzer03/trading_dbd \
     --token <NEW_REGISTRATION_TOKEN> \
     --name seiltanzer-prod-1 \
     --labels seiltanzer-prod \
     --unattended \
     --replace
   ```
4. Запустите службу runner'а:
   ```bash
   cd /opt/actions-runner && sudo ./svc.sh start
   ```

---

## 🗑 Удаление Runner'а (Remove Runner)

1. Получите токен удаления через GitHub API / UI:
   ```bash
   curl -X POST -H "Authorization: Bearer <GITHUB_PAT>" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/Seiltanzer03/trading_dbd/actions/runners/remove-token
   ```
2. Удалите systemd-сервис:
   ```bash
   cd /opt/actions-runner && sudo ./svc.sh uninstall
   ```
3. Удалите конфигурацию runner'а:
   ```bash
   sudo -u github-runner /opt/actions-runner/config.sh remove --token <REMOVE_TOKEN>
   ```

---

## 🚀 Ручной запуск Production Deploy

Чтобы вручную выполнить деплой актуальной версии `main` на сервере:

```bash
sudo /usr/local/sbin/deploy-seiltanzer
```

Скрипт автоматически:
1. Захватывает эксклюзивную блокировку `flock` (предотвращает параллельный деплой).
2. Выполняет `git fetch origin main` и `git reset --hard origin/main` в `/opt/seiltanzer`.
3. Устанавливает зависимости через `/opt/seiltanzer/.venv/bin/pip install --quiet -e /opt/seiltanzer`.
4. Аккуратно обновляет `/etc/seiltanzer.env` без затирания существующих переменных.
5. Перезапускает systemd-сервис `seiltanzer`.
6. Проверяет активность службы (`systemctl is-active seiltanzer`) и HTTP 200 на `http://127.0.0.1:8790/api/state`.

---

## 📄 Просмотр логов приложения Seiltanzer

Смотреть логи приложения в реальном времени (tail):
```bash
journalctl -u seiltanzer -f
```

Смотреть последние 100 строк логов:
```bash
journalctl -u seiltanzer -n 100 --no-pager
```

Смотреть логи службы GitHub Actions runner'а:
```bash
journalctl -u actions.runner.Seiltanzer03-trading_dbd.seiltanzer-prod-1.service -f
```
