"""Compact, stateful AI review of the active trade."""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .strategy_playbooks import PLAYBOOKS as SETUP_PLAYBOOKS


SYSTEM_PROMPT = """Ты — понятный диспетчер УЖЕ ОТКРЫТОЙ сделки Seiltanzer.
Входовой сетап уже принят трейдером: НЕ пересказывай FVG/AMD/Block/Fib и не
переоценивай вход. Из стратегии используй только лестницу фиксаций, БУ и trailing.

Внутренне проверь ВСЕ группы evidence_matrix: option first-touch/NO-TOUCH/barrier
EV/RND/IV/skew/term, live tape/ATR/уровни, корреляции, OI/GEX, время и качество.
Решение сверяй с manager_frame и scenario_frame. Наружу переводи профессиональные
метрики на обычный русский. Термин допустим только после смысла в скобках.
Например: «рынок ждёт боковик до опционного горизонта (NO-TOUCH 82%)», а не просто
«NO-TOUCH 82%». Низкий pTake при большом NO-TOUCH не называй провалом сделки.

Цель ответа: объяснить текущее состояние, изменение со прошлого разбора, влияние
времени и конкретное управление. Не пиши общие советы. Не выдумывай уровни.
Не расширяй стоп, не усредняй убыток. Лестница по 10%; БУ/trailing только после
1.5R. Delayed/proxy понижает уверенность, но не отменяет рабочий план.

Ответ 180–260 слов, строго:
СОСТОЯНИЕ — короткое русское название фазы и что происходит со сделкой.
ИЗМЕНИЛОСЬ — 1–2 значимых изменения с прошлого разбора; если их нет — так и скажи.
ВРЕМЯ — сколько сделка открыта, сессия, что означает оставшееся опционное окно.
ЧТО ДЕЛАТЬ — одно действие сейчас, следующий рубеж менеджмента и что пока запрещено.
ПОЧЕМУ — 2–3 фактора простыми словами; технические значения только в скобках.
ПЛАН — три строки: «Продолжение», «Зависание», «Ухудшение»: понятное событие → действие.
СЛЕДУЮЩАЯ ПРОВЕРКА — конкретное событие, не произвольное время; затем одна проблема данных.

Запрещены слова без расшифровки: baseline, setup_guard, action, live phase, option mode.
Не используй null/legacy edge. Не повторяй входовой сетап."""


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _rnd(value: Any, digits: int = 4) -> float | None:
    value = _num(value)
    return round(value, digits) if value is not None else None


def _at(value: Any, *path: str, default=None):
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _level_r(level: Any, trade: dict) -> float | None:
    level = _num(level)
    entry, stop = _num(trade.get("entry")), _num(trade.get("stop"))
    if level is None or entry is None or stop is None or entry == stop:
        return None
    sign = 1.0 if trade.get("direction") == "long" else -1.0
    return round(sign * (level - entry) / abs(entry - stop), 3)


def _forecast_history(engine, trade_id: int) -> dict:
    rows = engine.journal.option_forecast_history(trade_id, limit=120)
    if not rows:
        return {"samples": 0}
    first, latest = rows[0], rows[-1]
    previous = rows[-2] if len(rows) > 1 else first
    keys = ("r", "p_take", "p_stop", "p_unresolved", "option_ev")

    def delta(key: str, left: dict, right: dict):
        a, b = _num(left.get(key)), _num(right.get(key))
        return round(a - b, 4) if a is not None and b is not None else None

    elapsed = max((_num(latest.get("ts")) or 0) - (_num(first.get("ts")) or 0), 0)

    def point(row: dict) -> dict:
        return {k: _rnd(row.get(k)) for k in keys}

    def window(minutes: int) -> dict:
        target = (_num(latest.get("ts")) or 0) - minutes * 60
        base = min(rows, key=lambda row: abs((_num(row.get("ts")) or 0) - target))
        return {k: delta(k, latest, base) for k in keys}

    return {
        "samples": len(rows),
        "minutes": round(elapsed / 60, 1),
        "open": point(first), "previous": point(previous), "current": point(latest),
        "delta_from_open": {k: delta(k, latest, first) for k in keys},
        "delta_from_previous": {k: delta(k, latest, previous) for k in keys},
        "delta_windows": {"5m": window(5), "15m": window(15), "60m": window(60)},
        "chain_age_sec": _rnd(latest.get("chain_age_sec"), 1),
        "source": latest.get("source"),
    }


def _time_context(tick: dict, trade: dict, previous_reviews: list[dict]) -> dict:
    now_ts = _num(tick.get("ts")) or time.time()
    local_tz_name = os.environ.get("APP_TIMEZONE", "Europe/Athens")
    local_tz = ZoneInfo(local_tz_name)
    now_utc = datetime.fromtimestamp(now_ts, timezone.utc)
    now_local = now_utc.astimezone(local_tz)
    opened = datetime.fromtimestamp(float(trade["opened_at"]), timezone.utc)
    market_tz = ZoneInfo("America/New_York")
    ny = now_utc.astimezone(market_tz)
    regular_open = datetime.combine(ny.date(), dt_time(9, 30), market_tz)
    regular_close = datetime.combine(ny.date(), dt_time(16, 0), market_tz)
    weekday = ny.weekday() < 5
    if weekday and regular_open <= ny <= regular_close:
        session = "US regular open"
        session_minutes = round((regular_close - ny).total_seconds() / 60)
        session_event = "to_close"
    elif weekday and ny < regular_open:
        session = "US premarket"
        session_minutes = round((regular_open - ny).total_seconds() / 60)
        session_event = "to_open"
    else:
        session = "US post/closed"
        next_open = regular_open + timedelta(days=1)
        while next_open.weekday() >= 5:
            next_open += timedelta(days=1)
        session_minutes = round((next_open - ny).total_seconds() / 60)
        session_event = "to_next_open"
    market = tick.get("market") or {}
    horizon_years = _num(market.get("horizon_years"))
    median_years = _num(market.get("median_years"))
    horizon_minutes = horizon_years * 365 * 24 * 60 if horizon_years else None
    last_review_ts = _num(previous_reviews[-1].get("ts")) if previous_reviews else None
    return {
        "captured_local": now_local.isoformat(timespec="seconds"),
        "timezone": local_tz_name,
        "opened_local": opened.astimezone(local_tz).isoformat(timespec="seconds"),
        "minutes_open": round((now_ts - float(trade["opened_at"])) / 60, 1),
        "minutes_since_review": round((now_ts - last_review_ts) / 60, 1) if last_review_ts else None,
        "session": session, "session_event": session_event,
        "session_minutes": session_minutes,
        "option_horizon_minutes": _rnd(horizon_minutes, 1),
        "median_touch_minutes": _rnd(median_years * 365 * 24 * 60, 1) if median_years else None,
        "trade_age_to_forward_horizon": _rnd(
            (now_ts - float(trade["opened_at"])) / 60 / horizon_minutes, 2)
            if horizon_minutes else None,
    }


def _price_tape(engine, tick: dict, trade: dict) -> dict:
    points = [x for x in getattr(engine.market, "intraday", []) if len(x) >= 2]
    points = [(float(x[0]), float(x[1])) for x in points
              if _num(x[0]) is not None and _num(x[1]) is not None]
    current = _num(_at(tick, "feeds", "price", "value"))
    risk = abs((_num(trade.get("entry")) or 0) - (_num(trade.get("stop")) or 0))
    if not points or current is None:
        return {"samples": len(points), "available": False}
    offset = current - points[-1][1]
    aligned = [(ts, price + offset) for ts, price in points[-60:]]

    def move(n: int):
        start = aligned[max(0, len(aligned) - n)][1]
        delta = current - start
        return {"points": round(delta, 4),
                "r": round(delta / risk, 3) if risk > 0 else None}

    recent = aligned[-20:]
    ups = sum(recent[i][1] > recent[i - 1][1] for i in range(1, len(recent)))
    downs = sum(recent[i][1] < recent[i - 1][1] for i in range(1, len(recent)))
    signed = move(12).get("r")
    if trade.get("direction") == "short" and signed is not None:
        signed = -signed
    return {
        "available": True, "samples": len(points),
        "short": move(12), "medium": move(40),
        "directional_short_r": _rnd(signed, 3),
        "up_ticks": ups, "down_ticks": downs,
        "range_points": round(max(p for _, p in recent) - min(p for _, p in recent), 4),
        "last_span_min": round((recent[-1][0] - recent[0][0]) / 60, 1) if len(recent) > 1 else 0,
    }


def _iv_surface_summary(payload: dict) -> dict:
    rows = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"available": False, "status": _at(payload, "status")}
    summaries = []
    for row in rows[:3]:
        strikes, ivs = row.get("strikes") or [], row.get("ivs") or []
        spot = _num(row.get("spot_at_snapshot"))
        pairs = [(float(k), float(v)) for k, v in zip(strikes, ivs)
                 if _num(k) is not None and _num(v) is not None and float(v) > 0]
        if not pairs or spot is None:
            continue

        def nearest(target):
            return min(pairs, key=lambda p: abs(p[0] - target))[1]

        atm, left, right = nearest(spot), nearest(spot * 0.96), nearest(spot * 1.04)
        summaries.append({
            "days": _rnd(row.get("days"), 2), "expiry": row.get("expiry"),
            "atm_iv": round(atm, 4), "left_wing_iv": round(left, 4),
            "right_wing_iv": round(right, 4),
            "wing_skew": round(right - left, 4),
            "curvature": round((left + right) / 2 - atm, 4),
        })
    snapshot_spot = _num(rows[0].get("spot_at_snapshot")) if rows else None
    live_spot = _num(payload.get("spot_current"))
    return {
        "available": bool(summaries), "status": payload.get("status"),
        "source": payload.get("source"), "age_sec": (
            round(time.time() - float(payload["ts"]), 1) if _num(payload.get("ts")) else None),
        "live_moneyness_shift": (
            round(live_spot / snapshot_spot - 1, 5)
            if live_spot and snapshot_spot else None),
        "expiries": summaries,
    }


def _correlation_summary(payload: dict) -> dict:
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {"available": False, "status": _at(payload, "status")}
    assets = value.get("assets") or []
    short = value.get("matrix_short") or value.get("matrix") or []
    base = value.get("matrix_baseline") or []
    delta = value.get("matrix_delta") or []
    pairs = []
    for left, right in (("NAS", "VXN"), ("SP500", "VIX"),
                        ("GOLD", "GVZ"), ("NAS", "SP500")):
        if left not in assets or right not in assets:
            continue
        i, j = assets.index(left), assets.index(right)
        pairs.append({
            "pair": f"{left}-{right}",
            "rolling": _rnd(short[i][j] if i < len(short) and j < len(short[i]) else None, 3),
            "baseline": _rnd(base[i][j] if i < len(base) and j < len(base[i]) else None, 3),
            "delta": _rnd(delta[i][j] if i < len(delta) and j < len(delta[i]) else None, 3),
        })
    return {"available": bool(pairs), "status": payload.get("status"),
            "source": payload.get("source"), "pairs": pairs}


def _ridge_summary(ridge: dict, trade: dict) -> dict:
    if not ridge.get("available"):
        return {"available": False, "reason": ridge.get("reason")}
    snaps = ridge.get("snapshots") or []
    latest = snaps[-1] if snaps else {}
    previous = snaps[-2] if len(snaps) > 1 else {}
    latest_skew = _at(latest, "skew", "rr")
    previous_skew = _at(previous, "skew", "rr")
    skew_delta = (_num(latest_skew) - _num(previous_skew)
                  if _num(latest_skew) is not None and _num(previous_skew) is not None else None)
    walls = ridge.get("oi_walls") or {}
    return {
        "available": True, "proxy": ridge.get("proxy"),
        "snapshots": len(snaps), "rn_tail": ridge.get("rn_probs"),
        "implied_move_frac": _rnd(_at(latest, "implied_move", "move_frac")),
        "skew": latest.get("skew"), "skew_delta_snapshot": _rnd(skew_delta),
        "term": latest.get("term"),
        "oi_walls": {
            "call": _rnd(walls.get("call_wall")),
            "put": _rnd(walls.get("put_wall")),
            "call_r": _level_r(walls.get("call_wall"), trade),
            "put_r": _level_r(walls.get("put_wall"), trade),
        },
        "gex_context": {
            "available": _at(latest, "gex", "available", default=bool(latest.get("gex"))),
            "zero_flip": _rnd(_at(latest, "gex", "zero_flip")),
            "top": _at(latest, "gex", "top", default=[]),
            "authority": "heuristic_context_only",
        },
    }


def _strategy(engine, trade: dict) -> dict:
    from .config import SETUPS
    cfg = SETUPS.get(int(trade.get("setup") or 0))
    if not cfg:
        return {}
    playbook = SETUP_PLAYBOOKS.get(cfg.num, {})
    stats = engine.journal.setup_stats(cfg.num, engine.settings.journal_min_trades)
    return {
        "setup": cfg.num, "name": cfg.name, "instrument": cfg.instrument,
        "direction": trade.get("direction"), "source": "Strategy Premium Edition (2).pdf",
        "playbook": playbook,
        "manual_structure_required": True,
        "manual_structure_status": "available" if trade.get("zones") else "not_supplied",
        "required_filters": list(cfg.filters), "target_rr": cfg.rr,
        "stats": stats.__dict__,
        "management": {
            "take_fraction_each_rung": 0.10,
            "rungs_r": [1.0, 1.25, 1.5, 1.75, 2.0, 2.2],
            "breakeven_after_r": 1.5,
            "trailing": "after 1.5R: 5m; profitable cross halves remainder then 15m; losing 5m cross exits remainder",
            "forbidden": ["widen_stop", "average_loss", "invent_early_exit"],
        },
    }


def _observation(engine, tick: dict, ridge: dict, trade: dict) -> dict:
    state = tick.get("state") or {}
    prob = tick.get("prob") or {}
    market = tick.get("market") or {}
    cone = tick.get("cone") or {}
    gamma = tick.get("gamma") or {}
    levels = tick.get("levels") or {}
    vp = levels.get("volume_profile") or {}
    quote = _at(tick, "feeds", "price", default={}) or {}
    chain = _at(tick, "feeds", "chain", default={}) or {}
    max_r = _num(_at(tick, "ladder", "max_r"))
    r_now = _num(prob.get("r"))
    return {
        "position": {
            "price": _rnd(quote.get("value")), "r": _rnd(r_now, 3),
            "max_r": _rnd(max_r, 3),
            "pullback_from_max_r": _rnd(r_now - max_r, 3) if r_now is not None and max_r is not None else None,
            "to_take_r": _rnd(state.get("to_take_r"), 3),
            "to_stop_r": _rnd(state.get("to_stop_r"), 3),
            "target_r": _rnd(prob.get("T"), 3),
            "to_take_atr": _rnd(state.get("to_take_atr"), 2),
            "to_stop_atr": _rnd(state.get("to_stop_atr"), 2),
            "minutes_open": round((time.time() - float(trade["opened_at"])) / 60, 1),
            "price_tape": _price_tape(engine, tick, trade),
        },
        "exact_levels": {
            "entry": _rnd(trade.get("entry")), "stop": _rnd(trade.get("stop")),
            "take": _rnd(trade.get("take")), "current": _rnd(quote.get("value")),
        },
        "option_probability": {
            "available": prob.get("source") == "options_barrier_first_touch",
            "p_take_first": _rnd(prob.get("p")), "scenario_band": [_rnd(prob.get("p_lo")), _rnd(prob.get("p_hi"))],
            "barrier_ev_r": _rnd(market.get("horizon_barrier_ev")
                                  if market.get("horizon_barrier_ev") is not None
                                  else market.get("option_ev")),
            "no_touch_horizon": _rnd(market.get("p_unresolved_horizon")),
            "touch_take_horizon": _rnd(market.get("p_take_horizon")),
            "touch_stop_horizon": _rnd(market.get("p_stop_horizon")),
            "raw_reached_take": _rnd(market.get("p_take_reached_horizon")),
            "raw_reached_stop": _rnd(market.get("p_stop_reached_horizon")),
            "raw_unresolved": _rnd(market.get("p_unresolved_raw_horizon")),
            "anchor_reason": market.get("anchor_reason"),
            "quality": market.get("quality"), "chain_age_sec": _rnd(market.get("chain_age_sec"), 1),
        },
        "probability_cone": {
            "option_anchored": cone.get("option_anchored"),
            "mode_r": _rnd(market.get("scenario_mode_r"), 3),
            "q10_r": _rnd(market.get("scenario_p10_r"), 3),
            "median_r": _rnd(market.get("scenario_median_r"), 3),
            "q90_r": _rnd(market.get("scenario_p90_r"), 3),
            "alive_mass": _rnd(market.get("scenario_slice_alive")),
            "slice_time_frac": _rnd(market.get("scenario_slice_time_frac")),
            "touch_take_horizon": _rnd(market.get("p_take_horizon")),
            "touch_stop_horizon": _rnd(market.get("p_stop_horizon")),
            "no_touch_horizon": _rnd(market.get("p_unresolved_horizon")),
            "raw_reached_take": _rnd(market.get("p_take_reached_horizon")),
            "raw_reached_stop": _rnd(market.get("p_stop_reached_horizon")),
            "rv_iv_ratio": _rnd(cone.get("rv_iv_ratio")),
        },
        "lattice": {
            "barrier_take_first": _rnd(prob.get("p")),
            "ev_hold": _rnd(_at(tick, "mc", "ev_hold")),
            "execution_control_ev": _rnd(_at(tick, "mc", "ev_ladder")),
            "distribution": {
                "mode_r": _rnd(market.get("scenario_mode_r"), 3),
                "p10_r": _rnd(market.get("scenario_p10_r"), 3),
                "median_r": _rnd(market.get("scenario_median_r"), 3),
                "p90_r": _rnd(market.get("scenario_p90_r"), 3),
            },
        },
        "strike_landscape": _ridge_summary(ridge, trade),
        "iv_surface": _iv_surface_summary(tick.get("iv_surface") or {}),
        "gamma": {
            "available": gamma.get("available"), "zone": gamma.get("zone"),
            "magnet_r": _rnd(gamma.get("magnet_r"), 3), "strength": _rnd(gamma.get("strength")),
            "toward": gamma.get("toward"), "flip_r": _level_r(gamma.get("flip"), trade),
            "authority": "heuristic_context_only",
        },
        "levels": {
            "vwap_r": _level_r(levels.get("vwap"), trade),
            "day_low_r": _level_r(levels.get("day_low"), trade),
            "day_high_r": _level_r(levels.get("day_high"), trade),
            "implied_low_r": _level_r(_at(levels, "implied_band", "low"), trade),
            "implied_high_r": _level_r(_at(levels, "implied_band", "high"), trade),
            "poc_r": _level_r(vp.get("poc"), trade),
            "value_area_low_r": _level_r(vp.get("value_area_low"), trade),
            "value_area_high_r": _level_r(vp.get("value_area_high"), trade),
            "volume_kind": "TPO" if vp.get("is_tpo") else "volume",
            "fvg_zones": trade.get("zones") or [],
        },
        "volatility": {
            "atr": tick.get("atr"), "regime": tick.get("regime"),
            "sigma": tick.get("sigma"), "vrp": tick.get("vrp"),
            "skew": _at(tick, "options_summary", "skew"),
            "term": _at(tick, "options_summary", "term"),
        },
        "correlation": _correlation_summary(tick.get("correlation") or {}),
        "filters": tick.get("filters") or [],
        "execution": tick.get("ladder"),
        "feed_quality": {
            "price": {k: quote.get(k) for k in ("status", "source", "age_sec", "error", "derived")},
            "chain": {k: chain.get(k) for k in ("status", "source", "age_sec", "error", "delay_hint_sec")},
        },
    }


def _tone(value: float | None, good: float = 0.03, bad: float = -0.03) -> str:
    if value is None:
        return "unknown"
    return "support" if value >= good else "against" if value <= bad else "neutral"


def _evidence_matrix(strategy: dict, obs: dict, history: dict, timing: dict) -> dict:
    op = obs["option_probability"]
    cone = obs["probability_cone"]
    pos = obs["position"]
    tape = pos.get("price_tape") or {}
    hist_delta = history.get("delta_from_previous") or {}
    chain = obs["feed_quality"]["chain"]
    manual = strategy.get("manual_structure_status")
    return {
        "options_primary": {
            "status": _tone(_num(op.get("barrier_ev_r"))),
            "take_stop_no_touch": [op.get("touch_take_horizon"), op.get("touch_stop_horizon"), op.get("no_touch_horizon")],
            "barrier_ev_r": op.get("barrier_ev_r"),
            "delta": {k: hist_delta.get(k) for k in ("p_take", "p_stop", "p_unresolved", "option_ev")},
            "rnd_r": {k: cone.get(k) for k in ("q10_r", "mode_r", "median_r", "q90_r")},
            "iv_surface": obs.get("iv_surface"), "skew_term": obs.get("volatility"),
        },
        "live_price": {
            "status": _tone(_num(tape.get("directional_short_r")), 0.05, -0.05),
            "r": pos.get("r"), "max_r": pos.get("max_r"),
            "pullback_r": pos.get("pullback_from_max_r"), "tape": tape,
            "atr_regime": obs.get("volatility", {}).get("atr"),
        },
        "levels_structure": {
            "status": "manual_unknown" if manual != "available" else "available",
            "levels_r": obs.get("levels"),
            "warning": "FVG/AMD/Block/Fib not machine-confirmed" if manual != "available" else None,
        },
        "cross_asset": obs.get("correlation"),
        "oi_gamma_context": {"strike": obs.get("strike_landscape"), "gamma": obs.get("gamma")},
        "execution_time": {
            "session": timing.get("session"), "minutes_open": timing.get("minutes_open"),
            "horizon_minutes": timing.get("option_horizon_minutes"),
            "median_touch_minutes": timing.get("median_touch_minutes"),
            "current_r": pos.get("r"), "execution": obs.get("execution"),
            "be_rule": "only after 1.5R",
        },
        "data_quality": {
            "price": obs["feed_quality"]["price"], "chain": chain,
            "option_anchor": op.get("available"), "proxy_quality": op.get("quality"),
            "manual_structure": manual,
        },
    }


def _scenario_frame(strategy: dict, obs: dict, history: dict, timing: dict) -> dict:
    op, cone, pos = obs["option_probability"], obs["probability_cone"], obs["position"]
    p_take, p_stop, no_touch = (op.get("touch_take_horizon"), op.get("touch_stop_horizon"),
                                op.get("no_touch_horizon"))
    current_r = _num(pos.get("r"))
    ev = _num(op.get("barrier_ev_r"))
    next_rung = next((x for x in strategy.get("management", {}).get("rungs_r", [])
                      if current_r is None or x > current_r + 1e-6), None)
    center = _num(cone.get("median_r"))
    if center is None:
        center = _num(cone.get("mode_r"))
    p_take_up = round(min((p_take or 0) + 0.01, 1.0), 4) if p_take is not None else None
    p_stop_up = round(min((p_stop or 0) + 0.01, 1.0), 4) if p_stop is not None else None
    ev_up = round(max(ev + 0.03, 0.03), 4) if ev is not None else None
    ev_down = round(min(ev - 0.03, -0.03), 4) if ev is not None else None
    stall_mass = max(no_touch or 0.0, 0.70)
    ev_up_text = f"{ev_up:+.2f}R" if ev_up is not None else "положительного значения"
    ev_down_text = f"{ev_down:.2f}R" if ev_down is not None else "отрицательного значения"
    p_take_text = f"{p_take_up * 100:.1f}%" if p_take_up is not None else "значимого роста"
    p_stop_text = f"{p_stop_up * 100:.1f}%" if p_stop_up is not None else "значимого роста"
    center_text = f"{center:.2f}R" if center is not None else "центра распределения"
    protect = ("активировать БУ и trailing по правилам" if current_r is not None and current_r >= 1.5
               else "сохранить исходный стоп; БУ/trailing ещё не активировать")
    return {
        "baseline": {"barrier_ev_r": ev, "p_take": p_take, "p_stop": p_stop,
                     "no_touch": no_touch, "r": current_r, "rnd_center_r": center},
        "A_continuation": {
            "meaning": "рынок переходит от ожидания к продолжению в сторону тейка",
            "trigger_plain": (f"опционный баланс становится положительным минимум на {ev_up_text}, шанс касания тейка "
                              f"в текущем окне растёт до {p_take_text}; цена удерживается выше {center_text}"),
            "action_plain": (f"удерживать; снять очередные 10% на {next_rung}R"
                             if next_rung else "вести остаток по активированному trailing"),
        },
        "B_stall": {
            "meaning": "прибыль сохраняется, но рынок не показывает ускорения",
            "trigger_plain": (f"масса внутри коридора остаётся/растёт до {stall_mass * 100:.1f}%, "
                              f"цена остаётся около {center_text} без прогресса 0.20R"),
            "action_plain": "удерживать без добавления; ничего внепланово не фиксировать; пересчитать после новой цепочки",
        },
        "C_deterioration": {
            "meaning": "зависание превращается в реальное ухудшение, а не обычный шум",
            "trigger_plain": (f"опционный баланс становится отрицательным до {ev_down_text} или риск касания стопа растёт до {p_stop_text}; "
                              f"одновременно цена уходит ниже {center_text} и короткий поток направлен против сделки"),
            "action_plain": f"не добавлять и не усреднять; {protect}",
        },
        "next_review_events": [
            "новая опционная цепочка", "сдвиг цены на 0.20R от этого разбора",
            "переход цены через центр опционного распределения", "пересечение OI-wall или gamma flip",
        ],
        "time_note": {
            "session": timing.get("session"), "horizon_minutes": timing.get("option_horizon_minutes"),
            "minutes_open": timing.get("minutes_open"),
        },
    }


def _decision_frame(strategy: dict, obs: dict, history: dict, timing: dict) -> dict:
    op, pos = obs["option_probability"], obs["position"]
    p_take, p_stop, no_touch = map(_num, (
        op.get("touch_take_horizon"), op.get("touch_stop_horizon"), op.get("no_touch_horizon")))
    ev = _num(op.get("barrier_ev_r"))
    if not op.get("available"):
        option_regime = "no_option_anchor"
    elif no_touch is not None and no_touch >= max(p_take or 0, p_stop or 0):
        option_regime = "stall_dominant"
    else:
        option_regime = _tone(ev)
    tape_r = _num((pos.get("price_tape") or {}).get("directional_short_r"))
    pullback = _num(pos.get("pullback_from_max_r"))
    live_phase = ("adverse_impulse" if tape_r is not None and tape_r < -0.08
                  else "positive_impulse" if tape_r is not None and tape_r > 0.08
                  else "pullback" if pullback is not None and pullback < -0.20
                  else "rotation")
    required = [x for x in obs.get("filters", []) if x.get("required")]
    failed = [x.get("key") for x in required if x.get("state") == "fail"]
    manual = [x.get("key") for x in required if x.get("state") == "manual"]
    chain = obs["feed_quality"]["chain"]
    constraints = []
    if strategy.get("manual_structure_status") != "available":
        constraints.append("manual FVG/AMD/Block/Fib state not supplied")
    if chain.get("status") not in ("ok", "demo"):
        constraints.append(f"option chain {chain.get('status')}")
    if op.get("quality") == "experimental":
        constraints.append("experimental option proxy")
    if manual:
        constraints.append("manual filters: " + ",".join(str(x) for x in manual))
    return {
        "option_regime": option_regime, "live_phase": live_phase,
        "required_filter_failures": failed, "confidence_constraints": constraints,
        "since_last_review": history.get("since_last_ai_review"),
        "time_pressure": {
            "minutes_open": timing.get("minutes_open"),
            "forward_option_horizon_minutes": timing.get("option_horizon_minutes"),
            "session_event": timing.get("session_event"),
            "session_minutes": timing.get("session_minutes"),
        },
        "decision_rule": "structure sets validity; options rank continuation/stall/deterioration; live data times management",
    }


def _manager_frame(strategy: dict, obs: dict, history: dict, timing: dict) -> dict:
    """Детерминированный перевод метрик в язык сопровождения сделки."""
    op, cone, pos = obs["option_probability"], obs["probability_cone"], obs["position"]
    ev = _num(op.get("barrier_ev_r"))
    p_take = _num(op.get("touch_take_horizon"))
    p_stop = _num(op.get("touch_stop_horizon"))
    no_touch = _num(op.get("no_touch_horizon"))
    r_now = _num(pos.get("r"))
    max_r = _num(pos.get("max_r"))
    mode = _num(cone.get("mode_r"))
    median = _num(cone.get("median_r"))
    center_values = [x for x in (mode, median) if x is not None]
    center = sum(center_values) / len(center_values) if center_values else None
    tape_r = _num((pos.get("price_tape") or {}).get("directional_short_r"))
    change = history.get("since_last_ai_review") or {}
    d_r, d_ev = _num(change.get("r")), _num(change.get("barrier_ev_r"))
    d_take, d_stop = _num(change.get("p_take")), _num(change.get("p_stop"))
    d_center = _num(change.get("median_r"))
    adverse_live = tape_r is not None and tape_r <= -0.08
    positive_live = tape_r is not None and tape_r >= 0.08
    center_near = (center is not None and r_now is not None and abs(center - r_now) <= 0.30)

    if not op.get("available"):
        state_code, state_name = "data_limited", "ДАННЫХ НЕДОСТАТОЧНО"
        state_explanation = "опционная модель сейчас не имеет валидной привязки; остаётся только live-контекст"
    elif ev is not None and ev < -0.03 and (adverse_live or (d_ev is not None and d_ev < -0.02)
                                            or (d_stop is not None and d_stop > 0.01)):
        state_code, state_name = "deteriorating", "СДЕЛКА УХУДШАЕТСЯ"
        state_explanation = "опционный перекос и текущий поток одновременно смещаются против позиции"
    elif no_touch is not None and no_touch >= 0.70 and center_near:
        state_code, state_name = "stall", "ЗАВИСАНИЕ"
        state_explanation = "прибыль удерживается, но рынок пока не подтверждает ускорение к тейку"
    elif ev is not None and ev >= 0.03 and (positive_live or (d_center is not None and d_center > 0.04)):
        state_code, state_name = "advancing", "ПРОДОЛЖЕНИЕ"
        state_explanation = "опционный центр и текущий поток подтверждают движение в сторону тейка"
    elif max_r is not None and r_now is not None and max_r - r_now >= 0.20:
        state_code, state_name = "pullback", "ОТКАТ ВНУТРИ СДЕЛКИ"
        state_explanation = "цена отдала часть достигнутого R, но подтверждённого опционного ухудшения ещё нет"
    else:
        state_code, state_name = "balanced", "БАЛАНС"
        state_explanation = "сильного подтверждения продолжения или ухудшения пока нет"

    changes = []
    if d_r is not None and abs(d_r) >= 0.03:
        changes.append(f"результат сделки {'вырос' if d_r > 0 else 'снизился'} на {abs(d_r):.2f}R")
    if d_ev is not None and abs(d_ev) >= 0.01:
        changes.append(f"опционный перекос {'улучшился' if d_ev > 0 else 'ухудшился'} на {abs(d_ev):.2f}R")
    if d_take is not None and abs(d_take) >= 0.005:
        changes.append(f"шанс касания тейка в текущем окне {'вырос' if d_take > 0 else 'снизился'} на {abs(d_take) * 100:.1f} п.п.")
    if d_stop is not None and abs(d_stop) >= 0.005:
        changes.append(f"риск касания стопа в текущем окне {'вырос' if d_stop > 0 else 'снизился'} на {abs(d_stop) * 100:.1f} п.п.")
    if not changes:
        changes = ["значимого изменения относительно прошлого разбора нет"]

    rungs = strategy.get("management", {}).get("rungs_r", [])
    next_rung = next((x for x in rungs if r_now is None or x > r_now + 1e-6), None)
    if r_now is not None and r_now >= 1.5:
        protection = "порог 1.5R пройден: активировать БУ и trailing по правилам"
    else:
        protection = "до 1.5R не переносить стоп в БУ и не включать trailing"
    if state_code == "deteriorating":
        action = (f"не добавлять позицию; {protection}" if r_now is not None and r_now >= 1.5
                  else f"не добавлять позицию; сохранить исходный стоп; {protection}")
    elif state_code == "data_limited":
        action = f"не менять позицию по неполным данным; дождаться валидной цепочки; {protection}"
    else:
        action = f"удерживать без добавления; {protection}"
    if next_rung is not None:
        action += f"; следующий плановый рубеж — {next_rung}R, фиксация 10%"

    if ev is None:
        option_meaning = "направленный опционный перекос определить нельзя"
    elif ev >= 0.10:
        option_meaning = f"опционный перекос заметно в пользу сделки (+{ev:.2f}R)"
    elif ev >= 0.03:
        option_meaning = f"опционный перекос слабо в пользу сделки (+{ev:.2f}R), но сам по себе не подтверждает импульс"
    elif ev <= -0.03:
        option_meaning = f"опционный перекос против сделки ({ev:.2f}R)"
    else:
        option_meaning = f"опционный перекос практически нейтрален ({ev:+.2f}R)"
    if no_touch is not None and no_touch >= 0.50:
        horizon_meaning = (f"до конца текущего опционного окна рынок оценивает вероятность не коснуться ни тейка, "
                           f"ни стопа в {no_touch * 100:.1f}% — это ожидание паузы, а не вероятность проигрыша")
    elif no_touch is not None:
        horizon_meaning = (f"в текущем опционном окне касание одного из барьеров вероятнее паузы: "
                           f"тейк {((p_take or 0) * 100):.1f}%, стоп {((p_stop or 0) * 100):.1f}%, "
                           f"без касания {no_touch * 100:.1f}%")
    else:
        horizon_meaning = "оценка попадания в стоп/тейк к горизонту недоступна"
    center_meaning = (f"центр ожидаемого распределения около {center:.2f}R"
                      + (f", текущая цена {r_now:.2f}R" if r_now is not None else "")
                      if center is not None else "центр распределения недоступен")

    hours_open = (_num(timing.get("minutes_open")) or 0) / 60
    horizon_minutes = _num(timing.get("option_horizon_minutes"))
    session_ru = {"US regular open": "основная сессия США",
                  "US premarket": "премаркет США", "US post/closed": "сессия США закрыта"}.get(
                      timing.get("session"), timing.get("session"))
    time_meaning = f"сделка открыта {hours_open:.1f} ч; сейчас {session_ru}"
    if horizon_minutes is not None:
        time_meaning += (f"; текущая опционная оценка смотрит ещё примерно на {horizon_minutes:.0f} мин. "
                         "Это окно модели, а не крайний срок сделки")

    return {
        "state_code": state_code, "state_name": state_name,
        "state_explanation": state_explanation,
        "changes_plain": changes[:2], "time_plain": time_meaning,
        "action_now_plain": action,
        "reasons_plain": [horizon_meaning, option_meaning, center_meaning],
        "technical": {
            "r": r_now, "max_r": max_r, "rnd_mode_r": mode, "rnd_median_r": median,
            "barrier_ev_r": ev, "p_take": p_take, "p_stop": p_stop,
            "no_touch": no_touch, "live_short_r": tape_r,
        },
        "confidence_constraints": _decision_frame(strategy, obs, history, timing).get("confidence_constraints"),
    }


def build_snapshot(engine) -> dict:
    tick = engine.tick_payload()
    trade = tick.get("trade")
    if not trade:
        return {"captured_ts": tick.get("ts"), "trade": None,
                "message": "нет активной сделки"}
    trade_id = int(trade["id"])
    ridge = engine.ridge_payload()
    previous = engine.journal.recent_ai_contexts(trade_id, limit=2)
    observation = _observation(engine, tick, ridge, trade)
    history = _forecast_history(engine, trade_id)
    if previous:
        old = previous[-1].get("metrics") or {}
        current = {
            "r": observation["position"].get("r"),
            "p_take": observation["option_probability"].get("p_take_first"),
            "p_stop": observation["option_probability"].get("touch_stop_horizon"),
            "no_touch": observation["option_probability"].get("no_touch_horizon"),
            "barrier_ev_r": observation["option_probability"].get("barrier_ev_r"),
            "mode_r": observation["probability_cone"].get("mode_r"),
            "median_r": observation["probability_cone"].get("median_r"),
            "live_short_r": observation["position"].get("price_tape", {}).get("directional_short_r"),
        }
        history["since_last_ai_review"] = {
            key: (round(float(value) - float(old[key]), 4)
                  if _num(value) is not None and _num(old.get(key)) is not None else None)
            for key, value in current.items()
        }
    timing = _time_context(tick, trade, previous)
    strategy = _strategy(engine, trade)
    decision = _decision_frame(strategy, observation, history, timing)
    manager = _manager_frame(strategy, observation, history, timing)
    return {
        "captured_ts": tick.get("ts"), "trade_id": trade_id,
        "strategy": strategy, "time_context": timing,
        "observation": observation, "metric_history": history,
        "evidence_matrix": _evidence_matrix(strategy, observation, history, timing),
        "decision_frame": decision, "manager_frame": manager,
        "scenario_frame": _scenario_frame(strategy, observation, history, timing),
        "previous_reviews": [
            {"ts": item.get("ts"), "metrics": item.get("metrics")}
            for item in previous
        ],
        "validation": engine.journal.validation_report(),
    }


def request_verdict(snapshot: dict) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не настроен на сервере")
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    body = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 650,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Текущий семантический снимок сделки:\n"
             + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))},
        ],
    }
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    try:
        with httpx.Client(proxy=proxy, timeout=45, trust_env=False) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions", json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json", "Accept": "application/json",
                    "User-Agent": "Seiltanzer-Terminal/1.0",
                    "HTTP-Referer": "https://seiltanzer-terminal.local",
                    "X-Title": "Seiltanzer Terminal",
                })
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise RuntimeError(f"OpenRouter HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter connection failed: {type(exc).__name__}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("OpenRouter вернул пустой ответ")
    violations = [term for term in ("edge", "null", "setup_guard", "setup.playbook",
                                    "baseline", "action:", "live phase", "option mode")
                  if term in content.lower()]
    required_headers = ("СОСТОЯНИЕ", "ИЗМЕНИЛОСЬ", "ВРЕМЯ", "ЧТО ДЕЛАТЬ",
                        "ПОЧЕМУ", "ПЛАН", "СЛЕДУЮЩАЯ ПРОВЕРКА")
    missing_headers = [header for header in required_headers if header not in content.upper()]
    if missing_headers:
        violations.append("нет разделов: " + ",".join(missing_headers))
    if violations:
        correction = dict(body)
        correction["max_tokens"] = 650
        correction["messages"] = body["messages"] + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "Перепиши ответ полностью. Нарушения: "
             + ", ".join(violations)
             + ". Используй manager_frame и scenario_frame. Переведи метрики на обычный русский; "
               "не повторяй входовой сетап. Сохрани все обязательные разделы и три сценария событие → действие."},
        ]
        try:
            with httpx.Client(proxy=proxy, timeout=45, trust_env=False) as client:
                retry = client.post(
                    "https://openrouter.ai/api/v1/chat/completions", json=correction,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                             "Accept": "application/json", "User-Agent": "Seiltanzer-Terminal/1.0",
                             "HTTP-Referer": "https://seiltanzer-terminal.local", "X-Title": "Seiltanzer Terminal"})
                retry.raise_for_status()
                retry_result = retry.json()
            fixed = retry_result.get("choices", [{}])[0].get("message", {}).get("content")
            if fixed:
                content, result = fixed, retry_result
        except httpx.HTTPError:
            pass
    return {"verdict": content.strip(), "model": result.get("model", model),
            "captured_ts": snapshot.get("captured_ts")}
