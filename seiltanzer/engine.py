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
            "regime": self._regime_payload(atr),
            "trade": trade,
            "prob": None,
            "mc": None,
            "ladder": None,
            "market": None,
            "levels": None,
            "options_summary": self._options_summary(),
            "filters": self._filters_payload(trade),
        }

        payload["verdict"] = None
        price = self.market.price.get("value")
        if trade and price:
            payload.update(self._trade_payloads(trade, price, sigma, atr))
            payload["verdict"] = self._verdict(payload)
        return payload

    def _verdict(self, p: dict) -> dict:
        """Синтез состояния сделки в понятный сигнал + рекомендуемое действие.

        Собирает край (рынок vs модель), фильтры стратегии, гамма-режим, фазу волы
        и позицию (r/лестница) в один вердикт: что это значит и что делать.
        Каждый фактор виден отдельно (не «чёрный ящик»).
        """
        prob, market = p.get("prob"), p.get("market")
        gamma, ladder = p.get("gamma"), p.get("ladder")
        filters = p.get("filters", [])
        factors, score = [], 0

        edge = market.get("edge") if market else None
        if edge is None:
            factors.append({"k": "КРАЙ", "v": "нет опционов для рынка", "tone": "neutral"})
        elif edge > 0.12:
            factors.append({"k": "КРАЙ", "v": f"+{edge*100:.0f}% — рынок недооценивает сетап", "tone": "good"}); score += 2
        elif edge > 0.03:
            factors.append({"k": "КРАЙ", "v": f"+{edge*100:.0f}% — лёгкий перевес над рынком", "tone": "good"}); score += 1
        elif edge < -0.12:
            factors.append({"k": "КРАЙ", "v": f"{edge*100:.0f}% — рынок оценивает выше вас", "tone": "bad"}); score -= 2
        elif edge < -0.03:
            factors.append({"k": "КРАЙ", "v": f"{edge*100:.0f}% — рынок чуть выше вас", "tone": "bad"}); score -= 1
        else:
            factors.append({"k": "КРАЙ", "v": "≈ на уровне рынка", "tone": "neutral"})

        blocks = [c for c in filters if c.get("required") and c["state"] == "block"]
        manuals = [c for c in filters if c.get("required") and c["state"] == "manual"]
        if blocks:
            factors.append({"k": "ФИЛЬТРЫ", "v": "BLOCK: " + ", ".join(c["label"] for c in blocks), "tone": "bad"}); score -= 3
        elif manuals:
            factors.append({"k": "ФИЛЬТРЫ", "v": "проверь вручную: " + ", ".join(c["label"] for c in manuals), "tone": "neutral"})
        else:
            factors.append({"k": "ФИЛЬТРЫ", "v": "все PASS", "tone": "good"})

        if gamma and gamma.get("available"):
            if gamma["zone"] == "positive":
                if gamma["toward"] == "тейку":
                    factors.append({"k": "ГАММА", "v": "+ зона, пиннинг тянет к тейку", "tone": "good"}); score += 1
                else:
                    factors.append({"k": "ГАММА", "v": "+ зона, пиннинг тянет к стопу — далёкий тейк труднее", "tone": "bad"}); score -= 1
            else:
                factors.append({"k": "ГАММА", "v": "− зона: движения ускоряются (тренд чище)", "tone": "neutral"})

        # скью (risk-reversal): направление рынка vs направление сделки
        opts = p.get("options_summary") or {}
        skew = opts.get("skew")
        direction = (prob or {}).get("r") is not None and (p.get("trade") or {}).get("direction")
        if skew and direction:
            tilt = skew["tilt"]
            aligned = (tilt == "бычий" and direction == "long") or \
                      (tilt == "медвежий" and direction == "short")
            against = (tilt == "медвежий" and direction == "long") or \
                      (tilt == "бычий" and direction == "short")
            if aligned:
                factors.append({"k": "СКЬЮ", "v": f"{tilt} уклон — по вашему направлению", "tone": "good"}); score += 1
            elif against:
                factors.append({"k": "СКЬЮ", "v": f"{tilt} уклон — против вашего направления", "tone": "bad"}); score -= 1
            else:
                factors.append({"k": "СКЬЮ", "v": "нейтральный уклон", "tone": "neutral"})

        # term-structure: ожидание движения
        term = opts.get("term")
        if term:
            if term["shape"] == "бэквордация":
                factors.append({"k": "TERM", "v": "бэквордация — рынок ждёт движение скоро", "tone": "neutral"})
            elif term["shape"] == "контанго":
                factors.append({"k": "TERM", "v": "контанго — спокойно, далёкие цели по времени ок", "tone": "neutral"})

        phase = (p.get("atr") or {}).get("phase")
        if phase == "shock":
            factors.append({"k": "ФАЗА", "v": "ШОК — экстремальная вола, лучше переждать", "tone": "bad"}); score -= 2
        elif phase == "flat":
            factors.append({"k": "ФАЗА", "v": "ФЛЭТ — режь цель, не жди далёкого тейка", "tone": "neutral"})

        # вердикт
        if blocks:
            label, tone = "НЕ ВХОДИТЬ", "bad"
            action = "Фильтр стратегии блокирует сетап — пропусти или дождись условий."
        elif score >= 3:
            label, tone = "СИЛЬНЫЙ ПЕРЕВЕС", "good"
            action = "Сетап в вашу пользу и рынок недооценивает — вход по плану, ведите по лестнице фиксации."
        elif score >= 1:
            label, tone = "ПЕРЕВЕС", "good"
            action = "Небольшой перевес — вход допустим, дисциплина по лестнице и БУ после 1.5R."
        elif score <= -3:
            label, tone = "ПРОТИВ ВАС", "bad"
            action = "Рынок/гамма/фаза против — пропустите или минимальный объём."
        elif score <= -1:
            label, tone = "ОСТОРОЖНО", "bad"
            action = "Есть встречные факторы — уменьшите объём или дождитесь лучшего расклада."
        else:
            label, tone = "НЕЙТРАЛЬНО", "neutral"
            action = "Явного перевеса нет — торгуйте только чёткий сетап, стандартный риск."

        # позиционная подсказка (в сделке)
        if ladder and prob:
            r = prob.get("r", 0)
            if ladder.get("be_armed"):
                action += " Стоп уже в БУ — снимайте по лестнице, остаток тралом."
            elif r >= 1.0:
                action += f" r={r:+.2f}: рубеж 1.0R пройден — фиксируйте 10%, двигайтесь к БУ (1.5R)."
            elif r <= -0.6:
                action += f" r={r:+.2f}: близко к стопу — не усредняйте, план на стоп готов."

        return {"label": label, "tone": tone, "action": action, "score": score,
                "edge": edge, "factors": factors}

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

    def _regime_payload(self, atr: dict) -> dict:
        """Регим-ридаут (из дневных баров): тренд в σ, кластер волы, реализованная
        вола. Всё выводимо из данных; при отсутствии — честные None."""
        bars = self.market.daily.get("bars")
        out = {"trend_sigma": None, "vol_cluster": None, "realized_vol": None,
               "phase": atr.get("phase"), "status": self.market.daily.get("status")}
        if not bars or len(bars.get("closes", [])) < 21:
            return out
        import numpy as np
        closes = np.asarray(bars["closes"][-21:], dtype=float)
        rets = np.diff(np.log(closes))
        if rets.std() > 0:
            # z-счёт последней доходности относительно 20-дневного распределения
            out["trend_sigma"] = round(float((rets[-1] - rets.mean()) / rets.std()), 2)
        out["realized_vol"] = self.market.baseline_vol()
        ph = atr.get("phase")
        out["vol_cluster"] = ({"shock": "ВЫСОКИЙ", "impulse": "ВЫСОКИЙ",
                               "flat": "НИЗКИЙ", "normal": "СРЕДНИЙ"}.get(ph))
        return out

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
        market = self._market_dist(trade, price, T, band.p)
        if market and market.get("edge") is not None:
            # фиксируется только один раз (первый тик после входа) — трек «край vs факт»
            self.journal.update_edge_at_open(trade["id"], market["edge"])
        gamma = self._gamma_pin(trade, price)
        return {"prob": prob, "mc": mc, "ladder": ladder, "market": market,
                "gamma": gamma,
                "levels": self._levels_payload(trade, price, sigma, gamma)}

    def _gamma_pin(self, trade: dict, price: float) -> dict:
        """Гамма-пиннинг в шкале инструмента (эвристика позиционирования дилеров)."""
        m = self.market.chain.get("metrics")
        scale = self._proxy_scale()
        if not m or not scale:
            return {"available": False,
                    "reason": f"нет опционной цепочки для {self.market.instrument_code}"}
        from .core.options import gamma_pin
        gex = m["gex"]
        strikes_instr = [s * scale for s in gex["strikes"]]
        flip = gex["zero_flip"] * scale if gex["zero_flip"] else None
        res = gamma_pin(strikes_instr, gex["net"], flip, price,
                        trade["entry"], trade["stop"], trade["take"], trade["direction"])
        res["demo"] = m.get("demo", False)
        return res

    def _market_dist(self, trade: dict, price: float, T: float,
                     p_model: float) -> dict | None:
        """Распределение исхода из рыночной risk-neutral плотности (опционы) в R.

        None, если для инструмента нет цепочки (тогда доска покажет модель честно).
        edge = P_модели − рыночный hit_ratio: положительный — ваша статистика даёт
        лучшие шансы, чем закладывает опционный рынок (потенциальный край/переоценка).
        """
        m = self.market.chain.get("metrics")
        scale = self._proxy_scale()
        if not m or not scale:
            return None
        try:
            from .core.options import RNDensity, market_r_distribution
            import numpy as np
            dens = RNDensity(strikes=np.asarray(m["density"]["strikes"]),
                             density=np.asarray(m["density"]["q"]),
                             t_years=m["t_years"])
            md = market_r_distribution(dens, scale, trade["entry"], trade["stop"],
                                       trade["take"], trade["direction"], T)
        except (ValueError, KeyError):
            return None
        edge = (p_model - md["hit_ratio"]) if md["hit_ratio"] is not None else None
        md.update({
            "available": True,
            "demo": m.get("demo", False),
            "expiry": m.get("expiry"),
            "edge": edge,
            "p_model": p_model,
        })
        return md

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
            "experimental": m.get("experimental", False),
            "skew": m.get("skew"),
            "term": m.get("term"),
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

    def _levels_payload(self, trade: dict, price: float, sigma: dict,
                        gamma: dict | None = None) -> dict:
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
        if gamma and gamma.get("available"):
            levels["gamma"] = {
                "magnet": gamma["magnet"], "zone": gamma["zone"],
                "pull_dir": gamma["pull_dir"], "strength": gamma["strength"],
                "toward": gamma["toward"], "flip": gamma["flip"],
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
