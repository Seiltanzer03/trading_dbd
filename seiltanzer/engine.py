"""Движок терминала: собирает состояние тика из фидов, журнала и мат. ядра.

Каждое поле выхода прослеживается до источника: главная prob.* — option-anchored
barrier MC, историческая модель хранится отдельно как model_* control; options.*
— из последней реально полученной цепочки плюс live-moneyness. Если option
anchor отсутствует, edge выключается, а визуальный fallback явно помечается.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from copy import deepcopy

from .config import (
    BREAKEVEN_AFTER,
    INSTRUMENTS,
    LADDER_FRACTION,
    LADDER_RUNGS,
    SETUPS,
    Settings,
)
from .core import prob as pb
from .core import risk as rk
from .data.cache import DiskCache
from .data.feeds import MarketData
from .journal import Journal


def clean_nans(obj):
    if isinstance(obj, dict):
        return {k: clean_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nans(v) for v in obj]
    elif isinstance(obj, float) and math.isnan(obj):
        return None
    return obj

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
        self.stream_hub = None
        if settings.stream:
            from .data.stream import StreamHub
            # Тики торгуемого ряда двигают сделку, тики ETF-прокси двигают
            # moneyness последнего опционного снимка между обновлениями цепочки.
            tickers = sorted(
                {i.yahoo for i in INSTRUMENTS.values()}
                | {i.options_proxy for i in INSTRUMENTS.values()
                   if i.options_proxy is not None}
                | {driver for i in INSTRUMENTS.values()
                   for driver in i.live_price_drivers})
            self.stream_hub = StreamHub(tickers)
            self.market.stream = self.stream_hub
        self._mc_cache_key: tuple | None = None
        self._mc_cache: dict | None = None
        self._cone_cache_key: tuple | None = None
        self._cone_cache: dict | None = None
        trade = self.journal.active_trade()
        if trade:
            self.market.set_instrument(trade["instrument"])

    # ------------------------------------------------------------ lifecycle

    def _reset_scenario_caches(self) -> None:
        self._mc_cache_key = None
        self._mc_cache = None
        self._cone_cache_key = None
        self._cone_cache = None

    def on_trade_opened(self, trade: dict) -> None:
        self.market.set_instrument(trade["instrument"])
        self._reset_scenario_caches()
        # цепочку и дневки надо обновить сразу под новый инструмент
        self.market.refresh_proxy_price()
        self.market.refresh_daily()
        self.market.refresh_chain()
        self.market.refresh_iv_surface()

    def on_trade_edited(self, trade: dict) -> None:
        """Синхронизирует активный фид и сценарные кэши после правки сделки."""
        instrument_changed = (
            self.market.instrument_code != trade["instrument"])
        self.market.set_instrument(trade["instrument"])
        self._reset_scenario_caches()
        if instrument_changed:
            self.market.refresh_price()
            self.market.refresh_proxy_price()
            self.market.refresh_daily()
            self.market.refresh_chain()
            self.market.refresh_iv_surface()

    def _trade_quote_offset(self, trade: dict | None) -> float:
        """Калибровка брокера; старый futures→CFD basis после смены фида недействителен."""
        if not trade:
            return 0.0
        offset = float(trade.get("quote_offset") or 0.0)
        direct_source = ("Swissquote OTC" if self.market.instrument.swissquote_pair
                         else "TradingView snapshot"
                         if self.market.instrument.tradingview_symbol else None)
        if (offset and not self.settings.demo and direct_source
                and direct_source not in str(trade.get("quote_source") or "")):
            return 0.0
        return offset

    def _effective_price(self, trade: dict | None, raw_price: float | None) -> float | None:
        """Цена в шкале сделки: бесплатный тик + зафиксированный basis брокера."""
        if raw_price is None:
            return None
        return float(raw_price) + self._trade_quote_offset(trade)

    def _current_instrument_price(self, trade: dict | None = None) -> float | None:
        """Текущая цена в шкале терминала/сделки, а не сырой тик Yahoo."""
        if trade is None:
            trade = self.journal.active_trade()
        return self._effective_price(trade, self.market.price.get("value"))

    def _quoted_proxy_spot(self) -> float | None:
        """Полученная stream/REST-котировка proxy, без snapshot-fallback."""
        quote = self.market.proxy_price.get("value")
        if quote is not None and math.isfinite(float(quote)) and float(quote) > 0:
            return float(quote)
        return None

    def _current_proxy_spot(self, metrics: dict | None = None) -> float | None:
        """Котировка option-proxy; snapshot spot используется только как fallback."""
        quote = self._quoted_proxy_spot()
        if quote is not None:
            return quote
        metrics = metrics or self.market.chain.get("metrics")
        snap = (metrics or {}).get("spot")
        if snap is not None and math.isfinite(float(snap)) and float(snap) > 0:
            return float(snap)
        return None

    def _map_proxy_levels(self, levels, instrument_price: float | None = None,
                          metrics: dict | None = None) -> list[float] | None:
        """Страйки proxy -> шкала инструмента через актуальную moneyness."""
        metrics = metrics or self.market.chain.get("metrics")
        instrument_price = instrument_price or self._current_instrument_price()
        proxy_spot = self._current_proxy_spot(metrics)
        if metrics is None or instrument_price is None or proxy_spot is None:
            return None
        try:
            from .core.options import map_proxy_levels
            mapped = map_proxy_levels(
                levels, proxy_spot, instrument_price,
                self.market.instrument.proxy_transform)
        except (TypeError, ValueError):
            return None
        return [float(x) for x in mapped]

    def _mapped_density(self, instrument_price: float,
                        metrics: dict | None = None):
        """BL-плотность proxy -> плотность цены инструмента, включая Jacobian."""
        metrics = metrics or self.market.chain.get("metrics")
        proxy_spot = self._current_proxy_spot(metrics)
        if metrics is None or proxy_spot is None:
            return None
        try:
            import numpy as np

            from .core.options import RNDensity, map_proxy_density
            raw = RNDensity(
                strikes=np.asarray(metrics["density"]["strikes"], dtype=float),
                density=np.asarray(metrics["density"]["q"], dtype=float),
                t_years=float(metrics["t_years"]))
            return map_proxy_density(
                raw, proxy_spot, instrument_price,
                self.market.instrument.proxy_transform)
        except (KeyError, TypeError, ValueError):
            return None

    def _live_iv_surface(self) -> dict:
        """Опционный снимок + текущий тик прокси для динамического ATM/moneyness."""
        surf = deepcopy(getattr(self.market, "iv_surface", {}) or {})
        surf["spot_current"] = self.market.proxy_price.get("value")
        surf["spot_status"] = self.market.proxy_price.get("status")
        surf["spot_source"] = self.market.proxy_price.get("source")
        return surf

    # ------------------------------------------------------------- payloads

    def tick_payload(self) -> dict:
        now = time.time()
        account = self._account_payload()
        trade = self.journal.active_trade()
        atr = self._atr_payload()
        sigma = self.market.sigma_ratio()
        raw_price = self.market.price.get("value")
        price = self._effective_price(trade, raw_price)
        price_feed = {k: v for k, v in self.market.price.items()}
        price_feed.update({
            "value": price,
            "raw_value": raw_price,
            "effective_value": price,
            "basis_offset": self._trade_quote_offset(trade),
            "ticker": (self.market.instrument.tradingview_symbol
                       or self.market.instrument.swissquote_pair
                       or self.market.instrument.yahoo),
            "history_ticker": self.market.instrument.yahoo,
            "label": self.market.instrument.price_label or self.market.instrument.yahoo,
        })

        payload = {
            "ts": now,
            "demo": self.settings.demo,
            "instrument": self.market.instrument_code,
            "feeds": {
                "price": price_feed,
                "proxy_price": {k: v for k, v in self.market.proxy_price.items()},
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
            "cone": None,
            "state": None,
            "options_summary": self._options_summary(price),
            "iv_surface": self._live_iv_surface(),
            "correlation": getattr(self.market, "correlation", {}),
            "vrp": self._vrp_payload(),
            "filters": self._filters_payload(trade),
            "analytics": self._analytics_summary(price, trade),
        }

        payload["verdict"] = None
        if trade and price:
            payload.update(self._trade_payloads(trade, price, sigma, atr))
            payload["verdict"] = self._verdict(payload)
            payload["state"] = self._state_payload(payload)
        return clean_nans(payload)

    def _verdict(self, p: dict) -> dict:
        """Синтез состояния сделки в понятный сигнал + рекомендуемое действие.

        Собирает option P vs breakeven, применимые фильтры, фазу волы и позицию
        (r/лестница). GEX/skew показываются как контекст без скрытого веса.
        Каждый фактор виден отдельно (не «чёрный ящик»).
        """
        prob, market = p.get("prob"), p.get("market")
        gamma, ladder = p.get("gamma"), p.get("ladder")
        filters = p.get("filters", [])
        factors, score = [], 0

        barrier_ev = market.get("horizon_barrier_ev") if market else None
        if barrier_ev is None:
            reason = (market or {}).get("anchor_reason") or "нет валидной цепочки"
            factors.append({"k": "BARRIER EV≤H", "v": f"{reason} — оценка выключена",
                            "tone": "neutral"})
        elif barrier_ev > 0.15:
            factors.append({"k": "BARRIER EV≤H",
                            "v": f"+{barrier_ev:.2f}R к опционному горизонту",
                            "tone": "good"}); score += 2
        elif barrier_ev > 0.03:
            factors.append({"k": "BARRIER EV≤H",
                            "v": f"+{barrier_ev:.2f}R — умеренная асимметрия",
                            "tone": "good"}); score += 1
        elif barrier_ev < -0.15:
            factors.append({"k": "BARRIER EV≤H",
                            "v": f"{barrier_ev:.2f}R — опционная геометрия против сделки",
                            "tone": "bad"}); score -= 2
        elif barrier_ev < -0.03:
            factors.append({"k": "BARRIER EV≤H",
                            "v": f"{barrier_ev:.2f}R — слабая встречная асимметрия",
                            "tone": "bad"}); score -= 1
        else:
            factors.append({"k": "BARRIER EV≤H", "v": "≈ 0R",
                            "tone": "neutral"})

        # Только точные автоматические фильтры могут блокировать вердикт.
        # 15m Yahoo-контекст не притворяется полной 4H-последовательностью стратегии.
        blocks = [c for c in filters if c.get("required") and c["state"] == "block"
                  and c.get("decision_weight", True)]
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
                    factors.append({"k": "ГАММА · КОНТЕКСТ",
                                    "v": "+ условный OI×gamma уровень со стороны тейка",
                                    "tone": "neutral"})
                else:
                    factors.append({"k": "ГАММА · КОНТЕКСТ",
                                    "v": "+ условный OI×gamma уровень со стороны стопа",
                                    "tone": "neutral"})
            else:
                factors.append({"k": "ГАММА · КОНТЕКСТ",
                                "v": "− условная зона; знак позиции не наблюдается",
                                "tone": "neutral"})

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
                factors.append({"k": "СКЬЮ · КОНТЕКСТ",
                                "v": f"{tilt} уклон — по вашему направлению",
                                "tone": "neutral"})
            elif against:
                factors.append({"k": "СКЬЮ · КОНТЕКСТ",
                                "v": f"{tilt} уклон — против вашего направления",
                                "tone": "neutral"})
            else:
                factors.append({"k": "СКЬЮ · КОНТЕКСТ",
                                "v": "нейтральный уклон", "tone": "neutral"})

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

        # вердикт. Без option anchor не превращаем фильтры/расстояния/историческую
        # таблицу в суррогат «преимущества»: остаётся только сценарный монитор.
        if blocks:
            label, tone = "НЕ ВХОДИТЬ", "bad"
            action = "Фильтр стратегии блокирует сетап — пропусти или дождись условий."
        elif barrier_ev is None:
            label, tone = "НЕТ OPTION MODEL", "neutral"
            action = (
                "Нет валидного опционного якоря — конус и доска показывают "
                "сценарии, но barrier-вероятности и EV выключены."
            )
        elif score >= 3:
            label, tone = "СИЛЬНЫЙ ПЕРЕВЕС", "good"
            action = "Опционная асимметрия поддерживает сделку — ведите по плану и следите за barrier EV."
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
                "edge": None, "barrier_ev": barrier_ev, "factors": factors}

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

    def _atr_abs(self) -> float | None:
        """ATR(20) в пунктах инструмента — для дистанций «до тейка/стопа в ATR»."""
        bars = self.market.daily.get("bars")
        if not bars:
            return None
        try:
            return rk.atr(bars["highs"], bars["lows"], bars["closes"], 20)
        except (ValueError, KeyError):
            return None

    def _atr_payload(self) -> dict:
        ratio = self.market.atr_ratio()
        atr_abs = self._atr_abs()
        if ratio is None:
            return {"status": "no_data", "ratio": None, "phase": None,
                    "k": None, "rr_mult": None, "atr_abs": atr_abs,
                    "reason": "нет дневной истории инструмента"}
        ph = rk.classify_atr_phase(ratio)
        return {"status": self.market.daily.get("status", "no_data"),
                "ratio": round(ratio, 3), "phase": ph.phase, "k": ph.k,
                "rr_mult": ph.rr_mult, "atr_abs": atr_abs, "reason": None}

    # ------------------------------------------------------------- filters

    def _filters_payload(self, trade: dict | None) -> list[dict]:
        setup = SETUPS.get(trade["setup"]) if trade else None
        req = set(setup.filters) if setup else set()

        def vol_chip(key: str, label: str, code: str, cmp_pass) -> dict:
            feed = self.market.vols[key]
            required = code in req
            chip = {"key": key, "label": label, "required": required,
                    "value": feed.get("value"), "status_feed": feed.get("status"),
                    "state": "na", "detail": None,
                    # Yahoo даёт бесплатный 15m-контекст, но не подтверждает
                    # многошаговую 4H-логику. Показываем, не блокируем опционный
                    # вердикт этим приблизительным слоем.
                    "decision_weight": False}
            if not required:
                chip["detail"] = "не требуется для активного сетапа"
                return chip
            if feed.get("value") is None:
                # ТЗ: если тикер недоступен — «проверь вручную», не пропускать молча
                chip["state"] = "manual"
                chip["detail"] = f"{label}: фид недоступен — проверь вручную"
                return chip
            chip["state"] = "pass" if cmp_pass(feed["value"]) else "block"
            chip["detail"] = (f"{label}: 15m delayed-контекст; "
                              "не является полным подтверждением сетапа")
            return chip

        chips = [
            vol_chip("vix", "VIX > 20", "vix_gt_20", lambda v: v > 20.0),
            vol_chip("gvz", "GVZ < 18", "gvz_lt_18", lambda v: v < 18.0),
            vol_chip("dv1x", "DV1X < 19", "dv1x_lt_19", lambda v: v < 19.0),
        ]

        atr = self._atr_payload()
        chips.append({
            "key": "atr", "label": "ATR-ФАЗА", "required": trade is not None,
            "value": atr.get("ratio"), "status_feed": atr.get("status"),
            "state": ("no_data" if atr.get("phase") is None else
                      "block" if atr["phase"] == "shock" else "pass"),
            "phase": atr.get("phase"), "rr_mult": atr.get("rr_mult"),
            "decision_weight": True,
            "detail": (atr.get("reason") or
                       f"фаза {atr['phase']}, k={atr['k']}, RRx{atr['rr_mult']}"),
        })
        tech_required = bool(trade and trade.get("instrument") == "NAS100")
        chips.append({
            "key": "tech", "label": "ТЕХАНАЛИЗ > −30", "required": tech_required,
            "value": None, "status_feed": "manual",
            "state": "manual" if tech_required else "na",
            "decision_weight": False,
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

        # sigma_R — абсолютный ОПЦИОННЫЙ ход в R к выбранной экспирации.
        sigma_R, sr_source = self._sigma_R(trade, price, sigma)
        # Тёмная контрольная линия доски остаётся статистической моделью сетапа;
        # оранжевая поверхность/корзины ниже будут опционными.
        ratio_eff = sigma["ratio"] if sigma.get("applied") else 1.0
        board_sigma_R = float(min(max(0.85 * math.sqrt(ratio_eff), 0.45), 1.7))

        prob = {
            "r": r, "T": T, "p": None, "p_lo": None, "p_hi": None,
            "p_breakeven": 1.0 / (1.0 + T),   # винрейт для EV=0 при RR 1:T
            "source": "no_option_anchor",
            "available": False,
            "model_p": band.p,
            "model_p_lo": band.p_lo, "model_p_hi": band.p_hi,
            "model_small_sample": stats.n < 30,
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
        # АСИММЕТРИЯ по скью (risk-reversal): сторона страха (падение цены) шире —
        # для лонга это −R (стоп), для шорта +R. skew_R>0 расширяет −R. Медвежий
        # скью (rr<0) в лонге → skew_R>0 (толще нижний хвост, честнее P стопа).
        opts = self._options_summary(price)
        sk = (opts or {}).get("skew")
        drift_R = 0.0
        skew_R = 0.0
        if sk and sk.get("rr") is not None:
            rr = sk["rr"]
            skew_R = float(min(max((-rr if direction == "long" else rr) * 3.0, -0.4), 0.4))
        # TERM-STRUCTURE → форвардная вола конуса: контанго (slope>0) — вола дышит
        # позже (узко рано, шире к развязке); бэквордация (slope<0) — движение скоро.
        term = (opts or {}).get("term")
        term_slope = 0.0
        if term and term.get("slope") is not None:
            term_slope = float(min(max(term["slope"], -0.6), 0.6))
        # IV vs RV: во сколько раз реализованная вола отличается от implied
        iv_ann = sigma.get("sigma_implied") if sigma.get("applied") else None
        rv_ann = self.market.baseline_vol()
        rv_iv_ratio = (float(min(max(rv_ann / iv_ann, 0.3), 3.0))
                       if (iv_ann and iv_ann > 0 and rv_ann and rv_ann > 0) else None)
        # Ось времени и ширина теперь не подгоняются под расстояние стоп/тейк.
        # Их задаёт реальная экспирация последней цепочки и implied move.
        horizon_years = (opts or {}).get("t_years")
        cone_sigma_R = sigma_R

        # RND к экспирации (Бриден–Литценбергер) — для Strike Landscape и задней стены
        terminal = self._market_dist(trade, price, T)
        forward_drift_source = "carry_neutral"
        forward_drift_rejected = None
        if terminal and terminal.get("mean_r") is not None:
            # Бесплатная BL-плотность иногда имеет урезанный хвост, из-за чего
            # её среднее улетает на несколько R. Это нельзя превращать в drift.
            # Принимаем только правдоподобный forward, затем shrink + cap как в
            # robust risk model; на intraday rejected forward = нейтральный carry.
            raw_forward_move = float(terminal["mean_r"] - r)
            plausibility = max(3.0 * sigma_R, 0.35)
            if abs(raw_forward_move) <= plausibility:
                cap = max(0.30 * sigma_R, 0.04)
                drift_R = float(min(max(0.25 * raw_forward_move, -cap), cap))
                forward_drift_source = "bl_forward_shrunk"
            else:
                forward_drift_rejected = raw_forward_move
            
        gamma_info = self._gamma_pin(trade, price)
        ou_theta = 0.0
        ou_mu = 0.0
        heston_kappa = 0.0
        heston_theta = 1.0
        heston_xi = 0.0
        heston_rho = 0.0

        # Бесплатный OI×Black-Scholes-gamma не раскрывает знак реальной dealer
        # позиции. Поэтому он остаётся визуальным контекстом и не имеет права
        # сжимать option-implied barrier distribution до ложных 0%/100%.
        # OU/Heston включаются только для источника с подтверждённым знаком.
        if (gamma_info and gamma_info.get("zone") == "positive"
                and gamma_info.get("decision_weight") is True):
            ou_theta = 5.0 * gamma_info.get("strength", 0.0)
            ou_mu = gamma_info.get("magnet_r", 0.0)
            # Притягиваем волатильность (Heston)
            heston_kappa = 2.0
            heston_theta = rv_iv_ratio if rv_iv_ratio else 1.0
            heston_xi = 0.4
            heston_rho = -0.7

        # risk-neutral конус: option-implied diffusion + skew/term/gamma.
        # Take-touch / stop-touch / no-touch считаются раздельно.
        cone = self._cone(r, T, cone_sigma_R, drift_R, skew_R, term_slope,
                          horizon_years, terminal, rv_iv_ratio,
                          ou_theta, ou_mu, heston_kappa, heston_theta, heston_xi, heston_rho)
        cone["forward_drift_source"] = forward_drift_source
        cone["forward_drift_rejected"] = forward_drift_rejected

        has_options = bool(
            terminal is not None
            and cone.get("hit_source") == "option_barrier_first_touch")
        option_p = cone["hit_ratio"] if has_options else None
        p_be = prob["p_breakeven"]
        # Binary EV threshold is invalid while no-touch mass exists. Keep the
        # finite-horizon barrier contribution in R, and do not publish a fake
        # probability edge against 1/(1+T).
        option_edge = None
        option_ev = ((T * cone["p_take"] - cone["p_stop"])
                     if has_options else None)
        # Сценарная полоса включает Monte-Carlo noise, возраст снимка и штраф
        # экспериментального прокси. Это не академический confidence interval.
        chain_age = (time.time() - self.market.chain["ts"]
                     if self.market.chain.get("ts") else None)
        if option_p is not None:
            systematic = 0.035
            if self.market.instrument.proxy_experimental:
                systematic += 0.055
            if chain_age is not None:
                systematic += min(chain_age / 7200.0, 1.0) * 0.04
            prob.update({
                "p": option_p,
                "p_lo": max(0.0, option_p - systematic),
                "p_hi": min(1.0, option_p + systematic),
                "source": "options_barrier_first_touch",
                "available": True,
                "uncertainty": "proxy+snapshot scenario band",
                "small_sample": False,
                "band_kind": "scenario",
            })
            # Главный EV теперь использует именно опционную вероятность. Исторический
            # path-sim лестницы сохраняется как отдельный исследовательский ориентир.
            mc["ev_hold_model"] = mc["ev_hold"]
            mc["ev_hold"] = option_ev
            mc["ev_hold_source"] = "options_horizon_barrier_component"
            mc["ev_ladder_source"] = "setup_path_control"
        else:
            prob["band_kind"] = None
            mc["ev_hold_source"] = "setup_path_control"
            mc["ev_ladder_source"] = "setup_path_control"

        # «Рынок» для всех визуальных инструментов: finite-horizon barrier MC,
        # форма/хвост/forward/skew/term которого пришли из опционной цепочки.
        market = {
            "available": has_options,
            "probs": cone["slice_probs"] if has_options else None,
            "edges": cone["slice_edges"] if has_options else None,
            "scenario_probs": cone["slice_probs"],
            "scenario_edges": cone["slice_edges"],
            "scenario_slice_alive": cone.get("slice_alive"),
            "scenario_slice_time_frac": cone.get("slice_time_frac"),
            "scenario_mode_r": cone.get("slice_mode_r"),
            "scenario_p10_r": cone.get("slice_p10_r"),
            "scenario_median_r": cone.get("slice_median_r"),
            "scenario_p90_r": cone.get("slice_p90_r"),
            "p_take": option_p,
            "p_stop": cone.get("p_stop") if has_options else None,
            # Three-outcome finite horizon: no-touch is never folded into stop.
            "p_take_horizon": cone.get("p_take"),
            "p_stop_horizon": cone.get("p_stop"),
            "p_unresolved_horizon": cone.get("unresolved"),
            "p_take_reached_horizon": cone.get("p_take"),
            "p_stop_reached_horizon": cone.get("p_stop"),
            "p_unresolved_raw_horizon": cone.get("unresolved"),
            "hit_ratio": option_p,
            "edge": option_edge,
            "option_ev": option_ev,
            "horizon_barrier_ev": option_ev,
            "p_breakeven": p_be,
            "p_model": band.p,
            "horizon_years": horizon_years,
            "median_years": cone.get("median_years"),
            "source": cone.get("hit_source"), "has_chain": terminal is not None,
            "demo": (terminal or {}).get("demo", self.settings.demo),
            "terminal_p_take": (terminal or {}).get("p_take"),
            "terminal_p_stop": (terminal or {}).get("p_stop"),
            "terminal_hit": (terminal or {}).get("hit_ratio"),
            "forward_drift_source": forward_drift_source,
            "forward_drift_rejected": forward_drift_rejected,
            "chain_age_sec": chain_age,
            "proxy": self.market.instrument.options_proxy,
            "proxy_transform": self.market.instrument.proxy_transform,
            "barriers_supported": (terminal or {}).get("barriers_supported"),
            "tail_anchor_supported": (terminal or {}).get("tail_anchor_supported"),
            "terminal_tail_mass": (terminal or {}).get("tail_mass"),
            "support_low": (terminal or {}).get("support_low"),
            "support_high": (terminal or {}).get("support_high"),
            "anchor_reason": (
                None if has_options else
                "стоп/тейк вне доступной сетки страйков"
                if terminal and terminal.get("barriers_supported") is False else
                "слишком малая BL-масса за барьерами для устойчивого tail anchor"
                if terminal and terminal.get("tail_anchor_supported") is False else
                "нет валидной option-плотности или proxy mapping"
            ),
            "quality": ("experimental" if self.market.instrument.proxy_experimental
                        else "reference_proxy"),
        }
        # Первый снимок + редкие живые снимки дают out-of-sample проверку, а не
        # обещание преимущества по одному красивому кадру.
        self.journal.update_edge_at_open(trade["id"], option_edge)
        if option_p is not None:
            self.journal.record_option_forecast(
                trade["id"], price=price, r=r,
                p_take=option_p, p_stop=cone.get("p_stop", 0.0),
                p_unresolved=cone.get("unresolved", 0.0),
                option_edge=option_edge, option_ev=option_ev,
                chain_ts=self.market.chain.get("ts"), chain_age_sec=chain_age,
                source=cone.get("hit_source") or "unknown")
        return {"prob": prob, "mc": mc, "ladder": ladder, "market": market,
                "gamma": gamma_info, "cone": cone,
                "levels": self._levels_payload(trade, price, sigma, gamma_info)}

    def _cone(self, r: float, T: float, sigma_R: float, drift_R: float,
              skew_R: float, term_slope: float, horizon_years: float | None,
              terminal: dict | None, rv_iv_ratio: float | None,
              ou_theta: float = 0.0, ou_mu: float = 0.0,
              heston_kappa: float = 0.0, heston_theta: float = 1.0,
              heston_xi: float = 0.0, heston_rho: float = 0.0) -> dict:
        """3D risk-neutral конус: эволюция распределения R под ОПЦИОННУЮ волу.

        Драйверы — sigma_R (implied move в R), снос drift_R (скью) и цена (r0);
        ВИНРЕЙТ не участвует. Ось времени — реальные дни до экспирации. Дальняя
        грань несёт терминальную RND рынка (Бриден–Литценбергер) как ориентир.
        Кэш — по округлённым параметрам (пересчёт только при заметном сдвиге r/волы).
        """
        terminal_hit = (terminal or {}).get("hit_ratio")
        key = (round(r, 3), round(sigma_R, 3), round(T, 2), round(drift_R, 3),
               round(skew_R, 3), round(term_slope, 3),
               round((horizon_years or 0.0) * 3650, 2),
               round(float(terminal_hit), 4) if terminal_hit is not None else -1.0,
               round(ou_theta, 2), round(ou_mu, 2),
               round(heston_kappa, 2), round(heston_theta, 2),
               round(heston_xi, 2), round(heston_rho, 2))
        if key == self._cone_cache_key and self._cone_cache is not None:
            base = self._cone_cache
        else:
            # Common random numbers: a live r-tick must move the same simulated
            # paths, not reshuffle all paths and make the density jump.
            seed = 0x5A5A
            base = pb.rn_cone(r, sigma_R, T, drift_R=drift_R, skew=skew_R,
                              term_slope=term_slope, horizon_years=horizon_years,
                              terminal_hit=terminal_hit,
                              ou_theta=ou_theta, ou_mu=ou_mu,
                              heston_kappa=heston_kappa, heston_theta=heston_theta,
                              heston_xi=heston_xi, heston_rho=heston_rho,
                              seed=seed)
            self._cone_cache_key, self._cone_cache = key, base
        out = dict(base)
        # Конус остаётся видимым и без опционов, но в таком случае он явно
        # обозначен как fallback и не участвует в расчёте преимущества.
        out["available"] = True
        out["option_anchored"] = (
            terminal is not None
            and out.get("hit_source") == "option_barrier_first_touch"
        )
        out["scenario_only"] = not out["option_anchored"]
        out["probability_available"] = out["option_anchored"]
        out["rv_iv_ratio"] = rv_iv_ratio
        if terminal and terminal.get("probs"):
            out["market_terminal"] = terminal["probs"]
            out["market_edges"] = terminal["edges"]
            out["market_p_take"] = terminal.get("p_take")
            out["market_p_stop"] = terminal.get("p_stop")
            out["market_mean_r"] = terminal.get("mean_r")
            out["market_demo"] = terminal.get("demo", False)
        else:
            out["market_terminal"] = None
        return out

    def _state_payload(self, p: dict) -> dict | None:
        """Строка «СОСТОЯНИЕ / ПЕРСПЕКТИВА»: где сделка сейчас и куда клонит.

        Собирает в один взгляд: текущий r, дистанции до тейка/стопа (в R и в ATR),
        P(тейк раньше стопа) с полосой, сдвиг края относительно входа и одно
        рекомендованное действие (сжатая формулировка вердикта).
        """
        prob, trade = p.get("prob"), p.get("trade")
        if not prob or not trade:
            return None
        price = (p.get("levels") or {}).get("price")
        if price is None:
            price = self._current_instrument_price(trade)
        r, T = prob["r"], prob["T"]
        atr_abs = (p.get("atr") or {}).get("atr_abs")
        stop, take = trade["stop"], trade["take"]
        to_take_atr = (abs(take - price) / atr_abs) if (atr_abs and price) else None
        to_stop_atr = (abs(price - stop) / atr_abs) if (atr_abs and price) else None
        market = p.get("market")
        edge = market.get("edge") if market else None
        edge_open = trade.get("edge_at_open")
        edge_shift = (edge - edge_open) if (edge is not None and edge_open is not None) else None
        verdict = p.get("verdict") or {}
        ladder = p.get("ladder") or {}
        return {
            "r": r, "T": T,
            "to_take_r": T - r, "to_stop_r": r + 1.0,
            "to_take_atr": to_take_atr, "to_stop_atr": to_stop_atr,
            "atr_abs": atr_abs,
            "p": prob["p"], "p_lo": prob["p_lo"], "p_hi": prob["p_hi"],
            "p_breakeven": prob.get("p_breakeven"),
            "small_sample": prob.get("small_sample"),
            "edge": edge, "edge_at_open": edge_open, "edge_shift": edge_shift,
            "horizon_barrier_ev": (market or {}).get("horizon_barrier_ev"),
            "p_no_touch_horizon": (market or {}).get("p_unresolved_horizon"),
            "median_years": (market or {}).get("median_years"),
            "label": verdict.get("label"), "tone": verdict.get("tone"),
            "be_armed": ladder.get("be_armed"),
            "headline": self._state_headline(r, prob, verdict, ladder),
        }

    @staticmethod
    def _state_headline(r: float, prob: dict, verdict: dict, ladder: dict) -> str:
        """Одна короткая формулировка действия (сжатие вердикта под текущий r)."""
        base = {
            "СИЛЬНЫЙ ПЕРЕВЕС": "держите по плану, снимайте по лестнице фиксации",
            "ПЕРЕВЕС": "вход/удержание допустимы, БУ после 1.5R",
            "НЕЙТРАЛЬНО": "торгуйте только чёткий сетап, стандартный риск",
            "ОСТОРОЖНО": "уменьшите объём или дождитесь лучшего расклада",
            "ПРОТИВ ВАС": "пропуск или минимальный объём",
            "НЕ ВХОДИТЬ": "фильтр стратегии блокирует — пропустите",
            "НЕТ OPTION EDGE": "сценарный монитор без вероятности — ждите option anchor",
        }.get(verdict.get("label"), "оцените по факторам вердикта")
        if ladder.get("be_armed"):
            return "стоп в БУ — снимайте по лестнице, остаток тралом; " + base
        if r >= 1.0:
            return "рубеж 1.0R пройден — фиксируйте 10%; БУ только после 1.5R; " + base
        if r <= -0.6:
            return "близко к стопу — не усредняйте, план на стоп готов; " + base
        return base

    def _gamma_pin(self, trade: dict, price: float) -> dict:
        """Гамма-пиннинг в шкале инструмента (эвристика позиционирования дилеров)."""
        m = self.market.chain.get("metrics")
        if not m:
            return {"available": False,
                    "reason": f"нет опционной цепочки для {self.market.instrument_code}"}
        if self.market.instrument.proxy_transform == "inverse":
            return {
                "available": False,
                "reason": ("GEX отключён для inverse-proxy: без знака реальной "
                           "дилерской позиции перенос был бы вводящим в заблуждение"),
                "decision_weight": False,
            }
        from .core.options import gamma_pin
        gex = m["gex"]
        strikes_instr = self._map_proxy_levels(gex["strikes"], price, m)
        if not strikes_instr:
            return {"available": False, "reason": "нет синхронной цены proxy"}
        flip_levels = (self._map_proxy_levels([gex["zero_flip"]], price, m)
                       if gex.get("zero_flip") is not None else None)
        flip = flip_levels[0] if flip_levels else None
        res = gamma_pin(strikes_instr, gex["net"], flip, price,
                        trade["entry"], trade["stop"], trade["take"], trade["direction"])
        res["demo"] = m.get("demo", False)
        res["decision_weight"] = False
        res["quality"] = "oi_heuristic_not_observed_dealer_position"
        return res

    def _market_dist(self, trade: dict, price: float, T: float) -> dict | None:
        """Распределение исхода из рыночной risk-neutral плотности (опционы) в R.

        Плотность сначала переносится из proxy в инструмент через текущую
        moneyness (для inverse-прокси — с Jacobian), затем раскладывается по
        барьерам сделки. None означает, что edge должен быть выключен.
        """
        m = self.market.chain.get("metrics")
        dens = self._mapped_density(price, m)
        if not m or dens is None:
            return None
        try:
            from .core.options import market_r_distribution
            md = market_r_distribution(dens, 1.0, trade["entry"], trade["stop"],
                                       trade["take"], trade["direction"], T)
        except (TypeError, ValueError, KeyError):
            return None
        md.update({
            "available": True,
            "demo": m.get("demo", False),
            "expiry": m.get("expiry"),
            "proxy_spot_current": self._current_proxy_spot(m),
            "proxy_spot_snapshot": m.get("spot"),
            "proxy_transform": self.market.instrument.proxy_transform,
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
        opts = self._options_summary(price)
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
        """Совместимый scale только для direct-proxy.

        Новая математика использует `_map_proxy_levels`; единого множителя у
        inverse-прокси не существует.
        """
        m = self.market.chain.get("metrics")
        if self.market.instrument.proxy_transform != "direct":
            return None
        price = self._current_instrument_price()
        proxy_spot = self._current_proxy_spot(m)
        if not m or not price or not proxy_spot:
            return None
        return price / proxy_spot

    def _options_summary(self, price: float | None = None) -> dict | None:
        m = self.market.chain.get("metrics")
        if not m:
            return None
        price = price or self._current_instrument_price()
        proxy_current = self._current_proxy_spot(m)
        if price is None or proxy_current is None:
            return None
        sess_rem_y = _seconds_to_session_end() / (365.0 * 24 * 3600)
        sigma_ann = m["implied_move"]["sigma_annual"]
        band = (price * sigma_ann * (sess_rem_y ** 0.5)) if price else None

        # Для inverse-прокси инструментальные calls/puts меняются местами:
        # дорогие calls FXC означают защиту/ставку на падение USD/CAD.
        skew = deepcopy(m.get("skew"))
        if skew and self.market.instrument.proxy_transform == "inverse":
            skew["rr_proxy"] = skew.get("rr")
            skew["rr"] = -float(skew["rr"])
            skew["call_iv_otm"], skew["put_iv_otm"] = (
                skew.get("put_iv_otm"), skew.get("call_iv_otm"))
            skew["tilt"] = ("бычий" if skew["rr"] > 0.01 else
                            "медвежий" if skew["rr"] < -0.01 else
                            "нейтральный")

        gex_available = self.market.instrument.proxy_transform == "direct"
        mapped_flip = None
        mapped_top: list[dict] = []
        if gex_available:
            flip = m["gex"].get("zero_flip")
            if flip is not None:
                vals = self._map_proxy_levels([flip], price, m)
                mapped_flip = vals[0] if vals else None
            top_strikes = [t["strike"] for t in m["gex"].get("top", [])]
            top_mapped = self._map_proxy_levels(top_strikes, price, m) or []
            mapped_top = [
                {"price": mapped, "gex": src["gex"]}
                for mapped, src in zip(top_mapped, m["gex"].get("top", []))
            ]

        chain_ts = self.market.chain.get("ts")
        chain_age = time.time() - chain_ts if chain_ts else None
        return {
            "proxy": m["proxy"],
            "expiry": m["expiry"],
            "t_years": m.get("t_years"),
            "demo": m.get("demo", False),
            "experimental": m.get("experimental", False),
            "skew": skew,
            "term": m.get("term"),
            "spot_proxy": m["spot"],
            "spot_proxy_snapshot": m["spot"],
            "spot_proxy_current": proxy_current,
            "spot_proxy_status": self.market.proxy_price.get("status"),
            "spot_proxy_source": self.market.proxy_price.get("source"),
            "spot_proxy_is_snapshot_fallback": self._quoted_proxy_spot() is None,
            "proxy_move_since_snapshot": proxy_current / m["spot"] - 1.0,
            "proxy_transform": self.market.instrument.proxy_transform,
            "price_instrument_current": price,
            "price_label": (self.market.instrument.price_label
                            or self.market.instrument.yahoo),
            "chain_status": self.market.chain.get("status"),
            "chain_age_sec": chain_age,
            "implied_move_frac": m["implied_move"]["move_frac"],
            # Процентный implied move переносим на текущую цену; это устойчивее
            # абсолютного ETF-scale и корректно по направлению и для inverse.
            "implied_move_abs_instr": price * m["implied_move"]["move_frac"],
            "sigma_annual": sigma_ann,
            "session_band_abs": band,   # ±1σ до конца сессии в пунктах инструмента
            # ±ожидаемый ход к экспирации (implied move) — коридор рынка для карты
            "expiry_band_abs": price * m["implied_move"]["move_frac"],
            "gex_available": gex_available,
            "gex_reason": (None if gex_available else
                           "inverse-proxy: знак dealer GEX не переносится надёжно"),
            "gex_zero_flip_instr": mapped_flip,
            "gex_top_instr": mapped_top,
        }

    def _vrp_payload(self) -> dict:
        m = self.market.chain.get("metrics")
        rv = self.market.baseline_vol()
        if not m or rv is None or rv <= 0:
            return {"available": False}
        iv = m["implied_move"]["sigma_annual"]
        vrp = iv - rv
        vrp_pct = vrp / rv
        regime = "iv_premium" if vrp > 0.05 else ("iv_discount" if vrp < -0.03 else "balanced")
        return {
            "available": True,
            "iv": iv,
            "rv": rv,
            "vrp": vrp,
            "vrp_pp": vrp * 100.0,
            "vrp_pct": vrp_pct,
            "iv_rv_ratio": iv / rv,
            "regime": regime,
            "snapshot_ts": self.market.chain.get("ts"),
            "source": self.market.chain.get("source"),
        }

    def _volume_profile_payload(self, quote_offset: float = 0.0) -> dict | None:
        intraday = self.market.intraday
        if not intraday:
            return None

        prices = [x[1] + quote_offset for x in intraday]
        min_p, max_p = min(prices), max(prices)
        if min_p == max_p:
            return None

        # Фиксированная «nice» сетка от дневного ATR, а не min/max каждого
        # обновления. Поэтому новый экстремум сессии добавляет бины, но не
        # перебивает всю историю и не заставляет профиль прыгать.
        atr_abs = self._atr_abs()
        raw_step = (
            atr_abs / 36.0 if atr_abs and atr_abs > 0
            else (max_p - min_p) / 40.0
        )
        raw_step = max(raw_step, abs((min_p + max_p) / 2.0) * 1e-7, 1e-9)
        magnitude = 10.0 ** math.floor(math.log10(raw_step))
        scaled = raw_step / magnitude
        nice = next(x for x in (1.0, 2.0, 2.5, 5.0, 10.0) if scaled <= x)
        bin_size = nice * magnitude
        origin = math.floor(min_p / bin_size) * bin_size

        profile = {}  # {price_bin: {"volume": v, "bid_vol": b, "ask_vol": a}}
        total_vol = 0
        has_real_volume = sum(x[2] for x in intraday) > 0

        prev_p = None
        for _ts, raw_p, vol in intraday:
            p = raw_p + quote_offset
            # Если нет реального объема, используем 1 как TPO (Time Price Opportunity)
            v = vol if has_real_volume else 1.0
            
            # Эмуляция CVD (Cumulative Volume Delta) по тику:
            # Если цена выросла — агрессивная покупка (ask), упала — агрессивная продажа (bid)
            bid_vol = 0.0
            ask_vol = 0.0
            if prev_p is not None:
                if p > prev_p:
                    ask_vol = v
                elif p < prev_p:
                    bid_vol = v
                else:
                    bid_vol = v * 0.5
                    ask_vol = v * 0.5
            else:
                bid_vol = v * 0.5
                ask_vol = v * 0.5
            prev_p = p
            
            b = origin + math.floor((p - origin) / bin_size) * bin_size
            b = round(b + bin_size / 2.0, 8)
            
            if b not in profile:
                profile[b] = {"volume": 0.0, "bid_vol": 0.0, "ask_vol": 0.0}
            
            profile[b]["volume"] += v
            profile[b]["bid_vol"] += bid_vol
            profile[b]["ask_vol"] += ask_vol
            total_vol += v

        if total_vol == 0:
            return None

        poc_price = max(profile.keys(), key=lambda k: profile[k]["volume"])

        bins_list = [
            {
                "price": k, 
                "volume": v["volume"],
                "bid_vol": v["bid_vol"],
                "ask_vol": v["ask_vol"],
                "delta": v["ask_vol"] - v["bid_vol"]
            } 
            for k, v in profile.items()
        ]
        bins_list.sort(key=lambda x: x["price"])
        
        # 70% value area: берём наиболее принятые ценовые корзины до 70% массы,
        # затем показываем минимальную/максимальную границу выбранного набора.
        selected, accumulated = [], 0.0
        for price_bin, data in sorted(profile.items(), key=lambda kv: kv[1]["volume"], reverse=True):
            selected.append(price_bin)
            accumulated += data["volume"]
            if accumulated >= total_vol * 0.70:
                break

        return {
            "poc": poc_price,
            "bins": bins_list,
            "total": total_vol,
            "is_tpo": not has_real_volume,
            "bin_size": bin_size,
            "value_area_low": min(selected) if selected else None,
            "value_area_high": max(selected) if selected else None,
            "window_start": min(x[0] for x in intraday),
            "window_end": max(x[0] for x in intraday),
        }

    def _levels_payload(self, trade: dict, price: float, sigma: dict,
                        gamma: dict | None = None) -> dict:
        opts = self._options_summary(price)
        quote_offset = self._trade_quote_offset(trade)
        raw_vwap = self.market.vwap()
        raw_day = self.market.day_range()
        vwap = raw_vwap + quote_offset if raw_vwap is not None else None
        day = (
            (raw_day[0] + quote_offset, raw_day[1] + quote_offset)
            if raw_day else None)
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
            "volume_profile": self._volume_profile_payload(quote_offset),
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
        trade = self.journal.active_trade()
        price = self._current_instrument_price(trade)
        proxy_spot = self._current_proxy_spot(snaps[-1])
        if price is None or proxy_spot is None:
            return {
                "available": False,
                "reason": "нет синхронной цены инструмента или option-proxy",
                "snapshots": [],
            }
        mapped_snaps = [
            self._map_snapshot(s, price, proxy_spot)
            for s in snaps
        ]
        mapped_snaps = [s for s in mapped_snaps if s is not None]
        if not mapped_snaps:
            return {
                "available": False,
                "reason": "не удалось перенести option-proxy в шкалу инструмента",
                "snapshots": [],
            }
        oi_walls = self._oi_walls(mapped_snaps[-1], 1.0, price)
        rn_probs = None
        if trade:
            latest = mapped_snaps[-1]
            import numpy as np

            from .core.options import RNDensity
            dens = RNDensity(strikes=np.asarray(latest["density"]["strikes"]),
                             density=np.asarray(latest["density"]["q"]),
                             t_years=latest["t_years"])
            if trade["direction"] == "long":
                p_take_side = dens.tail_probs(trade["take"])[0]
                p_stop_side = dens.tail_probs(trade["stop"])[1]
            else:
                p_take_side = dens.tail_probs(trade["take"])[1]
                p_stop_side = dens.tail_probs(trade["stop"])[0]
            rn_probs = {"p_beyond_take": p_take_side, "p_beyond_stop": p_stop_side,
                        "expiry": latest.get("expiry"), "demo": latest.get("demo")}
        return clean_nans({
            "available": True,
            "proxy": inst.options_proxy,
            # Снимки уже в шкале инструмента; frontend scale оставлен равным 1
            # для обратной совместимости всех существующих визуальных слоёв.
            "scale": 1.0,
            "proxy_transform": inst.proxy_transform,
            "proxy_spot_current": proxy_spot,
            "snapshots": mapped_snaps,
            "trade": ({"entry": trade["entry"], "stop": trade["stop"],
                       "take": trade["take"], "direction": trade["direction"]}
                      if trade else None),
            "price": price,
            "rn_probs": rn_probs,
            "oi_walls": oi_walls,
        })

    def gex_migration_payload(self) -> dict:
        """Полноразмерный payload миграции условных уровней (GEX Migration Map)."""
        ridge = self.ridge_payload()
        if not ridge.get("available"):
            return {
                "available": False,
                "reason": ridge.get("reason", "Нет данных опционной цепочки"),
                "summary": {
                    "gamma_regime": "NO DATA",
                    "take_path": "NO DATA",
                    "path_pressure": 0.0,
                    "authority": "context_only",
                    "independent_vote": False,
                },
            }
        from .core.gex_migration import compute_gex_migration

        trade = self.journal.active_trade()
        price = ridge.get("price")
        snaps = ridge.get("snapshots") or []
        res = compute_gex_migration(snaps, price, trade)
        return clean_nans(res)

    def macro_regime_payload(self) -> dict:
        """Полноразмерный payload 3D Phase Space (Macro Regime Attractor)."""
        import time
        from .core.macro_regime import compute_macro_regime

        vols = self.market.vols
        corr = getattr(self.market, "correlation", {})
        raw_price = self.market.price.get("value")
        prices = []
        if raw_price and math.isfinite(raw_price):
            now = time.time()
            for i in range(50):
                prices.append({"ts": now - i * 300, "price": raw_price * (1.0 - i * 0.0002)})

        prev_regime = getattr(self, "_last_macro_regime", None)
        res = compute_macro_regime(prices, vols, corr, prev_regime)
        if res.get("available") and res.get("summary"):
            self._last_macro_regime = res["summary"]["regime"]
            self._macro_regime_summary_cache = res.get("summary")
        return clean_nans(res)

    def _analytics_summary(self, price, trade) -> dict:
        gex_mig = self.gex_migration_payload()
        gex_sum = (
            gex_mig.get("summary")
            if gex_mig.get("available")
            else {
                "available": False,
                "gamma_regime": "NO DATA",
                "take_path": "NO DATA",
                "path_pressure": 0.0,
                "authority": "context_only",
                "independent_vote": False,
            }
        )
        return {
            "gex_migration": gex_sum,
            "macro_regime": getattr(
                self,
                "_macro_regime_summary_cache",
                {"available": False, "authority": "strategy_context"},
            ),
            "wavelet": getattr(
                self,
                "_wavelet_summary_cache",
                {"available": False, "authority": "derived_price_context"},
            ),
            "cross_asset": getattr(
                self,
                "_cross_asset_summary_cache",
                {"available": False, "authority": "correlation_family"},
            ),
        }

    def _map_snapshot(self, snap: dict, instrument_price: float,
                      proxy_spot: float) -> dict | None:
        """Кэшированный option snapshot -> единая живая шкала инструмента."""
        try:
            import numpy as np

            from .core.options import RNDensity, map_proxy_density, map_proxy_levels

            out = deepcopy(snap)
            transform = self.market.instrument.proxy_transform

            raw_density = RNDensity(
                strikes=np.asarray(snap["density"]["strikes"], dtype=float),
                density=np.asarray(snap["density"]["q"], dtype=float),
                t_years=float(snap["t_years"]))
            dens = map_proxy_density(raw_density, proxy_spot, instrument_price,
                                     transform)
            out["density"] = {
                **out["density"],
                "strikes": dens.strikes.tolist(),
                "q": dens.density.tolist(),
            }

            oi = deepcopy(snap.get("oi_profile") or {})
            if oi.get("strikes"):
                oi_k = map_proxy_levels(
                    oi["strikes"], proxy_spot, instrument_price, transform)
                order = np.argsort(oi_k)
                call_oi = np.asarray(oi.get("call_oi") or [], dtype=float)
                put_oi = np.asarray(oi.get("put_oi") or [], dtype=float)
                oi["strikes"] = oi_k[order].tolist()
                if len(call_oi) == len(order) and len(put_oi) == len(order):
                    if transform == "inverse":
                        # proxy puts ~= instrument calls; proxy calls ~= puts.
                        oi["call_oi"] = put_oi[order].tolist()
                        oi["put_oi"] = call_oi[order].tolist()
                    else:
                        oi["call_oi"] = call_oi[order].tolist()
                        oi["put_oi"] = put_oi[order].tolist()
                out["oi_profile"] = oi

            gx = deepcopy(snap.get("gex") or {})
            if transform == "inverse":
                # Не рисуем ложный дилерский знак для нелинейно обратного proxy.
                gx.update({
                    "strikes": [],
                    "net": [],
                    "zero_flip": None,
                    "top": [],
                    "available": False,
                    "reason": "inverse-proxy: GEX sign disabled",
                })
            elif gx.get("strikes"):
                gk = map_proxy_levels(
                    gx["strikes"], proxy_spot, instrument_price, transform)
                order = np.argsort(gk)
                net = np.asarray(gx.get("net") or [], dtype=float)
                gx["strikes"] = gk[order].tolist()
                if len(net) == len(order):
                    gx["net"] = net[order].tolist()
                if gx.get("zero_flip") is not None:
                    gx["zero_flip"] = float(map_proxy_levels(
                        [gx["zero_flip"]], proxy_spot, instrument_price,
                        transform)[0])
                top = gx.get("top") or []
                if top:
                    tk = map_proxy_levels(
                        [t["strike"] for t in top], proxy_spot,
                        instrument_price, transform)
                    gx["top"] = [
                        {**t, "strike": float(k)} for t, k in zip(top, tk)
                    ]
                gx["available"] = True
            out["gex"] = gx
            out["spot_proxy_snapshot"] = snap.get("spot")
            out["spot_proxy_current"] = proxy_spot
            out["spot"] = instrument_price
            out["proxy_transform"] = transform
            return out
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _oi_walls(snap: dict, scale: float | None, price: float | None) -> dict | None:
        """Крупнейшие концентрации open interest коллов/путов.

        Показывает, где реально сосредоточены контракты, но не называет эти
        страйки поддержкой/сопротивлением: сторона позиции и хедж неизвестны.
        Расстояние — в % от цены.
        """
        oi = snap.get("oi_profile") if snap else None
        if not oi or not oi.get("strikes") or not scale:
            return None
        ks = [k * scale for k in oi["strikes"]]
        coi = oi.get("call_oi") or []
        poi = oi.get("put_oi") or []
        if len(coi) != len(ks) or len(poi) != len(ks) or not ks:
            return None
        ci = max(range(len(ks)), key=lambda i: coi[i])
        pi = max(range(len(ks)), key=lambda i: poi[i])
        call_wall, put_wall = ks[ci], ks[pi]

        def pct(level: float) -> float | None:
            return ((level - price) / price) if price else None

        return {
            "call_wall": call_wall, "put_wall": put_wall,
            "call_wall_pct": pct(call_wall), "put_wall_pct": pct(put_wall),
            "call_oi": float(coi[ci]), "put_oi": float(poi[pi]),
            "demo": snap.get("demo", False),
        }

    def diagnostics_payload(self) -> dict:
        """Самопроверка применимости данных и визуальных инструментов.

        Это не торговый сигнал: endpoint объясняет, какой ряд реально показан,
        чем он связан с опционами и какие слои имеют право влиять на verdict.
        """
        trade = self.journal.active_trade()
        inst = self.market.instrument
        raw = self.market.price.get("value")
        effective = self._effective_price(trade, raw)
        m = self.market.chain.get("metrics")
        proxy_spot = self._current_proxy_spot(m)
        quoted_proxy_spot = self._quoted_proxy_spot()
        proxy_quote_status = self.market.proxy_price.get("status")
        live_proxy_mapping = bool(
            quoted_proxy_spot is not None
            and proxy_quote_status in {"live", "demo"})
        chain_ts = self.market.chain.get("ts")
        age = time.time() - chain_ts if chain_ts else None
        has_density = bool(
            m and (m.get("density") or {}).get("strikes")
            and (m.get("density") or {}).get("q"))
        data_ready = bool(has_density and effective and proxy_spot is not None)
        trade_dist = None
        if trade and effective and data_ready:
            try:
                target_r = pb.target_rr_from_levels(
                    trade["entry"], trade["stop"], trade["take"],
                    trade["direction"])
                trade_dist = self._market_dist(trade, effective, target_r)
            except (KeyError, TypeError, ValueError):
                trade_dist = None
        barriers_supported = (
            trade_dist.get("barriers_supported") if trade_dist else None)
        tail_anchor_supported = (
            trade_dist.get("tail_anchor_supported") if trade_dist else None)
        anchor_ready = bool(
            data_ready
            and (not trade or (
                trade_dist is not None
                and trade_dist.get("hit_ratio") is not None
            ))
        )
        decision_ready = bool(trade and anchor_ready)
        dynamic_mapping = bool(data_ready and quoted_proxy_spot is not None)
        inverse = inst.proxy_transform == "inverse"

        warnings: list[str] = []
        if inst.code in {"XAU", "XAG"}:
            warnings.append(
                "price feed — активный COMEX futures; spot/CFD брокера может "
                "иметь постоянный basis и roll-разницу")
        if self.market.price.get("derived"):
            warnings.append(
                "между контрольными котировками уровень инструмента двигается "
                "по доходности live proxy; это derived mapping, не биржевой "
                "тик самого фьючерса/индекса")
        if self.market.price.get("driver_experimental"):
            warnings.append(
                f"тиковый драйвер {self.market.price.get('driver_ticker')} "
                "экспериментальный; он оживляет только доходность между "
                "контрольными котировками основного ряда")
        active_quote_offset = self._trade_quote_offset(trade)
        if trade and abs(active_quote_offset) > 0:
            warnings.append(
                f"к ценовому ряду применён basis {active_quote_offset:+.4f}, "
                "зафиксированный при открытии сделки")
        if inst.proxy_experimental:
            warnings.append(
                "option-proxy экспериментальный: используйте только как "
                "сценарный контекст, не как точную цену инструмента")
        if age is not None and age > 1800:
            warnings.append("опционный снимок старше 30 минут")
        if quoted_proxy_spot is None and inst.options_proxy:
            warnings.append(
                "нет живой цены option-proxy; динамическая moneyness использует "
                "spot последнего снимка")
        elif (quoted_proxy_spot is not None
              and proxy_quote_status == "delayed"):
            warnings.append(
                "option-proxy обновляется REST-котировкой indicative/delayed, "
                "а не живым stream-тиком")
        if barriers_supported is False and trade_dist:
            warnings.append(
                "стоп или тейк лежит за доступной сеткой страйков; option edge "
                "выключен, визуальный fallback сохранён")
        elif tail_anchor_supported is False and trade_dist:
            warnings.append(
                "суммарная BL-масса за барьерами слишком мала для устойчивого "
                "tail-ratio; option edge выключен")

        anchor_status = (
            "option_anchored_live" if anchor_ready and live_proxy_mapping else
            "option_anchored_indicative" if anchor_ready and dynamic_mapping else
            "option_anchored_snapshot" if anchor_ready else
            "fallback"
        )

        features = {
            "probability_lattice": {
                "status": anchor_status,
                "decision_weight": decision_ready,
                "driver": ("BL density + barrier MC + live instrument/proxy ticks"
                           if anchor_ready and live_proxy_mapping else
                           "BL snapshot + indicative proxy mapping"
                           if anchor_ready and dynamic_mapping else
                           "BL snapshot + barrier MC"
                           if anchor_ready else
                           "scenario diffusion only; headline probability disabled"),
            },
            "strike_landscape": {
                "status": (
                    "ready_live" if data_ready and live_proxy_mapping else
                    "ready_indicative" if data_ready and dynamic_mapping else
                    "ready_snapshot" if data_ready else "no_data"),
                "decision_weight": decision_ready,
                "driver": "mapped BL densities and max-OI context",
            },
            "probability_cone_3d": {
                "status": anchor_status,
                "decision_weight": decision_ready,
                "driver": "implied move + BL terminal tail + skew/term structure",
            },
            "probability_fan_2d": {
                "status": anchor_status,
                "decision_weight": decision_ready,
                "driver": "readable slice of the same cone",
            },
            "iv_surface_3d": {
                "status": ("snapshot_plus_live_spot"
                           if self.market.iv_surface.get("value")
                           and live_proxy_mapping
                           else "snapshot_plus_indicative_spot"
                           if self.market.iv_surface.get("value")
                           and quoted_proxy_spot is not None
                           else "snapshot_only"
                           if self.market.iv_surface.get("value") else "no_data"),
                "decision_weight": False,
                "driver": "delayed IV snapshot; quote-driven moneyness only",
            },
            "gex": {
                "status": ("disabled_inverse_proxy" if inverse else
                           "context_only" if m else "no_data"),
                "decision_weight": False,
                "driver": "open-interest sign heuristic; dealer positions unobserved",
            },
            "volatility_filters": {
                "status": "context_only",
                "decision_weight": False,
                "driver": "Yahoo 15m delayed context, not full strategy sequence",
            },
        }
        return clean_nans({
            "instrument": {
                "code": inst.code,
                "price_ticker": inst.yahoo,
                "price_label": inst.price_label or inst.yahoo,
                "option_proxy": inst.options_proxy,
                "proxy_transform": inst.proxy_transform,
                "proxy_quality": ("experimental" if inst.proxy_experimental
                                  else "reference"),
            },
            "quote": {
                "raw": raw,
                "effective": effective,
                "basis_offset": self._trade_quote_offset(trade),
                "source": self.market.price.get("source"),
                "status": self.market.price.get("status"),
                "derived": bool(self.market.price.get("derived")),
                "driver_ticker": self.market.price.get("driver_ticker"),
                "driver_experimental": bool(
                    self.market.price.get("driver_experimental")),
                "anchor_age_sec": self.market.price.get("anchor_age_sec"),
                "fresh": self.market.price.get("fresh"),
                "idle_secs": self.market.price.get("idle_secs"),
            },
            "options": {
                "status": self.market.chain.get("status"),
                "source": self.market.chain.get("source"),
                "expiry": (m or {}).get("expiry"),
                "snapshot_spot": (m or {}).get("spot"),
                "proxy_spot_current": proxy_spot,
                "proxy_quote": quoted_proxy_spot,
                "proxy_quote_status": proxy_quote_status,
                "uses_snapshot_spot_fallback": quoted_proxy_spot is None,
                "snapshot_age_sec": age,
                "density_ready": has_density,
                "barriers_supported": barriers_supported,
                "tail_anchor_supported": tail_anchor_supported,
                "terminal_tail_mass": (
                    trade_dist.get("tail_mass") if trade_dist else None),
                "dynamic_mapping_ready": dynamic_mapping,
                "live_mapping_ready": live_proxy_mapping,
                "option_anchor_ready": anchor_ready,
            },
            "features": features,
            "warnings": warnings,
        })

    def close(self) -> None:
        self.cache.close()
        self.journal.close()
