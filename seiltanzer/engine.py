"""Движок терминала: собирает состояние тика из фидов, журнала и мат. ядра.

Каждое поле выхода прослеживается до источника: prob.* — из статистики сетапа
и опционной поправки, mc.* — из Монте-Карло с теми же параметрами, options.* —
из последней реально полученной цепочки. Если данных нет — поле None и рядом
причина, фронт обязан показать состояние «нет данных».
"""

from __future__ import annotations

import datetime as dt
import math
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

        # sigma_R — абсолютный ожидаемый ход сделки в R к экспирации (для карты/справки).
        sigma_R, sr_source = self._sigma_R(trade, price, sigma)
        # ширина доски определяется РЕЖИМОМ волы (implied/realized), а не абсолютом:
        # при тесных стопах абсолютный ход в разы больше барьеров и дал бы бинарный
        # исход. Здесь колокол всегда виден, но раздувается в разогнанном рынке
        # (ratio>1) и сжимается в сжатом (ratio<1) — и сдвигается с ценой (r0).
        ratio_eff = sigma["ratio"] if sigma.get("applied") else 1.0
        board_sigma_R = float(min(max(0.85 * math.sqrt(ratio_eff), 0.45), 1.7))

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
            "sigma_R": sigma_R,
            "sigma_R_source": sr_source,
            "board_sigma_R": board_sigma_R,
            "vol_regime": ("разогнанный" if ratio_eff > 1.15 else
                           "сжатый" if ratio_eff < 0.87 else "нормальный"),
        }

        mc = self._mc(r, band.mu, band.sigma_ratio, T, board_sigma_R)

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
                "levels": self._levels_payload(trade, price, sigma)}

    # горизонт по умолчанию для σ-поправки без цепочки (свинг-сделки): торг. дни
    DEFAULT_HORIZON_TRADING_DAYS = 5.0

    def _sigma_R(self, trade: dict, price: float, sigma: dict) -> tuple[float, str]:
        """Разброс хода сделки в R за горизонт распределения.

        Приоритет источника (всё из реальных данных):
          1) implied move опционной цепочки к экспирации -> "implied move";
          2) индекс волы / realized за горизонт по умолчанию -> "vol-index"/"realized";
          3) нейтральный разброс, если волы нет вовсе -> "нейтрально (нет волы)".
        Возврат (sigma_R, источник). sigma_R ограничен [0.25, 8].
        """
        risk = abs(trade["entry"] - trade["stop"])
        if risk <= 0:
            return 1.0, "нейтрально"
        opts = self._options_summary()
        if opts and opts.get("implied_move_abs_instr"):
            # move_abs = E|ΔS| к экспирации; СКО хода = move_abs*sqrt(pi/2)
            sr = opts["implied_move_abs_instr"] * math.sqrt(math.pi / 2) / risk
            return float(min(max(sr, 0.25), 8.0)), "implied move"
        # без цепочки: σ_implied из индекса волы либо realized, за горизонт по умолч.
        t_years = self.DEFAULT_HORIZON_TRADING_DAYS / 252.0
        if sigma.get("applied") and sigma.get("sigma_implied"):
            std_price = sigma["sigma_implied"] * price * math.sqrt(t_years)
            src = "vol-index" if sigma.get("source") == "vol_index" else "implied"
            return float(min(max(std_price / risk, 0.25), 8.0)), src
        base = self.market.baseline_vol()
        if base and base > 0:
            std_price = base * price * math.sqrt(t_years)
            return float(min(max(std_price / risk, 0.25), 8.0)), "realized 20д"
        return 1.2, "нейтрально (нет волы)"

    def _mc(self, r: float, mu: float, sigma_ratio: float, T: float,
            board_sigma_R: float) -> dict:
        key = (round(r, 2), round(mu, 3), round(sigma_ratio, 2), round(T, 2),
               round(board_sigma_R, 2))
        if key == self._mc_cache_key and self._mc_cache is not None:
            return self._mc_cache
        seed = int(abs(r) * 100) & 0x7FFF
        # eventual — до поглощения (для EV холд/лестница и hero-совместимой P)
        ev = pb.simulate_remainder(r, mu, sigma_ratio, T,
                                   n_paths=3000, dt=0.01, horizon=16.0, seed=seed)
        # forward — проекция к ближайшей части горизонта, ширина = режим волы (доска)
        fwd = pb.forward_distribution(r, theta=2.0 * mu, sigma_R=board_sigma_R, T=T,
                                      n_paths=4000, horizon=1.0, dt=0.005, seed=seed)
        out = {
            "p_take": ev.p_take,
            "p_stop": ev.p_stop,
            "ev_hold": round(pb.ev_hold(ev), 4),
            "ev_ladder": round(pb.ev_ladder(ev, LADDER_RUNGS, LADDER_FRACTION,
                                            BREAKEVEN_AFTER), 4),
            "hist": pb.terminal_histogram(fwd, n_bins=11),
            "p_take_horizon": fwd.p_take,
            "p_stop_horizon": fwd.p_stop,
            "n_paths": len(fwd.terminal),
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
            # ±ожидаемый ход к экспирации (implied move) — коридор рынка для карты
            "expiry_band_abs": m["implied_move"]["move_abs"] * scale,
            "gex_zero_flip_instr": (m["gex"]["zero_flip"] * scale
                                    if m["gex"]["zero_flip"] else None),
            "gex_top_instr": [{"price": t["strike"] * scale, "gex": t["gex"]}
                              for t in m["gex"]["top"]],
        }

    def _levels_payload(self, trade: dict, price: float, sigma: dict) -> dict:
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
        # коридор = ожидаемый ход рынка к экспирации (implied move); если его нет,
        # но есть σ-поправка из индекса волы — строим ±1σ за горизонт по умолчанию
        band_abs = opts.get("expiry_band_abs") if opts else None
        band_demo = opts.get("demo") if opts else False
        if band_abs is None and sigma.get("applied") and sigma.get("sigma_implied"):
            band_abs = sigma["sigma_implied"] * price * math.sqrt(5.0 / 252.0)
        if band_abs:
            levels["implied_band"] = {
                "low": price - band_abs, "high": price + band_abs,
                "demo": bool(band_demo),
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
