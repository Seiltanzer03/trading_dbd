"""Движок терминала: собирает состояние тика из фидов, журнала и мат. ядра.

Каждое поле выхода прослеживается до источника: prob.* — из статистики сетапа
и опционной поправки, mc.* — из Монте-Карло с теми же параметрами, options.* —
из последней реально полученной цепочки. Если данных нет — поле None и рядом
причина, фронт обязан показать состояние «нет данных».
"""

from __future__ import annotations

import datetime as dt
import time

from .config import INSTRUMENTS, LADDER_RUNGS, LADDER_FRACTION, BREAKEVEN_AFTER, \
    SETUPS, Settings
from .core import prob as pb
from .core import risk as rk
from .data.cache import DiskCache
from .data.feeds import MarketData
from .journal import Journal

US_CLOSE_UTC_HOUR = 21  # аппроксимация конца сессии для полосы implied move


def _seconds_to_session_end(now: float | None = None) -> float:
    t = dt.datetime.fromtimestamp(now or time.time(), dt.timezone.utc)
    close = t.replace(hour=US_CLOSE_UTC_HOUR, minute=0, second=0, microsecond=0)
    if t >= close:
        close += dt.timedelta(days=1)
    return (close - t).total_seconds()


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = DiskCache(settings.cache_db)
        self.journal = Journal(settings.trades_db)
        self.market = MarketData(settings, self.cache)
        self._mc_cache_key: tuple | None = None
        self._mc_cache: dict | None = None
        trade = self.journal.active_trade()
        if trade:
            self.market.set_instrument(trade["instrument"])

    # ------------------------------------------------------------ lifecycle

    def on_trade_opened(self, trade: dict) -> None:
        self.market.set_instrument(trade["instrument"])
        self._mc_cache_key = None
        # цепочку и дневки надо обновить сразу под новый инструмент
        self.market.refresh_daily()
        self.market.refresh_chain()

    # ------------------------------------------------------------- payloads

    def tick_payload(self) -> dict:
        now = time.time()
        account = self._account_payload()
        trade = self.journal.active_trade()
        atr = self._atr_payload()
        sigma = self.market.sigma_ratio()

        payload = {
            "ts": now,
            "demo": self.settings.demo,
            "instrument": self.market.instrument_code,
            "feeds": {
                "price": {k: v for k, v in self.market.price.items()},
                "chain": {k: v for k, v in self.market.chain.items() if k != "metrics"},
                "daily": {k: v for k, v in self.market.daily.items() if k != "bars"},
                "vols": self.market.vols,
            },
            "account": account,
            "atr": atr,
            "sigma": sigma,
            "trade": trade,
            "prob": None,
            "mc": None,
            "ladder": None,
            "levels": None,
            "options_summary": self._options_summary(),
            "filters": self._filters_payload(trade),
        }

        price = self.market.price.get("value")
        if trade and price:
            payload.update(self._trade_payloads(trade, price, sigma, atr))
        return payload

    def _account_payload(self) -> dict:
        acc = self.journal.account()
        balance_pct = acc["balance"] / acc["acc_size"] * 100.0 if acc["acc_size"] else 0.0
        row = rk.risk_matrix_row(balance_pct, acc["phase"])
        atr_mult = self._atr_payload().get("rr_mult") or 1.0
        return {
            **acc,
            "balance_pct": balance_pct,
            "risk": {
                "risk_pct": row.risk_pct,
                "base_risk_pct": row.base_risk_pct,
                "target_rr": row.target_rr,
                "target_rr_adjusted": round(row.target_rr * atr_mult, 3),
                "mode": row.mode,
                "phase": row.phase,
            },
        }

    def _atr_payload(self) -> dict:
        ratio = self.market.atr_ratio()
        if ratio is None:
            return {"status": "no_data", "ratio": None, "phase": None,
                    "k": None, "rr_mult": None,
                    "reason": "нет дневной истории инструмента"}
        ph = rk.classify_atr_phase(ratio)
        return {"status": self.market.daily.get("status", "no_data"),
                "ratio": round(ratio, 3), "phase": ph.phase, "k": ph.k,
                "rr_mult": ph.rr_mult, "reason": None}

    # ------------------------------------------------------------- filters

    def _filters_payload(self, trade: dict | None) -> list[dict]:
        setup = SETUPS.get(trade["setup"]) if trade else None
        req = set(setup.filters) if setup else set()

        def vol_chip(key: str, label: str, code: str, cmp_pass) -> dict:
            feed = self.market.vols[key]
            required = code in req
            chip = {"key": key, "label": label, "required": required,
                    "value": feed.get("value"), "status_feed": feed.get("status"),
                    "state": "na", "detail": None}
            if not required:
                chip["detail"] = "не требуется для активного сетапа"
                return chip
            if feed.get("value") is None:
                # ТЗ: если тикер недоступен — «проверь вручную», не пропускать молча
                chip["state"] = "manual"
                chip["detail"] = f"{label}: фид недоступен — проверь вручную"
                return chip
            chip["state"] = "pass" if cmp_pass(feed["value"]) else "block"
            return chip

        chips = [
            vol_chip("vix", "VIX>20", "vix_gt_20", lambda v: v > 20.0),
            vol_chip("gvz", "GVZ<18", "gvz_lt_18", lambda v: v < 18.0),
            vol_chip("dv1x", "DV1X<19", "dv1x_lt_19", lambda v: v < 19.0),
        ]

        atr = self._atr_payload()
        chips.append({
            "key": "atr", "label": "ATR-ФАЗА", "required": trade is not None,
            "value": atr.get("ratio"), "status_feed": atr.get("status"),
            "state": ("no_data" if atr.get("phase") is None else
                      "block" if atr["phase"] == "shock" else "pass"),
            "detail": (atr.get("reason") or
                       f"фаза {atr['phase']}, k={atr['k']}, RRx{atr['rr_mult']}"),
        })
        chips.append({
            "key": "tech", "label": "ТЕХАНАЛИЗ>-30", "required": trade is not None,
            "value": None, "status_feed": "manual", "state": "manual",
            "detail": "индикатор «Теханализ» TradingView на 1D NAS100 — только вручную",
        })
        return chips

    # -------------------------------------------------------- trade-specific

    def _trade_payloads(self, trade: dict, price: float, sigma: dict,
                        atr: dict) -> dict:
        entry, stop, take = trade["entry"], trade["stop"], trade["take"]
        direction = trade["direction"]
        r = pb.r_coordinate(price, entry, stop, direction)
        T = pb.target_rr_from_levels(entry, stop, take, direction)
        if T <= 0:
            return {}
        stats = self.journal.setup_stats(trade["setup"],
                                         self.settings.journal_min_trades)
        sr = sigma["ratio"] if sigma["applied"] else 1.0

        band = pb.prob_band(r, stats.wins, stats.n, T, sigma_ratio=sr)
        jn, jw = self.journal.journal_counts(trade["setup"])
        prob = {
            "r": r, "T": T, "p": band.p, "p_lo": band.p_lo, "p_hi": band.p_hi,
            "mu": band.mu, "sigma_ratio": band.sigma_ratio,
            "winrate": band.winrate, "wr_lo": band.wr_lo, "wr_hi": band.wr_hi,
            "n": stats.n, "wins": stats.wins,
            "calibration": stats.source,          # builtin | journal
            "journal_n": jn, "journal_wins": jw,
            "small_sample": stats.n < 30,          # ТЗ: <30 — всегда с интервалом
            "efficiency": stats.efficiency,
            "efficiency_verdict": rk.efficiency_verdict(stats.efficiency),
        }

        mc = self._mc(r, band.mu, band.sigma_ratio, T)

        max_r = max(trade.get("max_r") if trade.get("max_r") is not None else r, r)
        self.journal.update_max_r(trade["id"], max_r)
        crossed = [max_r >= rung - 1e-12 for rung in LADDER_RUNGS]
        ladder = {
            "rungs": list(LADDER_RUNGS),
            "fraction": LADDER_FRACTION,
            "crossed": crossed,
            "be_after": BREAKEVEN_AFTER,
            "be_armed": max_r >= BREAKEVEN_AFTER,
            "max_r": max_r,
        }
        return {"prob": prob, "mc": mc, "ladder": ladder,
                "levels": self._levels_payload(trade, price)}

    def _mc(self, r: float, mu: float, sigma_ratio: float, T: float) -> dict:
        key = (round(r, 2), round(mu, 3), round(sigma_ratio, 2), round(T, 2))
        if key == self._mc_cache_key and self._mc_cache is not None:
            return self._mc_cache
        res = pb.simulate_remainder(r, mu, sigma_ratio, T,
                                    n_paths=3000, dt=0.01, horizon=16.0,
                                    seed=int(key[0] * 100) & 0x7FFF)
        out = {
            "p_take": res.p_take,
            "p_stop": res.p_stop,
            "ev_hold": round(pb.ev_hold(res), 4),
            "ev_ladder": round(pb.ev_ladder(res, LADDER_RUNGS, LADDER_FRACTION,
                                            BREAKEVEN_AFTER), 4),
            "hist": pb.terminal_histogram(res, n_bins=9),
            "horizons": pb.horizon_probs(res, (1.0, 2.0, 4.0, 8.0)),
            "n_paths": len(res.terminal),
        }
        self._mc_cache_key, self._mc_cache = key, out
        return out

    # --------------------------------------------------------------- levels

    def _proxy_scale(self) -> float | None:
        """Коэффициент пересчёта цен прокси-ETF в шкалу инструмента.

        scale = цена_инструмента / спот_прокси на момент снапшота цепочки.
        Пропорциональное отображение — приближение, указывается в tooltip.
        """
        m = self.market.chain.get("metrics")
        price = self.market.price.get("value")
        if not m or not price or not m.get("spot"):
            return None
        return price / m["spot"]

    def _options_summary(self) -> dict | None:
        m = self.market.chain.get("metrics")
        if not m:
            return None
        scale = self._proxy_scale() or 1.0
        sess_rem_y = _seconds_to_session_end() / (365.0 * 24 * 3600)
        sigma_ann = m["implied_move"]["sigma_annual"]
        price = self.market.price.get("value")
        band = (price * sigma_ann * (sess_rem_y ** 0.5)) if price else None
        return {
            "proxy": m["proxy"],
            "expiry": m["expiry"],
            "demo": m.get("demo", False),
            "spot_proxy": m["spot"],
            "scale": scale,
            "implied_move_frac": m["implied_move"]["move_frac"],
            "implied_move_abs_instr": m["implied_move"]["move_abs"] * scale,
            "sigma_annual": sigma_ann,
            "session_band_abs": band,   # ±1σ до конца сессии в пунктах инструмента
            "gex_zero_flip_instr": (m["gex"]["zero_flip"] * scale
                                    if m["gex"]["zero_flip"] else None),
            "gex_top_instr": [{"price": t["strike"] * scale, "gex": t["gex"]}
                              for t in m["gex"]["top"]],
        }

    def _levels_payload(self, trade: dict, price: float) -> dict:
        opts = self._options_summary()
        vwap = self.market.vwap()
        day = self.market.day_range()
        levels = {
            "price": price,
            "entry": trade["entry"], "stop": trade["stop"], "take": trade["take"],
            "direction": trade["direction"],
            "zones": trade.get("zones") or [],
            "vwap": vwap,
            "vwap_reason": None if vwap is not None else
                "нет объёмов в интрадей-барах (кэш-индексы Yahoo не дают объём)",
            "day_low": day[0] if day else None,
            "day_high": day[1] if day else None,
            "implied_band": None,
            "gex": None,
        }
        if opts and opts.get("session_band_abs"):
            levels["implied_band"] = {
                "low": price - opts["session_band_abs"],
                "high": price + opts["session_band_abs"],
                "demo": opts["demo"],
            }
        if opts:
            levels["gex"] = {
                "zero_flip": opts["gex_zero_flip_instr"],
                "top": opts["gex_top_instr"],
                "demo": opts["demo"],
            }
        return levels

    # -------------------------------------------------------- ridge (chains)

    def ridge_payload(self) -> dict:
        """История BL-плотностей для Strike Landscape + разметка текущей сделки."""
        inst = self.market.instrument
        if inst.options_proxy is None:
            return {"available": False,
                    "reason": f"опционные данные недоступны для {inst.code}",
                    "snapshots": []}
        snaps = self.cache.chain_snapshots(inst.options_proxy, limit=10)
        if not snaps:
            return {"available": False,
                    "reason": "ещё нет ни одного снапшота цепочки",
                    "snapshots": []}
        scale = self._proxy_scale()
        trade = self.journal.active_trade()
        price = self.market.price.get("value")
        rn_probs = None
        if trade and scale:
            latest = snaps[-1]
            from .core.options import RNDensity
            import numpy as np
            dens = RNDensity(strikes=np.asarray(latest["density"]["strikes"]),
                             density=np.asarray(latest["density"]["q"]),
                             t_years=latest["t_years"])
            take_p, stop_p = trade["take"] / scale, trade["stop"] / scale
            if trade["direction"] == "long":
                p_take_side = dens.tail_probs(take_p)[0]   # выше тейка
                p_stop_side = dens.tail_probs(stop_p)[1]   # ниже стопа
            else:
                p_take_side = dens.tail_probs(take_p)[1]   # ниже тейка (шорт)
                p_stop_side = dens.tail_probs(stop_p)[0]   # выше стопа
            rn_probs = {"p_beyond_take": p_take_side, "p_beyond_stop": p_stop_side,
                        "expiry": latest.get("expiry"), "demo": latest.get("demo")}
        return {
            "available": True,
            "proxy": inst.options_proxy,
            "scale": scale,
            "snapshots": snaps,
            "trade": ({"entry": trade["entry"], "stop": trade["stop"],
                       "take": trade["take"], "direction": trade["direction"]}
                      if trade else None),
            "price": price,
            "rn_probs": rn_probs,
        }

    def close(self) -> None:
        self.cache.close()
        self.journal.close()
