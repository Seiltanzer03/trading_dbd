"""Самопроверка боевых данных: `python -m seiltanzer --check`.

Прогоняет РЕАЛЬНЫЕ фиды (те же, что в боевом режиме, без --demo) по каждому
инструменту и печатает, что фактически пришло из бесплатного Yahoo: цена,
дневные бары, опционная цепочка (число страйков, implied move, скью, term),
индексы волатильности. Никаких синтетических данных — если источник не ответил,
это видно как FAIL/нет данных. Так вы подтверждаете, что заглушек нет и что
реально доступно в вашей сети.
"""

from __future__ import annotations

import tempfile

from .config import INSTRUMENTS, VOL_INDEX_TICKERS, Settings
from .data.cache import DiskCache
from .data.feeds import MarketData


def _fmt(v, n=2):
    return f"{v:.{n}f}" if isinstance(v, (int, float)) else "—"


def run_check() -> None:
    print("SEILTANZER — самопроверка боевых данных (реальный Yahoo, без заглушек)\n")
    settings = Settings(demo=False, data_dir=tempfile.mkdtemp())
    cache = DiskCache(settings.cache_db)
    md = MarketData(settings, cache)

    print("ИНДЕКСЫ ВОЛАТИЛЬНОСТИ:")
    md.refresh_vols()
    for key, tkr in VOL_INDEX_TICKERS.items():
        f = md.vols[key]
        st = f["status"]
        val = _fmt(f["value"])
        note = "" if st == "live" else f"  ({f.get('error') or 'нет данных'})"
        print(f"  {key.upper():5} {tkr:6}  {st:8} {val}{note}")

    header = (f"\n{'ИНСТР':7} {'ЦЕНА':>10} {'ДНЕВКИ':>7} {'ПРОКСИ':7} "
              f"{'ЦЕПОЧКА':8} {'СТРАЙК':>6} {'IMPL.MOVE':>9} {'СКЬЮ':>7} {'TERM':>12}")
    print(header)
    print("  " + "-" * (len(header) - 2))

    for code, inst in INSTRUMENTS.items():
        md.set_instrument(code)
        md.refresh_price()
        md.refresh_daily()
        md.refresh_chain()

        price = md.price
        price_s = f"{_fmt(price['value'])}" if price["status"] == "live" else price["status"]
        daily_s = "ok" if md.daily.get("bars") else "нет"
        proxy = inst.options_proxy or "—"
        exp_mark = " ⚠" if inst.proxy_experimental else ""

        m = md.chain.get("metrics")
        chain_st = md.chain["status"]
        if m:
            n_strikes = len(m["density"]["strikes"])
            impl = _fmt(m["implied_move"]["move_frac"] * 100, 2) + "%"
            skew = m.get("skew")
            skew_s = (f"{skew['rr']*100:+.1f}пп" if skew else "—")
            term = m.get("term")
            term_s = (term["shape"] if term else "—")
        else:
            n_strikes, impl, skew_s, term_s = "—", "—", "—", "—"
            if inst.options_proxy is None:
                chain_st = "нет прокси"

        print(f"  {code:7} {price_s:>10} {daily_s:>7} {proxy:5}{exp_mark:<2} "
              f"{chain_st:8} {str(n_strikes):>6} {impl:>9} {skew_s:>7} {term_s:>12}")

    print("\nЛегенда: live/ok = реальные данные Yahoo пришли; no_data/нет = источник")
    print("не ответил (честно, без подстановки); ⚠ = экспериментальный ETF-прокси")
    print("(тонкие опционы — низкая надёжность). Демо-данных здесь НЕТ.")
    cache.close()
