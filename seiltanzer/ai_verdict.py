"""Compact, stateful AI review of the active trade."""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

import httpx


SYSTEM_PROMPT = """Ты — наблюдатель активной сделки в терминале Seiltanzer.
Стратегический сетап задаёт исходный тезис; опционные и live-метрики помогают
сопровождать его, но не подменяют ручное подтверждение FVG/AMD/Block/Fib.

Правила:
- анализируй изменение метрик с открытия и прошлого запроса, а не только кадр;
- сначала опционная геометрия: option P/edge и их delta, Q10/Q50/Q90/mode,
  IV wings/skew/term и только затем live tape/уровни. OI/GEX — подтверждение,
  но не самостоятельная причина действия;
- option P — risk-neutral first-passage оценка, не исторический winrate и не
  простая пропорция стоп/тейк;
- delayed/proxy данные понижают уверенность; OI/GEX — только эвристический контекст;
- близость к стопу сама по себе не ломает тезис. Не пиши очевидное «на стопе
  закрыть». Называй цену близкой только при <=0.25R или <=0.35 ATR до стопа;
- не называй edge ухудшившимся без отрицательного delta; отделяй отсутствие
  option edge при открытии от его последующего ухудшения;
- не предлагай расширять стоп, усреднять убыток или нарушать лестницу. БУ только
  после 1.5R; фиксация по 10% на заданных рубежах;
- используй максимум три главных подтверждения для действия, остальное — в
  сценарных триггерах. Не пересказывай все числа и не добавляй дисклеймер.
- не придумывай ценовые диапазоны. Бери только переданные R/уровни; если точной
  цены уровня нет, пиши триггер в R. Каждый сценарий обязан содержать минимум
  один опционный триггер и реакцию live-цены;
- следующий контроль назначай по событию (обновление цепочки, сдвиг >=0.20R,
  пересечение option mode/Q50/OI-wall/gamma flip), а не произвольным «через час».

Ответ <=280 слов, строго в формате:
РЕЖИМ — тезис подтверждён / нейтрален / ухудшается / сломан / данных мало; одна конкретная причина без слова «причина».
ИЗМЕНИЛОСЬ — 1–3 коротких изменения с прошлого запроса или открытия.
СЕЙЧАС — одно действие по правилам стратегии и максимум 3 основания.
СЦЕНАРИИ — три строки: A продолжение; B зависание; C ухудшение. В каждой:
опционный триггер + реакция цены → конкретное действие по удержанию/лестнице/БУ.
Не используй сам стоп как аналитический триггер.
КОНТРОЛЬ — следующее событие для разбора и одна важная проблема данных.
Не придумывай отсутствующие значения."""


# Это не новый источник сигналов, а краткая карта того, что именно должен был
# вручную подтвердить трейдер при открытии конкретного сетапа.
SETUP_PLAYBOOKS = {
    1: ("8H AMD + FVG sweep", "удержание реакции после sweep/FVG", "возврат и закрепление за ручной структурной инвалидацией"),
    2: ("1H AMD + Weekly FVG 0.786", "реакция weekly FVG и продолжение AMD", "потеря weekly/Fib-зоны по ручной структуре"),
    3: ("12H FVG + 4H bFVGc", "реакция 12H FVG подтверждена 4H bFVGc", "отмена 4H подтверждения и потеря 12H зоны"),
    4: ("SP500 + NAS100 correlation", "направление подтверждается обоими индексами", "устойчивая межрыночная дивергенция против сделки"),
    5: ("12H FVG retest + VIX>20", "ретест FVG сохраняется при требуемом VIX-контексте", "потеря FVG и исчезновение подтверждения волы"),
    6: ("8H FVG + VIX>20", "8H FVG удерживается при требуемом VIX-контексте", "потеря 8H FVG и подтверждения волы"),
    7: ("12H FVG sweep + 1H FVG", "1H реакция подтверждает 12H sweep", "потеря 1H подтверждения; DV1X проверяется отдельно"),
    8: ("12H + 90m FVG + 2H bFVGc", "младшие FVG/bFVGc подтверждают 12H контекст", "последовательная потеря младших подтверждений"),
    9: ("12H FVG + 2H bFVGc", "2H bFVGc подтверждает 12H FVG", "отмена 2H подтверждения и потеря 12H зоны"),
    10: ("Daily FVG + 4H sweep", "4H sweep даёт реакцию внутри Daily FVG", "потеря Daily FVG после неудачного sweep"),
    11: ("4H VIX + GVZ correlation", "волатильностные связи подтверждают сценарий XAU", "устойчивый распад требуемой VIX/GVZ связи"),
    12: ("12H FVG sweep + 15m", "15m реакция подтверждает 12H sweep", "потеря 15m подтверждения и 12H зоны"),
    13: ("Daily FVG + AMD + Fib", "AMD/Fib реакция удерживает Daily FVG", "потеря Fib/FVG по ручной структуре"),
    14: ("Daily FVG + DXY long", "Daily FVG и DXY подтверждают long EURUSD", "DXY и структура устойчиво против long"),
    15: ("Daily FVG + DXY short", "Daily FVG и DXY подтверждают short EURUSD", "DXY и структура устойчиво против short"),
    16: ("8H Block + 4H confirmation", "4H реакция подтверждает 8H Block", "отмена 4H подтверждения и потеря блока"),
}


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

    def delta(key: str, left: dict, right: dict):
        a, b = _num(left.get(key)), _num(right.get(key))
        return round(a - b, 4) if a is not None and b is not None else None

    hours = max((_num(latest.get("ts")) or 0) - (_num(first.get("ts")) or 0), 0) / 3600
    edge_open_delta = delta("option_edge", latest, first)
    return {
        "samples": len(rows),
        "minutes": round(hours * 60, 1),
        "open": {k: _rnd(first.get(k)) for k in ("r", "p_take", "option_edge", "option_ev")},
        "previous": {k: _rnd(previous.get(k)) for k in ("r", "p_take", "option_edge", "option_ev")},
        "current": {k: _rnd(latest.get(k)) for k in ("r", "p_take", "option_edge", "option_ev")},
        "delta_from_open": {
            "r": delta("r", latest, first),
            "p_take": delta("p_take", latest, first),
            "edge": edge_open_delta,
        },
        "delta_from_previous": {
            "r": delta("r", latest, previous),
            "p_take": delta("p_take", latest, previous),
            "edge": delta("option_edge", latest, previous),
        },
        "edge_change_per_hour": (
            round(edge_open_delta / hours, 4)
            if edge_open_delta is not None and hours >= 0.05 else None),
        "chain_age_sec": _rnd(latest.get("chain_age_sec"), 1),
        "source": latest.get("source"),
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
    premise, confirmation, invalidation = SETUP_PLAYBOOKS.get(cfg.num, (cfg.name, "ручное подтверждение", "ручная структурная инвалидация"))
    stats = engine.journal.setup_stats(cfg.num, engine.settings.journal_min_trades)
    return {
        "setup": cfg.num, "name": cfg.name, "instrument": cfg.instrument,
        "direction": trade.get("direction"), "premise": premise,
        "confirmation": confirmation, "structural_invalidation": invalidation,
        "manual_structure_required": True,
        "required_filters": list(cfg.filters), "target_rr": cfg.rr,
        "stats": stats.__dict__,
        "management": {
            "take_fraction_each_rung": 0.10,
            "rungs_r": [1.0, 1.25, 1.5, 1.75, 2.0, 2.2],
            "breakeven_after_r": 1.5,
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
            "to_take_atr": _rnd(state.get("to_take_atr"), 2),
            "to_stop_atr": _rnd(state.get("to_stop_atr"), 2),
            "minutes_open": round((time.time() - float(trade["opened_at"])) / 60, 1),
            "price_tape": _price_tape(engine, tick, trade),
        },
        "option_probability": {
            "available": prob.get("source") == "options_barrier_mc",
            "p_take_first": _rnd(prob.get("p")), "scenario_band": [_rnd(prob.get("p_lo")), _rnd(prob.get("p_hi"))],
            "p_ev0": _rnd(prob.get("p_breakeven")), "edge": _rnd(market.get("edge")),
            "edge_at_open": _rnd(trade.get("edge_at_open")),
            "edge_delta_from_open": _rnd(state.get("edge_shift")),
            "option_ev": _rnd(market.get("option_ev")),
            "unresolved_horizon": _rnd(market.get("p_unresolved_horizon")),
            "raw_reached_take": _rnd(market.get("p_take_reached_horizon")),
            "raw_reached_stop": _rnd(market.get("p_stop_reached_horizon")),
            "raw_unresolved": _rnd(market.get("p_unresolved_raw_horizon")),
            "terminal_tail_take": _rnd(market.get("terminal_p_take")),
            "terminal_tail_stop": _rnd(market.get("terminal_p_stop")),
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
            "option_split_take": _rnd(market.get("p_take_horizon")),
            "option_split_stop": _rnd(market.get("p_stop_horizon")),
            "raw_reached_take": _rnd(market.get("p_take_reached_horizon")),
            "raw_reached_stop": _rnd(market.get("p_stop_reached_horizon")),
            "rv_iv_ratio": _rnd(cone.get("rv_iv_ratio")),
        },
        "lattice": {
            "model_control_p": _rnd(prob.get("model_p")),
            "option_p": _rnd(prob.get("p")),
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


def build_snapshot(engine) -> dict:
    tick = engine.tick_payload()
    trade = tick.get("trade")
    if not trade:
        return {"captured_ts": tick.get("ts"), "trade": None,
                "message": "нет активной сделки"}
    trade_id = int(trade["id"])
    ridge = engine.ridge_payload()
    return {
        "captured_ts": tick.get("ts"), "trade_id": trade_id,
        "strategy": _strategy(engine, trade),
        "observation": _observation(engine, tick, ridge, trade),
        "metric_history": _forecast_history(engine, trade_id),
        "previous_reviews": engine.journal.recent_ai_verdicts(trade_id, limit=3),
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
        "max_tokens": 700,
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
    return {"verdict": content.strip(), "model": result.get("model", model),
            "captured_ts": snapshot.get("captured_ts")}
