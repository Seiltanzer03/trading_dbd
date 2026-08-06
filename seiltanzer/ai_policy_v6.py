"""Policy manager v6: confirmed no-change action, explicit timing and input audit."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import ai_policy_v5 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_SELECT = _impl.select_final_policy
_BASE_ANALYZE = _impl.analyze_policies

_ACTIVE_DECISION_REQUIREMENTS = {
    "CLOSE_10": {
        "min_parameter_stability": 0.45,
        "min_source_stability": 0.45,
        "min_independent_adverse_families": 1,
        "requires_full_exit_authority": False,
    },
    "CLOSE_25": {
        "min_parameter_stability": 0.55,
        "min_source_stability": 0.50,
        "min_independent_adverse_families": 2,
        "requires_full_exit_authority": False,
    },
    "CLOSE_50": {
        "min_parameter_stability": 0.64,
        "min_source_stability": 0.625,
        "min_independent_adverse_families": 2,
        "requires_full_exit_authority": False,
    },
    "EXIT": {
        "min_parameter_stability": 0.73,
        "min_source_stability": 0.75,
        "min_independent_adverse_families": 3,
        "requires_full_exit_authority": True,
    },
}


def risk_constraint(inputs: PolicyInputs, tick: dict, trade: dict) -> dict:
    """Use the current stop/BE, but do not model an indicator-driven future trail."""
    sources = [trade or {}, tick or {}]
    stop, stop_key = _first_num(sources, (
        ("effective_stop_r",), ("stop_r",),
        ("management", "effective_stop_r"), ("management", "stop_r"),
        ("risk", "effective_stop_r"),
        ("ladder", "effective_stop_r"), ("ladder", "stop_r"),
        ("prob", "stop_r"),
    ))
    if stop is None:
        be_armed = inputs.max_r >= inputs.be_after - 1e-12
        stop = 0.0 if be_armed else -1.0
        stop_source = (
            f"strategy BE floor 0R: max_r {inputs.max_r:.3f}R >= "
            f"{inputs.be_after:.3f}R"
            if be_armed else
            "strategy initial stop -1R: BE threshold not reached"
        )
    else:
        stop_source = f"explicit current stop {stop_key}"

    max_giveback, giveback_key = _first_num(sources, (
        ("max_giveback_r",), ("management", "max_giveback_r"),
        ("risk", "max_giveback_r"), ("ladder", "max_giveback_r"),
    ))
    giveback_floor = (
        inputs.r0 - max_giveback
        if max_giveback is not None and max_giveback >= 0.0 else None
    )
    floor = float(stop) if giveback_floor is None else max(float(stop), giveback_floor)
    return {
        "cvar_floor_r": round(floor, 4),
        "effective_stop_floor_r": round(float(stop), 4),
        "max_giveback_r": (
            round(float(max_giveback), 4) if max_giveback is not None else None
        ),
        "giveback_floor_r": (
            round(float(giveback_floor), 4) if giveback_floor is not None else None
        ),
        "source": stop_source,
        "max_giveback_source": giveback_key,
        "rule": (
            "max(current stop/BE, r0-max_giveback_r)"
            if giveback_floor is not None else
            "current stop/BE; indicator trailing is outside the quantitative model"
        ),
        "trailing_modelled": False,
        "trailing_note": (
            "Индикаторный трейлинг не моделируется и не влияет на Expected/CVaR; "
            "учитываются только текущий стоп/БУ и лестница фиксаций."
        ),
    }


def _hold_confirmable(raw_choice: str, result: dict, stability: dict,
                      evidence: dict, selection_rule: dict,
                      source_share: float) -> bool:
    selected = result.get("policy") or raw_choice
    eligible = selection_rule.get("eligible") or []
    adverse = evidence.get("adverse_confirmation_families") or []
    local_share = float(stability.get("selected_share") or 0.0)
    return bool(
        raw_choice == "HOLD"
        and selected == "HOLD"
        and "HOLD" in eligible
        and not adverse
        and local_share >= 0.64
        and source_share >= 0.50
    )


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: PolicyInputs, selection_rule: dict) -> dict:
    """Low data authority blocks new orders, not a stable no-change decision."""
    result = _BASE_SELECT(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    source = result.get("authority_stability") or {}
    source_share = float(
        (source.get("winner_shares") or {}).get("HOLD", 0.0)
        if raw_choice == "HOLD"
        else result.get("source_stability_share") or 0.0
    )
    if _hold_confirmable(
        raw_choice, result, stability, evidence, selection_rule, source_share
    ):
        reliability = _at(
            evidence, "data_quality", "reliability", default={}) or {}
        level = reliability.get("level") or "не определена"
        result.update({
            "policy": "HOLD",
            "provisional_policy": "HOLD",
            "execution_policy": None,
            "execution_required": False,
            "automatic_execution_allowed": False,
            "working_action_confirmed": True,
            "status": "confirmed_hold",
            "source_stability_share": source_share,
            "reasons": [
                "HOLD подтверждён: он проходит CVaR, устойчив в stress-проверках "
                "и нет независимых аргументов за сокращение",
                (
                    f"надёжность данных {level}: она запрещает активное сокращение, "
                    "но не отменяет подтверждённое отсутствие нового вмешательства"
                ),
            ],
        })
    else:
        result.setdefault("working_action_confirmed", False)
        result.setdefault("execution_required", result.get("policy") != "HOLD")
    return result


def _price_from_r(trade: dict, r_value: float | None) -> float | None:
    if r_value is None:
        return None
    entry = _num((trade or {}).get("entry"))
    stop = _num((trade or {}).get("stop"))
    if entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    direction = str((trade or {}).get("direction") or "").lower()
    sign = 1.0 if direction in {"long", "buy", "лонг"} else -1.0
    return entry + sign * float(r_value) * risk


def _fmt_time(ts: float, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


def _chain_refresh(engine, tick: dict) -> dict:
    now = _num(tick.get("ts")) or time.time()
    chain = _at(tick, "feeds", "chain", default={}) or {}
    last_ts = _num(chain.get("ts"))
    poll_sec = float(getattr(getattr(engine, "settings", None),
                             "chain_poll_sec", 600.0) or 600.0)
    due_ts = (last_ts + poll_sec) if last_ts is not None else now
    seconds_until = max(0.0, due_ts - now)
    timezone_name = os.environ.get("APP_TIMEZONE", "Europe/Moscow")
    return {
        "poll_interval_sec": round(poll_sec, 1),
        "last_chain_ts": last_ts,
        "next_attempt_ts": due_ts,
        "next_attempt_utc": _fmt_time(due_ts, "UTC"),
        "next_attempt_local": _fmt_time(due_ts, timezone_name),
        "timezone": timezone_name,
        "seconds_until_attempt": round(seconds_until, 1),
        "overdue": due_ts <= now,
        "current_status": chain.get("status"),
        "current_source": chain.get("source"),
        "guarantees_live_direct": False,
        "estimate_basis": "last successful chain timestamp + configured poll interval",
        "note": (
            "Это расчётная ближайшая попытка по расписанию, а не гарантия точного "
            "момента ответа источника. Источник может снова вернуть delayed/proxy "
            "данные; получение live/direct цепочки не гарантируется."
        ),
    }


def _status_summary(values: dict) -> dict:
    rows = values if isinstance(values, dict) else {}
    statuses = {
        key: (value or {}).get("status")
        for key, value in rows.items() if isinstance(value, dict)
    }
    return {
        "available": any(status not in {None, "no_data"} for status in statuses.values()),
        "statuses": statuses,
    }


def _ridge_available(ridge: dict) -> bool:
    if not isinstance(ridge, dict) or not ridge:
        return False
    if ridge.get("available") is True:
        return True
    if ridge.get("status") in {"live", "delayed", "demo", "ok"}:
        return True
    return any(bool(ridge.get(key)) for key in (
        "density", "strikes", "gex", "oi_profile", "history", "snapshots"
    ))


def _input_audit(tick: dict, ridge: dict) -> dict:
    feeds = tick.get("feeds") or {}
    price = feeds.get("price") or {}
    proxy = feeds.get("proxy_price") or {}
    chain = feeds.get("chain") or {}
    rows = {
        "instrument_price": {
            "available": _num(price.get("value")) is not None,
            "status": price.get("status"),
            "source": price.get("source"),
            "value": _num(price.get("value")),
            "role": "optimizer_and_geometry",
        },
        "option_proxy_price": {
            "available": _num(proxy.get("value")) is not None,
            "status": proxy.get("status"),
            "source": proxy.get("source"),
            "value": _num(proxy.get("value")),
            "role": "option_moneyness_mapping",
        },
        "option_chain": {
            "available": chain.get("status") not in {None, "no_data"},
            "status": chain.get("status"),
            "source": chain.get("source"),
            "role": "option_anchor_optimizer_and_evidence",
        },
        "volatility_indices": {
            **_status_summary(feeds.get("vols") or {}),
            "role": "strategy_filters_and_context_when_applicable",
        },
        "atr_regime_vrp": {
            "available": bool(tick.get("atr") or tick.get("regime") or tick.get("vrp")),
            "role": "evidence_and_regime_context",
        },
        "levels_and_orderflow": {
            "available": bool(tick.get("levels")),
            "role": "evidence_gate",
        },
        "cross_asset_correlation": {
            "available": bool(tick.get("correlation")),
            "status": (tick.get("correlation") or {}).get("status")
                      if isinstance(tick.get("correlation"), dict) else None,
            "role": "uncertainty_and_regime_gate",
        },
        "oi_gex_strike_landscape": {
            "available": _ridge_available(ridge),
            "role": "context_only",
        },
        "strategy_filters": {
            "available": bool(tick.get("filters")),
            "role": "evidence_gate_when_setup_uses_filter",
        },
        "ladder_and_breakeven": {
            "available": bool(tick.get("ladder")),
            "role": "strategy_baseline_optimizer",
        },
    }
    return {
        "rows": rows,
        "available_count": sum(1 for row in rows.values() if row.get("available")),
        "total_count": len(rows),
        "all_inputs_equally_weighted": False,
        "note": (
            "Доступность не означает одинаковый вес: цена/цепочка/лестница входят "
            "в расчёт, уровни и фильтры — в gate, OI/GEX — только контекст."
        ),
    }


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    inputs = result.get("inputs") or {}
    r0 = _num(inputs.get("r0"))
    lower_r = r0 - 0.15 if r0 is not None else None
    upper_r = r0 + 0.15 if r0 is not None else None
    result["recalculation_triggers"] = {
        "current_r": r0,
        "minus_0_15_r": {
            "r": round(lower_r, 4) if lower_r is not None else None,
            "price": _price_from_r(trade, lower_r),
        },
        "plus_0_15_r": {
            "r": round(upper_r, 4) if upper_r is not None else None,
            "price": _price_from_r(trade, upper_r),
        },
        "chain_refresh": _chain_refresh(engine, tick),
        "event_triggers": ["next_rung", "stop", "breakeven", "execution_cost_change"],
    }
    result["input_audit"] = _input_audit(tick, ridge)
    result["management_model_scope"] = {
        "ladder_modelled": True,
        "breakeven_modelled": True,
        "indicator_trailing_modelled": False,
        "note": (
            "Индикаторный трейлинг исключён из Expected/CVaR. После решения ИИ "
            "ручное сопровождение по индикатору остаётся ответственностью пользователя."
        ),
    }
    result["decision_requirements"] = {
        "common": {
            "raw_optimizer_must_select_active_policy": True,
            "policy_must_be_cvar_feasible": True,
            "data_reliability_must_not_be_low": True,
            "independent_families_are_net_directional": True,
            "note": (
                "Пороговые значения необходимы, но недостаточны: активную политику "
                "сначала должен выбрать net-оптимизатор среди CVaR-допустимых."
            ),
        },
        "policies": _ACTIVE_DECISION_REQUIREMENTS,
    }

    rec = result.get("recommendation") or {}
    gate = result.get("gate") or {}
    if rec.get("policy") == "HOLD" and gate.get("working_action_confirmed"):
        rec.update({
            "execution_action_ru": (
                "УДЕРЖИВАТЬ ТЕКУЩИЙ ОСТАТОК; НОВЫХ ОРДЕРОВ НЕ ВЫСТАВЛЯТЬ"
            ),
            "working_action_code": "HOLD_CONFIRMED_NO_ORDER",
            "working_action_confirmed": True,
            "automatic_execution_allowed": False,
            "remaining_management": (
                "сохранить текущий стоп/БУ и лестницу фиксаций; "
                "индикаторный трейлинг не входит в расчёт ИИ"
            ),
        })
    else:
        rec["working_action_confirmed"] = bool(
            gate.get("working_action_confirmed"))
    result["recommendation"] = rec
    result["version"] = "quant-policy-v6-confirmed-hold-timing-input-audit"
    return result


# v4 owns analysis globals; v5 owns stress and authority globals.
for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._base,
):
    module.risk_constraint = risk_constraint
    module.select_final_policy = select_final_policy
    module.analyze_policies = analyze_policies

globals()["risk_constraint"] = risk_constraint
globals()["select_final_policy"] = select_final_policy
globals()["analyze_policies"] = analyze_policies
