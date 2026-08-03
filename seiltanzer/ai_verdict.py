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


SYSTEM_PROMPT = """Ты — компактный risk observer активной сделки Seiltanzer.
Ты получаешь уже нормализованный снимок: точную карточку сетапа из PDF, время,
динамику, evidence_matrix, decision_frame и scenario_frame. Прочитай ВСЕ группы, но выводи только
решающие факторы.

Иерархия: (1) option barrier first-touch TAKE/STOP/NO-TOUCH, barrier EV, RND
Q10/Q50/Q90/mode, IV skew/term; (2) live tape/ATR/уровни; (3) correlation;
(4) OI/GEX только как эвристический контекст. Это risk-neutral сценарная модель,
не исторический winrate и не расстояние stop/take. Большой NO-TOUCH означает
зависание до горизонта, не STOP. Не используй устаревшие или отсутствующие поля.

Правила: сравни с открытием и прошлым запросом; учитывай длительность сделки,
сессию, время до горизонта и обновления цепочки. Не выдумывай FVG-подтверждение,
уровни или диапазоны. Не пиши «закрыть на стопе». Стоп не расширять, убыток не
усреднять. Лестница по 10%; БУ и trailing только после 1.5R. Если данные delayed,
proxy или manual structure неизвестна — снизь уверенность, но дай рабочий план.
Не используй null как число. Не обещай преимущество: покажи наблюдаемое основание.

Ответ <=240 слов, без вводных:
СТАТУС — подтверждён / нейтрален / ухудшается / сломан / данных мало; одно основание.
ВРЕМЯ — фаза сделки, сессия и отношение к option horizon одной строкой.
ОПЦИОНЫ — 2–3 главных факта и их изменение.
СЕЙЧАС — одно действие; обязательно: barrier EV/NO-TOUCH, live-фаза и конкретное
условие активного setup.playbook. Нельзя называть pTake «низкой» без NO-TOUCH и горизонта.
СЦЕНАРИИ — A/B/C: перенеси числовой триггер и action из scenario_frame, не обобщай.
КОНТРОЛЬ — ближайшее событийное условие и одна главная проблема качества.
Только переданные значения; цены используй лишь из exact_levels."""


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
    next_rung = next((x for x in strategy.get("management", {}).get("rungs_r", [])
                      if current_r is None or x > current_r + 1e-6), None)
    no_touch_label = ("NO-TOUCH dominates" if _num(no_touch) is not None
                      and _num(no_touch) >= max(_num(p_take) or 0, _num(p_stop) or 0)
                      else "NO-TOUCH rises")
    return {
        "A_continuation": {
            "option_trigger": f"barrierEV improves from {op.get('barrier_ev_r')}R and pTake rises from {p_take}",
            "live_trigger": f"R holds above option mode {cone.get('mode_r')} while median {cone.get('median_r')} reprices upward",
            "action": f"hold; take 10% at next rung {next_rung}R" if next_rung else "manage remainder by trailing rules",
        },
        "B_stall": {
            "option_trigger": f"{no_touch_label} from {no_touch}",
            "live_trigger": f"R rotates around option mode {cone.get('mode_r')}/median {cone.get('median_r')} without >=0.20R progress",
            "action": "hold only while setup structure remains valid; reassess on chain refresh/horizon roll",
        },
        "C_deterioration": {
            "option_trigger": f"pStop rises from {p_stop} and barrierEV deteriorates",
            "live_trigger": f"R loses option mode {cone.get('mode_r')} with adverse live tape and falling median {cone.get('median_r')}",
            "action": "do not widen/average; preserve original stop and use only armed strategy management",
        },
        "next_review_events": [
            "new option-chain timestamp", "R move >=0.20 from this review",
            "cross option mode/median/Q10/Q90", "OI wall or gamma flip crossing",
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
        }
        history["since_last_ai_review"] = {
            key: (round(float(value) - float(old[key]), 4)
                  if _num(value) is not None and _num(old.get(key)) is not None else None)
            for key, value in current.items()
        }
    timing = _time_context(tick, trade, previous)
    strategy = _strategy(engine, trade)
    return {
        "captured_ts": tick.get("ts"), "trade_id": trade_id,
        "strategy": strategy, "time_context": timing,
        "observation": observation, "metric_history": history,
        "evidence_matrix": _evidence_matrix(strategy, observation, history, timing),
        "decision_frame": _decision_frame(strategy, observation, history, timing),
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
    forbidden = [term for term in ("edge", "null", "delta от открытия")
                 if term in content.lower()]
    if forbidden:
        correction = dict(body)
        correction["max_tokens"] = 650
        correction["messages"] = body["messages"] + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "Перепиши ответ полностью. Нарушения: "
             + ", ".join(forbidden)
             + ". Используй только decision_frame/scenario_frame; не повторяй отсутствующие метрики."},
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
