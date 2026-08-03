"""Статическая конфигурация: сетапы, инструменты, лестница фиксации.

Источники:
- Встроенная таблица 16 сетапов — ТЗ (n, wins, RR) + названия из главы 10 стратегии.
- Матрица рисков и формула RR — Excel-калькулятор (лист1, колонки G/J) и глава 2.1.
- Лестница фиксации — глава 2.2.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------- сетапы

@dataclass(frozen=True)
class Setup:
    num: int
    name: str
    instrument: str
    n: int          # всего сделок в встроенной статистике
    wins: int       # прибыльных
    rr: float       # целевой RR (T в R-координатах)
    filters: tuple[str, ...] = ()   # коды фильтров волатильности

    @property
    def winrate(self) -> float:
        return self.wins / self.n if self.n else 0.0


# (n, wins, RR) — из ТЗ; названия/инструменты — глава 10 стратегии;
# фильтры: vix_gt_20 — сетапы 5, 6, 11; gvz_lt_18 — 11; dv1x_lt_19 — 7 (глава 2.7/сетапы).
SETUPS: dict[int, Setup] = {s.num: s for s in [
    Setup(1,  "AMD + FVG Sweep 8H",        "NAS100", 7, 6, 2.5),
    Setup(2,  "AMD 1H + Weekly FVG 0.786", "NAS100", 8, 7, 2.5),
    Setup(3,  "12H FVG + 4H bFVGc",        "NAS100", 22, 15, 2.5),
    Setup(4,  "SP500+NAS100 корреляция",   "SP500",  14, 10, 2.5),
    Setup(5,  "12H FVG ретест + VIX>20",   "SP500",  11, 8, 2.5, ("vix_gt_20",)),
    Setup(6,  "8H FVG + VIX>20",           "US30",   16, 14, 2.5, ("vix_gt_20",)),
    Setup(7,  "12H FVG sweep + 1H FVG",    "GER40",  22, 13, 2.5, ("dv1x_lt_19",)),
    Setup(8,  "12H + 90m FVG + 2H bFVGc",  "GER40",  23, 17, 2.5),
    Setup(9,  "12H FVG + 2H bFVGc",        "UK100",  16, 13, 2.5),
    Setup(10, "Daily FVG + 4H sweep",      "JPY100", 22, 13, 2.5),
    Setup(11, "4H VIX + GVZ корреляция",   "XAU",    9, 7, 2.7, ("vix_gt_20", "gvz_lt_18")),
    Setup(12, "12H FVG sweep + 15m",       "XAU",    14, 10, 2.5),
    Setup(13, "Daily FVG + AMD + Fib",     "XAG",    14, 12, 2.5),
    Setup(14, "Daily FVG + DXY (ЛОНГ)",    "EURUSD", 11, 8, 2.5),
    Setup(15, "Daily FVG + DXY (ШОРТ)",    "EURUSD", 7, 5, 2.5),
    Setup(16, "8H Block + 4H Conf",        "USDCAD", 17, 14, 2.5),
]}


# ------------------------------------------------------------ инструменты

@dataclass(frozen=True)
class Instrument:
    code: str
    yahoo: str                    # тикер цены в Yahoo Finance
    options_proxy: str | None     # тикер опционной цепочки (ETF-прокси) или None
    demo_price: float             # стартовая цена в демо-режиме
    demo_vol: float               # годовая вола в демо-режиме
    proxy_experimental: bool = False  # тонкий/неточный прокси — помечать в UI
    # Как переносить ДОХОДНОСТИ/страйки прокси в шкалу торгуемого инструмента:
    # direct:  +1% прокси ~= +1% инструмента;
    # inverse: +1% прокси ~= -1% инструмента (FXC -> USD/CAD).
    proxy_transform: str = "direct"
    # Короткая честная подпись реального ценового ряда. Код инструмента — это
    # пользовательская стратегия, price_label — то, что действительно пришло.
    price_label: str | None = None
    # Дополнительные тиковые драйверы только для переноса ДОХОДНОСТИ между
    # контрольными котировками основного ряда. Первый свежий имеет приоритет.
    # Опционный proxy остаётся отдельным полем и не подменяется этим списком.
    live_price_drivers: tuple[str, ...] = ()
    # Прямая OTC/spot-котировка Swissquote. Если задана, именно она является
    # отображаемой ценой; Yahoo-фьючерс используется только для истории.
    swissquote_pair: str | None = None


INSTRUMENTS: dict[str, Instrument] = {i.code: i for i in [
    Instrument("NAS100", "^NDX",     "QQQ", 21500.0, 0.22,
               price_label="NASDAQ-100 cash index"),
    Instrument("SP500",  "^GSPC",    "SPY", 6100.0,  0.17,
               price_label="S&P 500 cash index"),
    Instrument("US30",   "^DJI",     "DIA", 44500.0, 0.15,
               price_label="Dow Jones cash index"),
    # экспериментальные прокси — US-ETF на страну/валюту; трекинг неточный,
    # опционы тонкие; данные помечаются «низкая надёжность» в интерфейсе.
    Instrument("GER40",  "^GDAXI",   "EWG", 24300.0, 0.16, proxy_experimental=True,
               price_label="DAX cash index"),
    Instrument("UK100",  "^FTSE",    "EWU", 8900.0,  0.12, proxy_experimental=True,
               price_label="FTSE 100 cash index"),
    Instrument("JPY100", "JPY=X",    None,  148.0,   0.10,
               price_label="USD/JPY (название JPY100 условное)",
               swissquote_pair="USD/JPY"),
    # Отображаем spot OTC. Yahoo futures остаются только источником
    # истории/объёма и переводятся в текущую spot-шкалу.
    Instrument("XAU",    "GC=F",     "GLD", 3350.0,  0.16,
               price_label="XAU/USD spot (Swissquote OTC)",
               live_price_drivers=("GLD", "PAXG-USD"),
               swissquote_pair="XAU/USD"),
    Instrument("XAG",    "SI=F",     "SLV", 38.0,    0.28,
               price_label="XAG/USD spot (Swissquote OTC)",
               swissquote_pair="XAG/USD"),
    Instrument("EURUSD", "EURUSD=X", "FXE", 1.17,    0.07, proxy_experimental=True,
               price_label="EUR/USD", swissquote_pair="EUR/USD"),
    # FXC — CAD/USD-подобный ETF: его доходности противоположны USD/CAD.
    Instrument("USDCAD", "CAD=X",    "FXC", 1.37,    0.06, proxy_experimental=True,
               proxy_transform="inverse", price_label="USD/CAD",
               swissquote_pair="USD/CAD"),
]}

# индексы волатильности (Yahoo). Первые три — ворота фильтров стратегии;
# evz/vxn — источник implied-волы для σ-поправки там, где полной цепочки нет.
# ^V1X (VDAX-NEW) в Yahoo обычно недоступен — фид честно вернёт "no_data",
# UI покажет «проверь вручную» (ТЗ, п.7 ядра).
VOL_INDEX_TICKERS = {
    "vix": "^VIX", "gvz": "^GVZ", "dv1x": "^V1X",
    "evz": "^EVZ",   # CBOE EuroCurrency Volatility Index — implied-вола EUR/USD
    "vxn": "^VXN",   # CBOE NASDAQ-100 Volatility Index
}

# Инструмент -> ключ индекса волы как ИСТОЧНИК sigma_implied, когда полной
# опционной цепочки нет. Значение индекса — годовая implied-вола в % (÷100).
# Даёт σ-поправку без цепочки (но без Strike Landscape / GEX). Честный список:
# только там, где есть бесплатный профильный индекс волы.
SIGMA_INDEX_FOR = {"EURUSD": "evz"}


# ----------------------------------------------------- лестница фиксации

# Глава 2.2: по 10% позиции на каждом рубеже; после 1.5R стоп в безубыток.
LADDER_RUNGS: tuple[float, ...] = (1.0, 1.25, 1.5, 1.75, 2.0, 2.2)
LADDER_FRACTION = 0.10
BREAKEVEN_AFTER = 1.5


# ------------------------------------------------------------- настройки

@dataclass
class Settings:
    demo: bool = False
    stream: bool = False        # бесплатный WebSocket-стрим цены (Yahoo), опция
    host: str = "127.0.0.1"
    port: int = 8790
    data_dir: str = field(default_factory=lambda: os.environ.get("SEILTANZER_DATA_DIR", "."))
    price_poll_sec: float = 4.0     # ТЗ: 3–5 сек
    chain_poll_sec: float = 600.0   # ТЗ: 5–10 мин
    proxy_poll_sec: float = 60.0    # REST-прокси; при --stream обновляется тиково
    vol_poll_sec: float = 60.0
    journal_min_trades: int = 20    # порог перекалибровки на журнал (ТЗ, п.2 ядра)

    @property
    def trades_db(self) -> str:
        return os.path.join(self.data_dir, "trades.db")

    @property
    def cache_db(self) -> str:
        return os.path.join(self.data_dir, "cache.db")


def settings_from_env() -> Settings:
    return Settings(
        demo=os.environ.get("SEILTANZER_DEMO", "") == "1",
        host=os.environ.get("SEILTANZER_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEILTANZER_PORT", "8790")),
    )
