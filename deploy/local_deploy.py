#!/usr/bin/env python3
"""Локальный деплой Seiltanzer НА СЕРВЕР ПРЯМО ИЗ ПАПКИ — без GitHub.

Для агента/машины, у которой есть только локальная папка проекта и сетевой
доступ к серверу (порт 22). Заливает текущий репозиторий на сервер по SSH/SFTP
(только изменённые по размеру файлы), затем рестартит сервис. GitHub не нужен.

Настройка ОДИН РАЗ — создайте файл `deploy/server.env` рядом с этим скриптом
(он в .gitignore, в репозиторий не попадёт):

    SERVER_HOST=94.241.171.182
    SERVER_USER=root
    SERVER_PASS=пароль_сервера
    APP_DIR=/opt/seiltanzer          # необязательно, это значение по умолчанию

Запуск из любой папки:

    pip install paramiko
    python deploy/local_deploy.py

Что делает: SFTP-заливает код в APP_DIR (пропуская .git/.venv/data/кэш),
`pip install -e .` (на случай новых зависимостей), `systemctl restart seiltanzer`,
проверяет, что сервис активен и отвечает по HTTP. Идемпотентно, ничего не ломает:
папка `data/` (ваши сделки/кэш) НЕ трогается.
"""
from __future__ import annotations

import os
import posixpath
import sys
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit("Нужен paramiko:  pip install paramiko")

ROOT = Path(__file__).resolve().parent.parent          # корень репозитория
EXCLUDE_DIRS = {".git", ".venv", "data", "__pycache__", ".pytest_cache",
                "node_modules", "dist"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}
EXCLUDE_SUBSTR = ("egg-info",)


def load_cfg() -> dict:
    cfg = {}
    envf = ROOT / "deploy" / "server.env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    for k in ("SERVER_HOST", "SERVER_USER", "SERVER_PASS", "APP_DIR"):
        if os.environ.get(k):                          # env важнее файла
            cfg[k] = os.environ[k]
    return cfg


def iter_files(root: Path):
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        if set(rel.parts) & EXCLUDE_DIRS:
            continue
        if p.suffix in EXCLUDE_SUFFIX:
            continue
        if any(s in rel.as_posix() for s in EXCLUDE_SUBSTR):
            continue
        yield rel


def ensure_remote_dir(sftp, path: str, made: set):
    parts, cur = path.split("/"), ""
    for part in parts:
        cur = (cur + "/" + part) if cur else part
        if not part or cur in made:
            continue
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)
        made.add(cur)


def main() -> None:
    cfg = load_cfg()
    host = cfg.get("SERVER_HOST")
    user = cfg.get("SERVER_USER", "root")
    pw = cfg.get("SERVER_PASS")
    app_dir = cfg.get("APP_DIR", "/opt/seiltanzer")
    if not host or not pw:
        sys.exit("Заполните SERVER_HOST и SERVER_PASS в deploy/server.env (или env).")

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Подключаюсь к {user}@{host} …")
    cli.connect(hostname=host, port=22, username=user, password=pw,
                look_for_keys=False, allow_agent=False, timeout=30)
    sftp = cli.open_sftp()

    files = list(iter_files(ROOT))
    made: set = set()
    sent = skipped = 0
    for rel in files:
        remote = posixpath.join(app_dir, rel.as_posix())
        ensure_remote_dir(sftp, posixpath.dirname(remote), made)
        local_size = (ROOT / rel).stat().st_size
        try:                                            # пропускаем неизменённые
            if sftp.stat(remote).st_size == local_size:
                skipped += 1
                continue
        except FileNotFoundError:
            pass
        sftp.put(str(ROOT / rel), remote)
        sent += 1
    sftp.close()
    print(f"Залито: {sent} файлов (пропущено без изменений: {skipped}).")

    print("Рестарт сервиса …")
    cmd = (
        f"cd {app_dir} && "
        f"({app_dir}/.venv/bin/pip install -q -e . >/dev/null 2>&1 || true); "
        f"systemctl restart seiltanzer && sleep 3 && "
        f"echo active=$(systemctl is-active seiltanzer) && "
        f"echo http=$(curl -s -o /dev/null -w '%{{http_code}}' "
        f"http://127.0.0.1:8790/api/state)"
    )
    _in, out, err = cli.exec_command(cmd, timeout=300, get_pty=True)
    print(out.read().decode(errors="replace").strip())
    e = err.read().decode(errors="replace").strip()
    if e:
        print("stderr:", e)
    cli.close()
    print("Готово.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"ОШИБКА ДЕПЛОЯ: {type(exc).__name__}: {exc}")
