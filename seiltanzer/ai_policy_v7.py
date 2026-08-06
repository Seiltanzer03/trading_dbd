"""Policy manager v7: risk-efficient active decisions with degraded data authority.

Weak delayed/proxy option data no longer blocks every active recommendation.  It
moves the decision into a manual degraded-authority mode and requires independent
live evidence plus material tail-risk improvement.  The delayed option family can
contribute at most one family and can never authorize an action by itself.
"""
from __future__ import annotations

import time
from typing import Any

from . import ai_policy_v6 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_SELECT = _impl.select_final_policy
_BASE_ANALYZE = _impl.analyze_policies

_LIVE_AUTHORITY_FAMILIES = {
    "live_tape", "orderflow_levels", "strategy_filters",
}

# The overlay is intentionally asymmetric: a small cut can be recommended with
# one live family, while a full exit needs broad live confirmation or a genuine
# hard-risk breach.  Expected sacrifice is measured against HOLD after costs.
_DEGRADED_REQUIREMENTS = {
    "CLOSE_10": {
        "max_expected_sacrifice_r": 0.025,
        "min_cvar_gain_r": 0.08,
        "min_total_adverse_families": 1,
        "min_live_adverse_families": 1,
        "min_local_support": 0.55,
        "min_source_support": 0.50,
    },
    "CLOSE_25": {
        "max_expected_sacrifice_r": 0.04,
        "min_cvar_gain_r": 0.18,
        "min_total_adverse_families": 2,
        "min_live_adverse_families": 1,
        "min_local_support": 0.65,
        "min_source_support": 0.50,
    },
    "CLOSE_50": {
        "max_expected_sacrifice_r": 0.06,
        "min_cvar_gain_r": 0.35,
        "min_total_adverse_families": 2,
        "min_live_adverse_families": 1,
        "min_local_support": 0.75,
        "min_source_support": 0.625,
    },
    "EXIT": {
        "max_expected_sacrifice_r": 0.10,
        "min_cvar_gain_r": 0.70,
        "min_total_adverse_families": 3,
        "min_live_adverse_families": 2,
        "min_local_support": 0.80,
        "min_source_support": 0.625,
    },
}

_ACTIONS_RU = {
    "HOLD": "НЕ ВМЕШИВАТЬСЯ; СОХРАНИТЬ ТЕКУЩИЙ МЕНЕДЖМЕНТ",
    "CLOSE_10": "ЗАКРЫТЬ 10% ТЕКУЩЕГО ОСТАТКА СЕЙЧАС",
    "CLOSE_25": "ЗАКРЫТЬ 25% ТЕКУЩЕГО ОСТАТКА СЕЙЧАС",
    "CLOSE_50": "ЗАКРЫТЬ 50% ТЕКУЩЕГО ОСТАТКА СЕЙЧАС",
    "EXIT": "ЗАКРЫТЬ ВЕСЬ ТЕКУЩИЙ ОСТАТОК СЕЙЧАС",
}


def _float(value: Any, default: float = 0.0) -> float:
    value = _num(value)
    return float(value) if value is not None else float(default)


def _policy_support(stability: dict, authority: dict, policy: str,
                    raw_choice: str) -> dict:
    local_row = ((stability.get("policy_stats") or {}).get(policy) or {})
    local_win = _float(local_row.get("winner_share"))
    local_feasible = _float(local_row.get("feasible_share"))

    checks = int(_float(authority.get("checks")))
    source_win = _float((authority.get("winner_shares") or {}).get(policy))
    source_feasible = (
        _float((authority.get("feasible_counts") or {}).get(policy)) / checks
        if checks > 0 else 0.0
    )

    # When the base optimizer already selects the policy, winner stability is the
    # relevant measure.  A risk overlay intentionally overrides the Expected tie
    # break, so feasibility across stresses is the meaningful support measure.
    if raw_choice == policy:
        local_support = local_win
        source_support = source_win
        support_basis = "winner_share"
    else:
        local_support = local_feasible
        source_support = source_feasible
        support_basis = "feasible_share_for_risk_overlay"
    return {
        "local_winner_share": round(local_win, 4),
        "local_feasible_share": round(local_feasible, 4),
        "source_winner_share": round(source_win, 4),
        "source_feasible_share": round(source_feasible, 4),
        "local_support": round(local_support, 4),
        "source_support": round(source_support, 4),
        "basis": support_basis,
    }


def _authority_mode(inputs: PolicyInputs, evidence: dict) -> dict:
    reliability = _at(evidence, "data_quality", "reliability", default={}) or {}
    level = str(reliability.get("level") or "не определена").lower()
    chain_status = str(inputs.chain_status or "unknown").lower()
    proxy = str(inputs.proxy_quality or "unknown").lower()
    chain_trusted = chain_status in {"live", "ok", "demo"}
    proxy_degraded = any(token in proxy for token in (
        "proxy", "reference", "fallback", "derived", "synthetic",
    ))
    degraded = level == "низкая" or not chain_trusted or proxy_degraded
    return {
        "mode": "degraded_manual" if degraded else "full_authority",
        "data_reliability": reliability.get("level") or "не определена",
        "chain_status": inputs.chain_status,
        "proxy_quality": inputs.proxy_quality,
        "chain_trusted": chain_trusted,
        "proxy_degraded": proxy_degraded,
        "automatic_execution_allowed": not degraded,
    }


def _active_evidence(evidence: dict) -> dict:
    adverse = list(evidence.get("adverse_confirmation_families") or [])
    supportive = list(evidence.get("supportive_confirmation_families") or [])
    mixed = list(evidence.get("mixed_confirmation_families") or [])
    live = [family for family in adverse if family in _LIVE_AUTHORITY_FAMILIES]
    non_option = [family for family in adverse if family != "option_distribution"]
    return {
        "adverse_families": adverse,
        "supportive_families": supportive,
        "mixed_families": mixed,
        "total_adverse_count": len(adverse),
        "live_adverse_families": live,
        "live_adverse_count": len(live),
        "non_option_adverse_count": len(non_option),
        "option_only": bool(adverse and not non_option),
    }


def _candidate_row(policy: str, metrics: dict[str, dict], stability: dict,
                   authority: dict, raw_choice: str, selection_rule: dict,
                   evidence_summary: dict) -> dict:
    hold = metrics.get("HOLD") or {}
    candidate = metrics.get(policy) or {}
    expected_delta = (
        _float(candidate.get("expected_final_r"))
        - _float(hold.get("expected_final_r"))
    )
    expected_sacrifice = max(0.0, -expected_delta)
    cvar_gain = (
        _float(candidate.get("cvar10_r"))
        - _float(hold.get("cvar10_r"))
    )
    giveback25_reduction = (
        _float(hold.get("p_giveback_0_25_from_now"))
        - _float(candidate.get("p_giveback_0_25_from_now"))
    )
    giveback50_reduction = (
        _float(hold.get("p_giveback_0_50_from_now"))
        - _float(candidate.get("p_giveback_0_50_from_now"))
    )
    support = _policy_support(stability, authority, policy, raw_choice)
    req = _DEGRADED_REQUIREMENTS[policy]
    eligible = policy in (selection_rule.get("eligible") or [])
    floor = _float(selection_rule.get("cvar_floor_r"), -1.0)
    hold_cvar = _float(hold.get("cvar10_r"), -999.0)
    hard_risk_shortfall = max(0.0, floor - hold_cvar)
    raw_selected = raw_choice == policy
    risk_efficient = bool(
        expected_sacrifice <= req["max_expected_sacrifice_r"] + 1e-12
        and cvar_gain >= req["min_cvar_gain_r"] - 1e-12
    )

    total_ok = (
        evidence_summary["total_adverse_count"]
        >= req["min_total_adverse_families"]
    )
    live_ok = (
        evidence_summary["live_adverse_count"]
        >= req["min_live_adverse_families"]
    )
    local_ok = support["local_support"] >= req["min_local_support"] - 1e-12
    source_ok = support["source_support"] >= req["min_source_support"] - 1e-12

    # Emergency EXIT remains possible under weak data when HOLD itself violates
    # the hard floor materially.  It still needs a live family and one additional
    # independent adverse family; delayed options alone are never sufficient.
    emergency_exit = bool(
        policy == "EXIT"
        and raw_selected
        and hard_risk_shortfall >= 0.15
        and evidence_summary["live_adverse_count"] >= 1
        and evidence_summary["total_adverse_count"] >= 2
        and support["local_support"] >= 0.73
        and support["source_support"] >= 0.50
    )

    qualified = bool(
        eligible
        and not evidence_summary["option_only"]
        and (raw_selected or risk_efficient)
        and ((total_ok and live_ok and local_ok and source_ok) or emergency_exit)
    )
    utility = (
        expected_delta
        + 0.35 * cvar_gain
        + 0.10 * max(giveback50_reduction, 0.0)
        + 0.02 * POLICY_FRACTIONS[policy]
    )
    return {
        "policy": policy,
        "eligible": eligible,
        "raw_selected": raw_selected,
        "risk_efficient_override": risk_efficient and not raw_selected,
        "expected_delta_vs_hold_r": round(expected_delta, 4),
        "expected_sacrifice_vs_hold_r": round(expected_sacrifice, 4),
        "cvar_gain_vs_hold_r": round(cvar_gain, 4),
        "p_giveback_0_25_reduction": round(giveback25_reduction, 4),
        "p_giveback_0_50_reduction": round(giveback50_reduction, 4),
        "hard_risk_shortfall_r": round(hard_risk_shortfall, 4),
        "emergency_exit": emergency_exit,
        "support": support,
        "requirements": dict(req),
        "checks": {
            "expected_and_cvar": raw_selected or risk_efficient,
            "total_adverse": total_ok,
            "live_adverse": live_ok,
            "local_support": local_ok,
            "source_support": source_ok,
            "not_option_only": not evidence_summary["option_only"],
        },
        "qualified": qualified,
        "utility": round(utility, 6),
    }


def _degraded_overlay(raw_choice: str, base_result: dict,
                      stability: dict, metrics: dict[str, dict], evidence: dict,
                      inputs: PolicyInputs, selection_rule: dict) -> dict:
    authority = base_result.get("authority_stability") or {}
    mode = _authority_mode(inputs, evidence)
    evidence_summary = _active_evidence(evidence)
    rows = [
        _candidate_row(
            policy, metrics, stability, authority, raw_choice, selection_rule,
            evidence_summary,
        )
        for policy in ("CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
        if policy in metrics
    ]
    qualified = [row for row in rows if row["qualified"]]
    selected_row = max(qualified, key=lambda row: row["utility"]) if qualified else None
    overlay = {
        "authority": mode,
        "evidence": evidence_summary,
        "candidates": rows,
        "selected": selected_row,
        "active_recommendation_available": selected_row is not None,
        "rule": (
            "weak data permits manual active decisions only with independent live "
            "evidence and material net tail-risk improvement"
        ),
    }
    base_result["degraded_authority_overlay"] = overlay
    base_result["authority_mode"] = mode["mode"]

    if selected_row is None:
        return base_result

    policy = selected_row["policy"]
    reasons = [
        f"{policy} разрешён в режиме пониженного авторитета для ручного исполнения",
        (
            f"Expected против HOLD {selected_row['expected_delta_vs_hold_r']:+.3f}R; "
            f"улучшение CVaR10 {selected_row['cvar_gain_vs_hold_r']:+.3f}R"
        ),
        (
            f"живых независимых семей против HOLD "
            f"{evidence_summary['live_adverse_count']}; всего независимых семей "
            f"{evidence_summary['total_adverse_count']}"
        ),
        (
            "решение не основано только на delayed option-proxy; "
            "автоматическое исполнение отключено"
        ),
    ]
    if selected_row["emergency_exit"]:
        reasons.append(
            "аварийное основание: HOLD существенно нарушает hard CVaR floor"
        )
    elif selected_row["risk_efficient_override"]:
        reasons.append(
            "risk-overlay сильнее стандартного менеджмента: малая потеря Expected "
            "обменивается на существенное снижение хвостового риска"
        )

    base_result.update({
        "policy": policy,
        "provisional_policy": policy,
        "execution_policy": None,
        "execution_required": True,
        "manual_execution_required": True,
        "automatic_execution_allowed": False,
        "working_action_confirmed": True,
        "status": "confirmed_degraded_manual",
        "source_stability_share": selected_row["support"]["source_support"],
        "confirmation_families": evidence_summary["adverse_families"],
        "confirmation_count": evidence_summary["total_adverse_count"],
        "reasons": reasons,
    })
    return base_result


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: PolicyInputs, selection_rule: dict) -> dict:
    result = _BASE_SELECT(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    return _degraded_overlay(
        raw_choice, result, stability, metrics, evidence, inputs, selection_rule
    )


def _timestamp_seconds(value: Any) -> float | None:
    value = _num(value)
    if value is None:
        return None
    value = float(value)
    if value > 10_000_000_000:
        value /= 1000.0
    return value


def _audit_time(row: dict, now: float, fallback_ts: float | None = None) -> dict:
    ts = _timestamp_seconds(row.get("ts") or row.get("timestamp"))
    if ts is None:
        ts = fallback_ts
    return {
        "timestamp": ts,
        "timestamp_utc": _fmt_time(ts, "UTC") if ts is not None else None,
        "age_sec": round(max(0.0, now - ts), 1) if ts is not None else None,
    }


def _enrich_input_audit(result: dict, tick: dict, ridge: dict) -> None:
    audit = result.get("input_audit") or {}
    rows = audit.get("rows") or {}
    feeds = tick.get("feeds") or {}
    now = _timestamp_seconds(tick.get("ts")) or time.time()
    price = feeds.get("price") or {}
    proxy = feeds.get("proxy_price") or {}
    chain = feeds.get("chain") or {}
    tick_ts = _timestamp_seconds(tick.get("ts"))

    for key, source, fallback in (
        ("instrument_price", price, tick_ts),
        ("option_proxy_price", proxy, tick_ts),
        ("option_chain", chain, None),
    ):
        row = rows.get(key) or {}
        row.update(_audit_time(source, now, fallback))
        if _num(source.get("value")) is not None:
            row["value"] = _num(source.get("value"))
        row["symbol"] = source.get("symbol") or source.get("ticker")
        rows[key] = row

    vol_items = []
    for symbol, item in (feeds.get("vols") or {}).items():
        if not isinstance(item, dict):
            continue
        vol_items.append({
            "symbol": symbol,
            "value": _num(item.get("value")),
            "status": item.get("status"),
            "source": item.get("source"),
            **_audit_time(item, now),
        })
    if "volatility_indices" in rows:
        rows["volatility_indices"]["items"] = vol_items

    correlation = tick.get("correlation") or {}
    if isinstance(correlation, dict) and "cross_asset_correlation" in rows:
        rows["cross_asset_correlation"].update(
            _audit_time(correlation, now, tick_ts)
        )

    rows.setdefault("oi_gex_strike_landscape", {})["snapshot_count"] = len(
        ridge.get("snapshots") or ridge.get("history") or []
    ) if isinstance(ridge, dict) else 0
    audit["rows"] = rows
    audit["snapshot_ts"] = now
    audit["snapshot_utc"] = _fmt_time(now, "UTC")
    result["input_audit"] = audit


def _economic_indifference(result: dict) -> dict:
    policies = result.get("policies") or {}
    hold = policies.get("HOLD") or {}
    hold_expected = _num(hold.get("expected_final_r"))
    alternatives = []
    for name in ("CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        row = policies.get(name) or {}
        expected = _num(row.get("expected_final_r"))
        cvar = _num(row.get("cvar10_r"))
        if hold_expected is None or expected is None:
            continue
        alternatives.append({
            "policy": name,
            "expected_delta_vs_hold_r": round(expected - hold_expected, 4),
            "expected_gap_abs_r": round(abs(expected - hold_expected), 4),
            "cvar_gain_vs_hold_r": round(
                (cvar or 0.0) - (_num(hold.get("cvar10_r")) or 0.0), 4
            ),
        })
    nearest = min(alternatives, key=lambda row: row["expected_gap_abs_r"]) if alternatives else None
    exit_row = next((row for row in alternatives if row["policy"] == "EXIT"), None)
    band = _float((result.get("selection_rule") or {}).get("indifference_band_r"), 0.03)
    return {
        "indifference_band_r": band,
        "nearest_active_policy": nearest,
        "exit_comparison": exit_row,
        "policies_economically_close": bool(
            nearest and nearest["expected_gap_abs_r"] <= band + 1e-12
        ),
        "interpretation": (
            "HOLD can be the least-intervention tie break rather than a strong "
            "directional forecast"
        ),
    }


def _strategy_next_step(result: dict, trade: dict) -> dict:
    inputs = result.get("inputs") or {}
    max_r = _float(inputs.get("max_r"), _float(inputs.get("r0")))
    rungs = sorted(float(x) for x in (inputs.get("rungs") or []))
    next_rung = next((rung for rung in rungs if rung > max_r + 1e-8), None)
    fraction = _float(inputs.get("rung_fraction"), 0.10)
    past_count = sum(max_r >= rung - 1e-12 for rung in rungs)
    original_remaining = max(1.0 - fraction * past_count, 1e-9)
    fraction_of_current = min(fraction / original_remaining, 1.0)
    return {
        "next_rung_r": next_rung,
        "next_rung_price": _price_from_r(trade, next_rung),
        "distance_from_current_r": (
            round(next_rung - _float(inputs.get("r0")), 4)
            if next_rung is not None else None
        ),
        "close_fraction_of_current_remainder": (
            round(fraction_of_current, 4) if next_rung is not None else None
        ),
        "ladder_order_is_strategy_planned": next_rung is not None,
    }


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    _enrich_input_audit(result, tick, ridge)
    result["economic_indifference"] = _economic_indifference(result)
    result["strategy_next_step"] = _strategy_next_step(result, trade)

    gate = result.get("gate") or {}
    rec = result.get("recommendation") or {}
    selected = gate.get("policy") or rec.get("policy") or "HOLD"
    if gate.get("status") == "confirmed_degraded_manual":
        rec.update({
            "policy": selected,
            "close_fraction": POLICY_FRACTIONS[selected],
            "action_ru": _ACTIONS_RU[selected],
            "execution_action_ru": _ACTIONS_RU[selected],
            "working_action_code": f"{selected}_DEGRADED_MANUAL",
            "working_action_confirmed": True,
            "automatic_execution_allowed": False,
            "manual_execution_required": True,
            "remaining_fraction": round(1.0 - POLICY_FRACTIONS[selected], 2),
            "remaining_management": (
                "после ручного сокращения сохранить текущий стоп/БУ и "
                "предусмотренные стратегией ступени для оставшегося объёма"
                if selected != "EXIT" else
                "после полного выхода сопровождение текущей сделки завершено"
            ),
        })
    elif rec.get("policy") == "HOLD" and gate.get("working_action_confirmed"):
        rec.update({
            "execution_action_ru": (
                "НЕ СОВЕРШАТЬ ВНЕПЛАНОВЫХ РУЧНЫХ ИЗМЕНЕНИЙ; СОХРАНИТЬ "
                "ТЕКУЩИЙ СТОП/БУ И ПРЕДУСМОТРЕННЫЕ СТРАТЕГИЕЙ ОРДЕРА ЛЕСТНИЦЫ"
            ),
            "working_action_code": "HOLD_STRATEGY_CONTINUES",
            "remaining_management": (
                "сохранить текущий стоп/БУ и предусмотренные стратегией ступени; "
                "HOLD не является прогнозом обязательного продолжения роста"
            ),
        })
    result["recommendation"] = rec

    common = ((result.get("decision_requirements") or {}).get("common") or {})
    common.update({
        "data_reliability_must_not_be_low": False,
        "low_reliability_mode": "manual_degraded_authority",
        "delayed_option_alone_can_authorize_action": False,
        "risk_overlay_may_override_hold_tie_break": True,
    })
    result.setdefault("decision_requirements", {})["common"] = common
    result["decision_requirements"]["degraded_manual_policies"] = _DEGRADED_REQUIREMENTS
    result["version"] = "quant-policy-v7-degraded-authority-risk-overlay"
    return result


# The lower analysis layers resolve their global selector dynamically.  Patch the
# complete compatibility chain so public imports and maintenance imports agree.
for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._base,
):
    module.select_final_policy = select_final_policy
    module.analyze_policies = analyze_policies

globals()["select_final_policy"] = select_final_policy
globals()["analyze_policies"] = analyze_policies
