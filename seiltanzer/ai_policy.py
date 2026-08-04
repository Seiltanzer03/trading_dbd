"""Public facade and hard-risk corrections for the quantitative AI policy manager.

The large simulation implementation remains in :mod:`seiltanzer.ai_policy_base`.
This facade owns the safety-critical selection rules so a confirmation gate can
never restore a policy which violates the active CVaR constraint.
"""
from __future__ import annotations

from dataclasses import replace

from . import ai_policy_base as _base

globals().update({
    name: value for name, value in vars(_base).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})

_ORIGINAL_BUILD_EVIDENCE = _base.build_metric_evidence
_ORIGINAL_LOCAL_IV = _base.local_iv_surface
_ORIGINAL_METRIC_COVERAGE = _base.metric_coverage


def baseline_strategy_outcomes(sim: _base.PathSimulation,
                               inputs: _base.PolicyInputs) -> _base.np.ndarray:
    """Outcome per unit of the position remaining at the review moment."""
    past_count = sum(inputs.max_r >= rung - 1e-12 for rung in inputs.rungs)
    original_remaining = max(1.0 - inputs.rung_fraction * past_count, 1e-9)
    future_fraction = min(inputs.rung_fraction / original_remaining, 1.0)
    future = _base.np.asarray(
        [rung for rung in inputs.rungs if rung > inputs.max_r + 1e-8], dtype=float)
    if future.size:
        crossed = sim.max_r[:, None] >= future[None, :] - 1e-12
        realized = future_fraction * (crossed * future[None, :]).sum(axis=1)
        closed = _base.np.minimum(1.0, future_fraction * crossed.sum(axis=1))
    else:
        realized = _base.np.zeros_like(sim.terminal)
        closed = _base.np.zeros_like(sim.terminal)
    remaining = _base.np.maximum(0.0, 1.0 - closed)
    exit_r = sim.terminal.copy()
    be_armed = ((inputs.max_r >= inputs.be_after - 1e-12)
                | (sim.max_r >= inputs.be_after - 1e-12))
    exit_r = _base.np.where(be_armed & (exit_r < 0.0), 0.0, exit_r)
    return realized + remaining * exit_r


def policy_metrics(policy: _base.PolicyDistribution, sim: _base.PathSimulation,
                   inputs: _base.PolicyInputs) -> dict:
    """Policy P/L metrics plus empirical event counts on the shared paths."""
    values = policy.outcomes
    next_rung = _base._next_rung(inputs)
    stop_t = sim.stop_time
    rung_t = sim.rung_times.get(next_rung) if next_rung is not None else sim.take_time
    if rung_t is None:
        rung_t = _base.np.full_like(stop_t, _base.np.nan)
    rung_first = ~_base.np.isnan(rung_t) & (_base.np.isnan(stop_t) | (rung_t < stop_t))
    stop_first = ~_base.np.isnan(stop_t) & (_base.np.isnan(rung_t) | (stop_t < rung_t))
    event_t = _base.np.where(rung_first, rung_t,
                             _base.np.where(stop_first, stop_t, _base.np.nan))
    resolved = event_t[~_base.np.isnan(event_t)]
    n = int(values.size)
    no_event: dict[str, float] = {}
    empirical: dict[str, dict] = {}
    for minutes in (15, 30, 60, 120):
        frac = min(minutes / max(inputs.horizon_minutes, 1.0), 1.0)
        event_by = (~_base.np.isnan(event_t)) & (event_t <= frac + 1e-12)
        events = int(_base.np.count_nonzero(event_by))
        estimate = float(1.0 - events / max(n, 1))
        no_event[f"{minutes}m"] = round(estimate, 4)
        empirical[f"{minutes}m"] = {
            "events": events, "scenarios": n, "estimate": round(estimate, 6),
            "display": ">99.9%" if events == 0 and n >= 1000 else f"{estimate * 100:.1f}%",
        }
    return {
        "name": policy.name, "close_fraction": policy.close_fraction,
        "expected_final_r": round(float(_base.np.mean(values)), 4),
        "median_final_r": round(float(_base.np.median(values)), 4),
        "cvar10_r": round(_base._cvar(values, 0.10), 4),
        "p_final_profit": round(float(_base.np.mean(values > 0.0)), 4),
        "p_final_loss": round(float(_base.np.mean(values < 0.0)), 4),
        "p_giveback_0_25_from_now": round(float(_base.np.mean(values <= inputs.r0 - 0.25)), 4),
        "p_giveback_0_50_from_now": round(float(_base.np.mean(values <= inputs.r0 - 0.50)), 4),
        "p_next_rung_before_stop": round(float(_base.np.mean(rung_first)), 4),
        "p_stop_before_next_rung": round(float(_base.np.mean(stop_first)), 4),
        "next_rung_r": _base._rnd(next_rung, 3),
        "expected_event_minutes": (round(float(_base.np.mean(resolved)) * inputs.horizon_minutes, 1)
                                   if resolved.size else None),
        "no_event_probability": no_event,
        "no_event_empirical": empirical,
        "scenario_count": n,
    }


def _raw_policy_choice(metrics: dict[str, dict], r0: float,
                       *, cvar_floor: float | None = None) -> tuple[str, dict]:
    """Choose maximum Expected R inside the hard CVaR feasible set."""
    floor = max(-0.60, r0 - 0.80) if cvar_floor is None else float(cvar_floor)
    eligible = []
    ineligible = {}
    for name, metric in metrics.items():
        cvar = _base._num(metric.get("cvar10_r"))
        if cvar is not None and cvar >= floor - 1e-12:
            eligible.append(metric)
        else:
            ineligible[name] = {"cvar10_r": cvar, "shortfall_r": (
                round(floor - cvar, 4) if cvar is not None else None)}
    if not eligible:
        best = max(metrics.values(), key=lambda x: _base._num(x.get("cvar10_r")) or -999.0)
        return best["name"], {
            "cvar_floor_r": round(floor, 4), "eligible": [],
            "ineligible": ineligible, "risk_constraint_unmet": True,
            "indifference_band_r": 0.03,
        }
    best_mean = max(float(m["expected_final_r"]) for m in eligible)
    near = [m for m in eligible if best_mean - float(m["expected_final_r"]) <= 0.03 + 1e-12]
    best = min(near, key=lambda x: POLICY_FRACTIONS[x["name"]])
    return best["name"], {
        "cvar_floor_r": round(floor, 4),
        "eligible": [m["name"] for m in eligible],
        "ineligible": ineligible,
        "risk_constraint_unmet": False,
        "best_expected_r": round(best_mean, 4),
        "indifference_band_r": 0.03,
    }


def stability_analysis(inputs: _base.PolicyInputs, base_choice: str) -> dict:
    """Return winner and feasibility frequencies for every policy, not one only."""
    scenarios = [inputs]
    scenarios += [replace(inputs, r0=min(max(inputs.r0 + d, -0.98), inputs.T - 0.02))
                  for d in (-0.10, 0.10)]
    scenarios += [replace(inputs, sigma_R=max(0.08, inputs.sigma_R * m))
                  for m in (0.95, 1.05)]
    scenarios += [replace(inputs, drift_R=inputs.drift_R + d) for d in (-0.04, 0.04)]
    scenarios += [replace(inputs, skew_R=min(max(inputs.skew_R + d, -0.45), 0.45))
                  for d in (-0.05, 0.05)]
    scenarios += [replace(inputs, term_slope=min(max(inputs.term_slope + d, -0.8), 0.8))
                  for d in (-0.10, 0.10)]
    floor = max(-0.60, inputs.r0 - 0.80)
    winners = {name: 0 for name in POLICY_FRACTIONS}
    feasible = {name: 0 for name in POLICY_FRACTIONS}
    for scenario in scenarios:
        metrics, _ = _base._run_once(scenario, n_paths=1400, n_steps=180, seed=0xB100)
        choice, rule = _raw_policy_choice(metrics, scenario.r0, cvar_floor=floor)
        winners[choice] += 1
        for name in rule.get("eligible", []):
            feasible[name] += 1
    total = len(scenarios)
    stats = {
        name: {
            "winner_count": winners[name], "winner_share": round(winners[name] / total, 4),
            "feasible_count": feasible[name], "feasible_share": round(feasible[name] / total, 4),
        } for name in POLICY_FRACTIONS
    }
    return {
        "checks": total,
        "selected_policy": base_choice,
        "selected_count": winners.get(base_choice, 0),
        "selected_share": round(winners.get(base_choice, 0) / total, 4),
        "winner_counts": winners, "winner_shares": {k: v["winner_share"] for k, v in stats.items()},
        "policy_stats": stats, "fixed_cvar_floor_r": round(floor, 4),
        "perturbations": "r±0.10R; sigma±5%; drift±0.04R; skew±0.05; term±0.10",
    }


def _wing_status(target: float, row_ranges: list[tuple[float, float]],
                 common_range: tuple[float, float]) -> str:
    lo, hi = common_range
    if target < lo or target > hi:
        return "edge_clamped"
    if any(target < row_lo or target > row_hi for row_lo, row_hi in row_ranges):
        return "extrapolated"
    return "interpolated"


def local_iv_surface(payload: dict) -> dict:
    """Add explicit ±5% wing coverage to the existing IV calculation."""
    out = _ORIGINAL_LOCAL_IV(payload)
    if not out.get("available"):
        return out
    rows = payload.get("value") if isinstance(payload, dict) else []
    ranges = []
    for row in rows or []:
        spot = _base._num(row.get("spot_at_snapshot"))
        xs = [(_base._num(k) / spot - 1.0) * 100.0 for k in (row.get("strikes") or [])
              if spot and _base._num(k) is not None]
        if xs:
            ranges.append((min(xs), max(xs)))
    common = out.get("moneyness_range_pct") or [0.0, 0.0]
    live = float(out.get("live_moneyness_pct") or 0.0)
    put_status = _wing_status(live - 5.0, ranges, (float(common[0]), float(common[1])))
    call_status = _wing_status(live + 5.0, ranges, (float(common[0]), float(common[1])))
    coverage = {
        "put_5pct_available": put_status == "interpolated",
        "call_5pct_available": call_status == "interpolated",
        "put_5pct_status": put_status, "call_5pct_status": call_status,
        "independent_confirmation_eligible": (
            put_status == "interpolated" and call_status == "interpolated"),
    }
    out["wing_coverage"] = coverage
    for group in (out.get("local_24h") or [], out.get("real_expiries") or []):
        for row in group:
            row["wing_coverage"] = dict(coverage)
    return out


def _accepted_value_confirmation(level_r: dict, r0: float, tape: dict,
                                 delta_ratio: float | None) -> dict:
    overhead = [k for k in ("vwap", "poc", "value_area_high")
                if _base._num(level_r.get(k)) is not None
                and float(level_r[k]) >= r0 + 0.05]
    move15 = _base._num(_base._at(tape, "moves", "15m", "directional_r"))
    move60 = _base._num(_base._at(tape, "moves", "60m", "directional_r"))
    nearest = min((float(level_r[k]) for k in overhead), default=None)
    adverse_flow = ((move15 is not None and move15 <= -0.08)
                    or (move60 is not None and move60 <= -0.15))
    conditions = {
        "rejection_from_level": bool(nearest is not None and nearest - r0 <= 0.25 and adverse_flow),
        "adverse_directional_delta": bool(delta_ratio is not None and delta_ratio <= -0.10),
        "no_acceptance_above": bool(nearest is not None and r0 < nearest),
        "live_flow_against": adverse_flow,
    }
    return {"levels": overhead, "conditions": conditions,
            "confirmed": len(overhead) >= 2 and all(conditions.values())}


def build_metric_evidence(engine, tick: dict, ridge: dict, trade: dict,
                          inputs: _base.PolicyInputs, sim: _base.PathSimulation,
                          policy_metrics_map: dict[str, dict]) -> dict:
    evidence = _ORIGINAL_BUILD_EVIDENCE(engine, tick, ridge, trade, inputs, sim,
                                        policy_metrics_map)
    adverse = list(evidence.get("adverse_confirmations") or [])
    supportive = list(evidence.get("supportive_contradictions") or [])
    context = list(evidence.get("context_observations") or [])
    flags = list(evidence.get("uncertainty_flags") or [])

    coverage = _base._at(evidence, "iv_surface", "wing_coverage", default={}) or {}
    if not coverage.get("independent_confirmation_eligible", False):
        adverse = [x for x in adverse if x.get("metric") != "local24h_put_call_skew_pp"]
        supportive = [x for x in supportive if x.get("metric") != "local24h_put_call_skew_pp"]
        flags.append({"metric": "iv_wing_coverage", **coverage})

    overhead_items = [x for x in adverse if x.get("metric") == "accepted_value_overhead"]
    adverse = [x for x in adverse if x.get("metric") != "accepted_value_overhead"]
    level_r = _base._at(evidence, "levels", "r", default={}) or {}
    tape = evidence.get("live_price") or {}
    delta_ratio = _base._num(_base._at(evidence, "levels", "volume_profile_delta",
                                       "directional_delta_ratio"))
    accepted = _accepted_value_confirmation(level_r, inputs.r0, tape, delta_ratio)
    if overhead_items or accepted["levels"]:
        item = {"metric": "accepted_value_overhead", **accepted}
        if accepted["confirmed"]:
            adverse.append(item)
        else:
            item["context_only"] = True
            context.append(item)

    local24 = next((x for x in (_base._at(evidence, "iv_surface", "local_24h", default=[]) or [])
                    if _base._num(x.get("hours")) == 24.0), {})
    skew = abs(_base._num(local24.get("put_call_skew_pp")) or 0.0)
    curvature = abs(_base._num(local24.get("curvature_pp")) or 0.0)
    reasons = []
    score = 2
    status = inputs.chain_status or "unknown"
    if status not in ("ok", "live", "demo"):
        reasons.append(f"опционная цепочка: {status}")
        score -= 1
    if inputs.chain_age_sec is not None and inputs.chain_age_sec > 900:
        reasons.append(f"возраст цепочки {inputs.chain_age_sec / 60:.1f} мин")
        score -= 1
    if not coverage.get("independent_confirmation_eligible", False):
        reasons.append("IV-крылья ±5% не полностью покрыты реальными страйками")
        score -= 1
    if skew >= 8.0 or curvature >= 15.0:
        reasons.append(f"аномальный IV skew/curvature: {skew:.1f}/{curvature:.1f} п.п.")
        score -= 1
    if any(x.get("metric") == "correlation_regime_shift" for x in flags):
        reasons.append("сдвиг корреляционного режима")
        score -= 1
    if not inputs.option_available:
        reasons.append("нет валидной опционной привязки")
        score = -1
    reliability = "высокая" if score >= 2 and not reasons else "средняя" if score >= 0 else "низкая"

    evidence["adverse_confirmations"] = adverse
    evidence["supportive_contradictions"] = supportive
    evidence["context_observations"] = context
    evidence["uncertainty_flags"] = flags
    evidence["adverse_confirmation_count"] = sum(
        1 for x in adverse if not x.get("context_only"))
    evidence.setdefault("data_quality", {})["reliability"] = {
        "level": reliability, "reasons": reasons or ["существенных ограничений не выявлено"]}
    roles = evidence.setdefault("decision_roles", {})
    roles["conditional_confirmation"] = [
        "accepted_value_overhead only after rejection + adverse delta + no acceptance + adverse flow",
        "local_IV_skew only with both ±5% wings interpolated from real strike coverage",
    ]
    return evidence


def metric_coverage(evidence: dict, history: dict | None = None) -> dict:
    result = _ORIGINAL_METRIC_COVERAGE(evidence, history)
    summary = result.setdefault("summary", {})
    summary["coverage_label"] = (
        f"{summary.get('available_groups', 0)}/{summary.get('total_groups', 0)}")
    summary["reliability"] = _base._at(evidence, "data_quality", "reliability", default={})
    return result


def _policy_stability(stability: dict, policy: str) -> dict:
    stats = (stability.get("policy_stats") or {}).get(policy) or {}
    return {
        "policy": policy, "checks": stability.get("checks", 0),
        "selected_count": stats.get("winner_count", 0),
        "selected_share": stats.get("winner_share", 0.0),
        "feasible_count": stats.get("feasible_count", 0),
        "feasible_share": stats.get("feasible_share", 0.0),
        "winner_counts": stability.get("winner_counts", {}),
        "winner_shares": stability.get("winner_shares", {}),
        "policy_stats": stability.get("policy_stats", {}),
        "perturbations": stability.get("perturbations"),
    }


def select_final_policy(raw_choice: str, stability: dict, metrics: dict[str, dict],
                        evidence: dict, inputs: _base.PolicyInputs,
                        selection_rule: dict) -> dict:
    """Confirmation gate constrained to the current CVaR-feasible set."""
    eligible = list(selection_rule.get("eligible") or [])
    if raw_choice not in eligible and eligible:
        raw_choice = max(eligible, key=lambda n: metrics[n]["expected_final_r"])
    floor = float(selection_rule.get("cvar_floor_r", -0.60))
    hold_feasible = "HOLD" in eligible
    tail_override = not hold_feasible
    confirmations = int(evidence.get("adverse_confirmation_count") or 0)
    stats = stability.get("policy_stats") or {}
    requirements = {
        "HOLD": (0.0, 0), "CLOSE_10": (0.45, 1), "CLOSE_25": (0.55, 2),
        "CLOSE_50": (0.64, 2), "EXIT": (0.73, 3),
    }

    def passes(name: str) -> tuple[bool, list[str]]:
        share = float((stats.get(name) or {}).get("winner_share") or 0.0)
        min_share, min_conf = requirements[name]
        reasons = []
        if share <= 0.0:
            reasons.append("политика не выиграла ни одного stress-пересчёта")
        elif share < min_share:
            reasons.append(f"устойчивость {share:.0%} ниже {min_share:.0%}")
        if name != "HOLD" and confirmations < min_conf:
            reasons.append(f"независимых подтверждений {confirmations} < {min_conf}")
        if not inputs.option_available:
            reasons.append("нет валидной option first-touch привязки")
        if inputs.chain_age_sec is not None and inputs.chain_age_sec > 1800:
            reasons.append("цепочка старше 30 минут")
        return not reasons, reasons

    raw_ok, raw_reasons = passes(raw_choice)
    selected = raw_choice
    status = "confirmed" if raw_ok else "conflict"
    reasons = list(raw_reasons)
    downgraded_from = None
    if not raw_ok:
        raw_fraction = POLICY_FRACTIONS[raw_choice]
        candidates = sorted(
            [name for name in eligible if POLICY_FRACTIONS[name] < raw_fraction],
            key=lambda name: POLICY_FRACTIONS[name], reverse=True)
        for candidate in candidates:
            ok, _ = passes(candidate)
            if ok:
                selected = candidate
                status = "downgraded_within_feasible_set"
                downgraded_from = raw_choice
                reasons = raw_reasons
                break

    selected_stats = stats.get(selected) or {}
    if float(selected_stats.get("winner_share") or 0.0) <= 0.0:
        stable_eligible = [name for name in eligible
                           if float((stats.get(name) or {}).get("winner_share") or 0.0) > 0.0]
        if stable_eligible:
            selected = max(stable_eligible, key=lambda name: (
                float((stats.get(name) or {}).get("winner_share") or 0.0),
                float(metrics[name]["expected_final_r"])))
            status = "conflict_stability_fallback"
            reasons.append("исходная политика имела устойчивость 0%; выбран устойчивый вариант из допустимых")
        else:
            selected = max(eligible or list(metrics), key=lambda name: metrics[name]["cvar10_r"])
            status = "manual_conflict"
            reasons.append("ни одна текущая допустимая политика не выиграла stress-пересчёт")

    selected_cvar = _base._num(metrics[selected].get("cvar10_r"))
    if eligible and (selected not in eligible or selected_cvar is None or selected_cvar < floor - 1e-12):
        raise RuntimeError("confirmation gate attempted to select a CVaR-infeasible policy")
    return {
        "policy": selected, "status": status, "reasons": reasons,
        "raw_policy": raw_choice, "downgraded_from": downgraded_from,
        "eligible_policies": eligible,
        "ineligible_policies": selection_rule.get("ineligible") or {},
        "hold_feasible": hold_feasible, "tail_risk_override": tail_override,
        "cvar_floor_r": floor, "confirmation_count": confirmations,
        "automatic_execution_allowed": status != "manual_conflict",
    }


def cancellation_boundaries(inputs: _base.PolicyInputs, selected: str) -> dict:
    if selected == "HOLD":
        return {"available": False,
                "reason": "Для HOLD границы отмены до исполнения нет; переоценка по событиям."}
    return _base.cancellation_boundaries(inputs, selected)


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None) -> dict:
    inputs = _base.extract_policy_inputs(tick)
    metrics, sim = _base._run_once(inputs, n_paths=6500, n_steps=340, seed=0xA17E)
    raw_choice, selection_rule = _raw_policy_choice(metrics, inputs.r0)
    evidence = build_metric_evidence(engine, tick, ridge, trade, inputs, sim, metrics)
    all_stability = stability_analysis(inputs, raw_choice)
    gate = select_final_policy(raw_choice, all_stability, metrics, evidence, inputs,
                               selection_rule)
    selected = gate["policy"]
    raw_stability = _policy_stability(all_stability, raw_choice)
    final_stability = _policy_stability(all_stability, selected)
    advantage = _base._policy_advantage(metrics, selected)
    attribution = _base.counterfactual_attribution(inputs, previous_policy_inputs)
    changes = _base.metric_change_summary(evidence, previous_evidence)
    cancellation = cancellation_boundaries(inputs, selected)
    next_rung = _base._next_rung(inputs)
    hold = metrics["HOLD"]
    chosen = metrics[selected]
    raw = metrics[raw_choice]
    geometry = {
        "scenario_count": hold.get("scenario_count"),
        "next_rung_r": hold.get("next_rung_r"),
        "p_next_rung_before_stop": hold.get("p_next_rung_before_stop"),
        "p_stop_before_next_rung": hold.get("p_stop_before_next_rung"),
        "expected_event_minutes": hold.get("expected_event_minutes"),
        "no_event_empirical": hold.get("no_event_empirical"),
    }
    recommendation = {
        "policy": selected, "close_fraction": POLICY_FRACTIONS[selected],
        "action_ru": {
            "HOLD": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
            "CLOSE_10": "ЗАКРЫТЬ 10% ПОЗИЦИИ СЕЙЧАС",
            "CLOSE_25": "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
            "CLOSE_50": "ЗАКРЫТЬ 50% ПОЗИЦИИ СЕЙЧАС",
            "EXIT": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
        }[selected],
        "remaining_fraction": round(1.0 - POLICY_FRACTIONS[selected], 2),
        "remaining_management": (
            "остаток вести по исходному стопу; БУ/trailing запрещены до 1.5R"
            if inputs.max_r < inputs.be_after else
            "порог 1.5R достигнут: остаток вести по действующим правилам БУ/trailing"),
        "next_rung_r": _base._rnd(next_rung, 3),
        "raw_optimizer_policy": raw_choice,
        "gate_status": gate["status"], "gate_reasons": gate["reasons"],
        "gate_downgrade_reasons": gate["reasons"],
        "automatic_execution_allowed": gate["automatic_execution_allowed"],
    }
    return {
        "version": "quant-policy-v2-hard-cvar",
        "inputs": inputs.as_dict(), "recommendation": recommendation,
        "policies": metrics, "scenario_geometry": geometry,
        "selection_rule": selection_rule, "gate": gate,
        "selected_advantage": advantage,
        "raw_optimizer_stability": raw_stability,
        "stability": final_stability,
        "policy_stability": all_stability.get("policy_stats"),
        "risk_tradeoff": {
            "expected_cost_vs_hold_r": round(chosen["expected_final_r"] - hold["expected_final_r"], 4),
            "cvar_improvement_vs_hold_r": round(chosen["cvar10_r"] - hold["cvar10_r"], 4),
            "raw_expected_cost_vs_hold_r": round(raw["expected_final_r"] - hold["expected_final_r"], 4),
            "raw_cvar_improvement_vs_hold_r": round(raw["cvar10_r"] - hold["cvar10_r"], 4),
        },
        "evidence": evidence, "metric_coverage": metric_coverage(evidence),
        "counterfactual_attribution": attribution, "metric_changes": changes,
        "cancellation_boundary": cancellation,
        "assumptions": [
            "hard CVaR feasibility is enforced before and after the confirmation gate",
            "all policies use the same option-implied paths",
            "identical path-event probabilities are reported once as scenario geometry",
            "OI/GEX is context-only without observed dealer position sign",
            "IV skew is confirmation only when both ±5% wings have real strike coverage",
        ],
    }


for _name in (
    "baseline_strategy_outcomes", "policy_metrics", "_raw_policy_choice",
    "stability_analysis", "local_iv_surface", "build_metric_evidence",
    "metric_coverage", "cancellation_boundaries", "analyze_policies",
):
    setattr(_base, _name, globals()[_name])
