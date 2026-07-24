#!/usr/bin/env python3
"""Получение рыночных данных по опционам США через неофициальную библиотеку webull
(tedchou12/webull) — эмуляция мобильного приложения, без официальных API-ключей.

ГЛАВНОЕ: SMS-код (MFA) вводится РОВНО ОДИН РАЗ. После первого входа токены сессии
сохраняются в webull_credentials.json (рядом со скриптом), и все последующие запуски
идут по сохранённым токенам через refresh_login() — БЕЗ SMS. Актуально при
одноразовом виртуальном номере.

────────────────────────────────────────────────────────────────────────────
УСТАНОВКА (важно — обычный `pip install webull` падает на новом setuptools):
    pip install "setuptools<66" wheel
    pip install webull
────────────────────────────────────────────────────────────────────────────
ЗАПОЛНИТЕ ниже: COUNTRY_CODE, PHONE_NUMBER, PASSWORD.
Затем: python tools/webull_quotes.py
Первый запуск попросит SMS-код; дальше — молча по токенам.

БЕЗОПАСНОСТЬ: webull_credentials.json и did.bin содержат вашу сессию — они в
.gitignore, НЕ коммитьте их. Пароль от брокера держите в тайне (лучше — задать
через переменную окружения WEBULL_PASSWORD, а не в файле).
"""
from __future__ import annotations

import json
import os
import sys

# ── КОНФИГ ─────────────────────────────────────────────────────────────────
# Можно задать через переменные окружения (быстро, без правки файла):
#   WEBULL_COUNTRY, WEBULL_PHONE, WEBULL_PASSWORD, WEBULL_REGION, WEBULL_TICKER
COUNTRY_CODE = os.environ.get("WEBULL_COUNTRY", "+66")   # код страны (пример: +66 — Таиланд)
PHONE_NUMBER = os.environ.get("WEBULL_PHONE", "YOUR_PHONE_NUMBER")  # БЕЗ кода страны (и обычно без ведущего 0)
PASSWORD = os.environ.get("WEBULL_PASSWORD", "YOUR_PASSWORD")
REGION_CODE = int(os.environ.get("WEBULL_REGION", "6"))  # regionId (6 по умолч.; смените, если вход не проходит)
DEVICE_NAME = "seiltanzer-quotes"

TICKER = os.environ.get("WEBULL_TICKER", "SPY")          # тикер для цепочки опционов

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = os.path.join(SCRIPT_DIR, "webull_credentials.json")

# полный логин webull для телефона: "+CC-NUMBER"  (напр. US "+1-2223334444")
USERNAME = f"{COUNTRY_CODE}-{PHONE_NUMBER}"


def _import_webull():
    try:
        from webull import webull  # noqa: WPS433
        return webull
    except ImportError:
        sys.exit("Библиотека не установлена. Выполните:\n"
                 "    pip install \"setuptools<66\" wheel\n"
                 "    pip install webull")


# ── работа с сохранённой сессией ────────────────────────────────────────────

def save_session(wb) -> None:
    """Сохранить текущие токены сессии в JSON (после login или refresh_login)."""
    data = {
        "accessToken": wb._access_token,
        "refreshToken": wb._refresh_token,
        "tokenExpireTime": wb._token_expire,
        "uuid": wb._uuid,
    }
    with open(CRED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[✓] токены сессии сохранены -> {CRED_FILE}")


def load_and_refresh(wb) -> None:
    """Загрузить токены из файла и обновить сессию БЕЗ SMS (refresh_login).

    Бросает RuntimeError, если refresh-токен протух (тогда нужен повторный SMS-вход).
    """
    with open(CRED_FILE, encoding="utf-8") as f:
        tok = json.load(f)
    wb._access_token = tok.get("accessToken", "")
    wb._refresh_token = tok.get("refreshToken", "")
    wb._token_expire = tok.get("tokenExpireTime", "")
    wb._uuid = tok.get("uuid", "")
    if not wb._refresh_token:
        raise RuntimeError("в файле нет refreshToken")
    result = wb.refresh_login()          # продлевает access-токен по refresh-токену
    if not result.get("accessToken"):
        raise RuntimeError(f"refresh_login не удался (токен протух?): {result}")
    save_session(wb)                     # refresh_login обновил wb._*, пересохраняем
    print("[✓] вход по сохранённым токенам (без SMS)")


def first_login_with_mfa(wb) -> None:
    """Первый вход: отправить SMS, ввести код, залогиниться, сохранить токены."""
    print(f"[i] первый вход для {USERNAME}: отправляю SMS-код…")
    if not wb.get_mfa(USERNAME):
        sys.exit("[✗] не удалось отправить SMS. Проверьте номер, COUNTRY_CODE и "
                 "REGION_CODE.")
    code = input("Введите СМС-код (MFA): ").strip()

    result = wb.login(username=USERNAME, password=PASSWORD,
                      device_name=DEVICE_NAME, mfa=code)
    if "accessToken" not in result:
        # частый случай: у аккаунта включён вопрос безопасности —
        # тогда нужны wb.get_security(USERNAME)/wb.next_security(USERNAME) и
        # передать question_id/question_answer в login().
        sys.exit(f"[✗] логин не удался: {result}")
    save_session(wb)
    print("[✓] первичный вход выполнен")


# ── опционы ─────────────────────────────────────────────────────────────────

def _num(entry: dict, *keys):
    """Достать первое непустое поле из контракта (схема webull плавает)."""
    for k in keys:
        v = entry.get(k)
        if v not in (None, "", "-"):
            return v
    return None


def fetch_and_print_options(wb, ticker: str) -> None:
    dates = wb.get_options_expiration_dates(stock=ticker)
    if not dates:
        sys.exit(f"[✗] нет дат экспирации для {ticker} (сессия валидна?)")
    expire = None
    for d in dates:
        if isinstance(d, dict) and d.get("days", 0) and d["days"] > 0:
            expire = d.get("date")
            break
    expire = expire or (dates[0].get("date") if isinstance(dates[0], dict) else None)
    print(f"[i] ближайшая экспирация {ticker}: {expire}")

    chain = wb.get_options(stock=ticker, expireDate=expire, direction="all")
    if not chain:
        sys.exit("[✗] пустая цепочка опционов")

    # схема контракта плавает между версиями — печатаем ключи одного контракта,
    # чтобы было видно, какие поля доступны (пригодится для интеграции в сервис)
    sample = (chain[len(chain) // 2].get("call")
              or chain[len(chain) // 2].get("put") or {})
    print(f"[i] поля контракта: {sorted(sample.keys())}\n")

    hdr = f"{'STRIKE':>9} | {'CALL bid/ask':>16} {'oi':>7} {'iv':>7} | " \
          f"{'PUT bid/ask':>16} {'oi':>7} {'iv':>7}"
    print(hdr); print("-" * len(hdr))
    for row in chain:
        s = row.get("strikePrice")
        c = row.get("call") or {}
        p = row.get("put") or {}

        def fmt(o):
            bid = _num(o, "bid", "bidPrice")
            ask = _num(o, "ask", "askPrice")
            oi = _num(o, "openInterest", "open_interest")
            iv = _num(o, "impVol", "impliedVol", "impliedVolatility")
            ba = f"{bid}/{ask}" if bid or ask else "—"
            return f"{ba:>16} {str(oi or '—'):>7} {str(iv or '—'):>7}"

        print(f"{s:>9} | {fmt(c)} | {fmt(p)}")
    print(f"\n[✓] получено {len(chain)} страйков по {ticker} @ {expire}")


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    os.chdir(SCRIPT_DIR)             # did.bin/credentials кладём рядом со скриптом
    webull = _import_webull()
    wb = webull(region_code=REGION_CODE)

    if os.path.exists(CRED_FILE):
        try:
            load_and_refresh(wb)
        except Exception as exc:     # noqa: BLE001 — протухшие токены/битый файл
            print(f"[!] сохранённая сессия недействительна ({exc}) — нужен SMS-вход")
            first_login_with_mfa(wb)
    else:
        print("[i] файла сессии нет — первый запуск")
        first_login_with_mfa(wb)

    fetch_and_print_options(wb, TICKER)


if __name__ == "__main__":
    main()
