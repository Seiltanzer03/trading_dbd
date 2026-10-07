"""Bounded, presentation-only audit of the unified management selector.

The ranking score is dimensionless. Scenario Expected/CVaR remain separate
economic estimates and must never be described as historical trading profit.
"""
from __future__ import annotations

import math
from typing import Any


COMPONENT_LABELS = {
    "quant": "Количественная база",
    "quantitative_base": "Количественная база",
    "mathematical_edge": "Математический edge",
    "active_edge": "Active Edge",
    "historical_llm": "Исторические LLM-гипотезы",
    "historical_llm_hypotheses": "Исторические LLM-гипотезы",
    "current_llm": "Текущий LLM",
}


def _bounded(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value[:256]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth >= 3:
        return None
    if isinstance(value, list):
        return [_bounded(item, depth + 1) for item in value[:16]]
    if isinstance(value, dict):
        return {str(key): _bounded(item, depth + 1)
                for key, item in list(value.items())[:20]}
    return None


def _row(value: Any, keys: tuple[str, ...]) -> dict:
    return {key: _bounded(value[key]) for key in keys if key in value} if isinstance(value, dict) else {}


def compact_unified_ensemble(value: Any) -> dict:
    """Preserve every action and bounded registered expert, never scenario paths."""
    if not isinstance(value, dict) or not value:
        return {}
    out = _row(value, (
        "contract_version", "version", "available", "applied", "status",
        "scheme", "instrument", "regime", "selected_candidate_id",
        "selected_policy", "selected_parameters", "reason", "selection_rule",
        "score_unit", "economics_basis", "historical_validation_status",
        "historical_validation", "risk_constraints_preserved",
        "hard_risk_cvar_preserved", "nominal_weights", "effective_weights",
        "component_order", "measurement_note_ru", "legacy_policy",
        "economics_scope", "shared_scenario_bank", "historical_profit_proven",
        "common_economics_invalid", "common_economics_reason",
        "score_semantics", "regime_weight_semantics", "hard_risk_override",
        "automatic_execution_allowed",
        "ranking_available", "ranking_selected_policy", "ranking_selected_candidate_id",
        "regime_context",
        "operational_guard_reason", "operational_decision_id",
        "authoritative_bank_reused", "execution_assumption", "bridge_events_reproduced",
    ))
    registry = value.get("expert_registry")
    if isinstance(registry, dict):
        out["expert_registry"] = _row(registry, (
            "contract_version", "available", "reason", "definition_sha256",
            "registered_expert_ids", "admitted_schemes", "budgets",
            "rejected_experts", "rejected_schemes", "score_semantics"))
        definition_keys = ("expert_id", "label", "instrument", "model_version", "source_ids",
                           "evidence_family_ids", "max_age_sec", "supported_regimes", "score_semantics")
        out["expert_registry"]["definitions"] = [
            _row(row, definition_keys) for row in (registry.get("definitions") or [])[:11]]
    for key in ("scenario_bank", "comparison_bank"):
        if isinstance(value.get(key), dict):
            out[key] = _row(value[key], (
                "bank_id", "source", "distribution_kind", "exact_authoritative_bank",
                "bridge_events_reproduced", "measure", "seeds", "path_count",
                "step_count", "horizon_minutes", "weights_preserved",
                "effective_path_count", "execution_assumption", "changes_original_admission",
            ))
    contracts = {
        "candidates": (
            "candidate_id", "policy", "parameters", "eligible", "reason",
            "expected_net_r", "cvar10_net_r", "delta_expected_r", "score",
            "component_contributions", "component_scores", "intervention_cost_r",
            "ranking_eligible", "ranking_reason", "delta_cvar_r",
            "execution_cost_r", "economics_basis", "source",
        ),
        "components": (
            "component_id", "nominal_weight", "effective_weight", "availability",
            "label", "registered_expert", "registry_definition_sha256", "instrument",
            "received_ts", "source_lineage_verified",
            "observed_ts", "max_age_sec", "model_version", "score_semantics",
            "available", "reason", "quality", "age_sec", "source_ids",
            "quality_basis", "standalone_action_status", "standalone_action_reason",
            "evidence_family_ids", "freshness_factor", "duplicate_factor",
            "dedup_factor", "suppression_reasons", "redistributed_weight",
            "freshness_multiplier", "dependence_multiplier", "shared_with_components",
        ),
        "counterfactuals": (
            "excluded_component_id", "selected_candidate_id", "selected_policy",
            "selected_parameters", "changed", "expected_net_r", "cvar10_net_r",
        ),
        "scheme_comparisons": (
            "scheme", "selected_candidate_id", "selected_policy",
            "selected_parameters", "expected_net_r", "cvar10_net_r",
            "delta_expected_r", "score", "effective_weights", "available",
            "reason", "historical_validation_status", "extra_interventions",
        ),
        "edge_families": (
            "family_id", "edge_family", "family", "name", "available",
            "status", "reason", "quality", "age_sec", "source_ids",
            "evidence_family_ids", "direction_score", "score", "horizon_minutes",
            "production_role", "historical_validation_status", "component_id",
            "readiness", "forecast_available", "needs_data", "observed_ts", "max_age_sec",
            "weight_pool", "standalone_vote",
        ),
    }
    for key, keys in contracts.items():
        rows = value.get(key)
        if key == "edge_families" and isinstance(rows, dict):
            rows = [{"family_id": family, **row} for family, row in rows.items() if isinstance(row, dict)]
        if isinstance(rows, list):
            # Twelve policies plus bounded parameter variants; no compared
            # policy is lost under the normal twelve-action contract.
            limit = 32 if key == "candidates" else 16
            out[key] = [_row(row, keys) for row in rows[:limit] if isinstance(row, dict)]
            if len(rows) > limit:
                out[f"{key}_truncated_count"] = len(rows) - limit
    return out


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _format(value: Any, unit: str = "") -> str:
    number = _num(value)
    return "—" if number is None else f"{number:+.3f}{unit}"


def _pct(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "—"
    return f"{number * 100:.3g}%" if 0 < abs(number) < .001 else f"{number * 100:.1f}%"


def render_unified_ensemble_lines(value: Any) -> list[str]:
    audit = compact_unified_ensemble(value)
    if not audit:
        return []
    selected = audit.get("selected_candidate_id")
    policy = audit.get("selected_policy") or "—"
    lines = [
        "**ЕДИНЫЙ ВЫБОР ДЕЙСТВИЯ** —",
        f"Схема {audit.get('scheme') or '—'}; инструмент {audit.get('instrument') or '—'}; режим {audit.get('regime') or '—'}. Выбран {policy} ({selected or '—'}).",
        "Баллы компонентов ранжируют только допустимые действия. Hard-risk/CVaR обязательны независимо от весов; исполнение определяется единственным действующим планом.",
        "Expected и CVaR ниже — экономика модельных сценариев после издержек. Баллы безразмерные; историческая прибыль и частота лишних вмешательств требуют отдельного воспроизведения истории.",
        ("Кандидаты с оценкой рассчитаны на общих сценариях."
         if audit.get("shared_scenario_bank") else "Общий набор сценариев не подтверждён; расширенные варианты используют парное сравнение с HOLD. " + str(audit.get("economics_scope") or "")),
        "Δ общего сравнения не заменяет исходные ограничения риска, источников и независимого допуска. Раздел «Независимая консервативная проверка допуска» показывает собственные HOLD/Δ и метод; отказ этой проверки сохраняется, даже если общая модель показывает больший прирост.",
        "Номинальный → фактический вес:",
    ]
    labels = {**COMPONENT_LABELS, **{row.get("component_id"): row.get("label")
                                  for row in audit.get("components") or [] if row.get("label")}}
    bank = audit.get("scenario_bank") or audit.get("comparison_bank") or {}
    regime = audit.get("regime_context") or {}
    if regime:
        lines.insert(-1, "Применимость по режиму: " + str(regime.get("reason") or "UNKNOWN")
                     + "; рабочая классификация, качество " + str(regime.get("quality", 0.))
                     + ". Отдельного голоса и динамической смены долей не добавляет.")
    if bank:
        lines.insert(-1, f"Банк сценариев {bank.get('bank_id') or '—'}; источник {bank.get('source') or '—'}; "
                     f"исходный авторитетный банк использован: {bool(bank.get('exact_authoritative_bank'))}; "
                     f"допущение исполнения: {bank.get('execution_assumption') or '—'}.")
    for row in audit.get("components") or []:
        component = str(row.get("component_id") or "—")
        provenance = ", ".join(str(item) for item in row.get("evidence_family_ids") or []) or "—"
        suppression = ""
        freshness = _num(row.get("freshness_multiplier", row.get("freshness_factor")))
        dependence = _num(row.get("dependence_multiplier", row.get("duplicate_factor")))
        if freshness is not None and freshness < 1:
            suppression += f"; свежесть ×{freshness:.3f}"
        if dependence is not None and dependence < 1:
            suppression += f"; повторные доказательства ×{dependence:.3f}"
        if row.get("quality_basis") == "STRUCTURED_PREFERENCE_ACCEPTANCE_NOT_CALIBRATED_ACCURACY":
            suppression += "; качество — принятие структурированного предпочтения, не калиброванная точность"
            suppression += f"; отдельный LLM-допуск {row.get('standalone_action_status') or '—'} ({row.get('standalone_action_reason') or '—'})"
        lines.append(
            f"• {labels.get(component, component)}: {_pct(row.get('nominal_weight'))} → {_pct(row.get('effective_weight'))}; "
            f"качество {_pct(row.get('quality'))}; возраст {row.get('age_sec') if row.get('age_sec') is not None else '—'} сек; "
            f"статус {row.get('availability', row.get('available', '—'))}; {row.get('reason') or 'доступен'}; семьи {provenance}{suppression}."
        )
    lines.append("Все кандидаты (сравнение с HOLD):")
    for row in audit.get("candidates") or []:
        eligible = row.get("ranking_eligible", row.get("eligible"))
        marker = "выбран" if row.get("candidate_id") == selected else ("допустим" if eligible else "исключён")
        reason = row.get("ranking_reason") or row.get("reason")
        parameters = row.get("parameters") or {}
        params = ", ".join(f"{key}={item}" for key, item in parameters.items()) if isinstance(parameters, dict) else str(parameters)
        lines.append(
            f"• {row.get('policy') or '—'} ({row.get('candidate_id') or '—'}){f' · {params}' if params else ''}: {marker}; "
            f"Expected {_format(row.get('expected_net_r'), 'R')}; CVaR10 {_format(row.get('cvar10_net_r'), 'R')}; "
            f"ΔExpected/HOLD {_format(row.get('delta_expected_r'), 'R')}; балл {_format(row.get('score'))}"
            f"{'; ' + str(reason) if reason else ''}."
        )
        contributions = row.get("component_contributions") or {}
        if isinstance(contributions, list):
            contributions = {item.get("component_id"): item.get("contribution")
                             for item in contributions if isinstance(item, dict)}
        if row.get("candidate_id") == selected and isinstance(contributions, dict):
            lines.append("Вклад в балл выбранного действия: " + "; ".join(
                f"{labels.get(str(key), str(key))} {_format(item)}"
                for key, item in contributions.items()) + ".")
    for row in audit.get("counterfactuals") or []:
        component = str(row.get("excluded_component_id") or "—")
        lines.append(f"Без {labels.get(component, component)}: {row.get('selected_policy') or '—'} ({row.get('selected_candidate_id') or '—'}).")
    lines.append("Сравнение схем при неизменной экономике кандидатов:")
    for row in audit.get("scheme_comparisons") or []:
        lines.append(
            f"• {row.get('scheme') or '—'} → {row.get('selected_policy') or '—'}; "
            f"Expected {_format(row.get('expected_net_r'), 'R')}; CVaR10 {_format(row.get('cvar10_net_r'), 'R')}."
        )
    for row in audit.get("edge_families") or []:
        lines.append(f"Edge {row.get('family_id') or row.get('edge_family') or row.get('family') or row.get('name') or '—'}: "
                     f"{row.get('readiness') or row.get('status') or 'UNAVAILABLE'}; "
                     f"входы: {row.get('available', '—')}; прогноз: {row.get('forecast_available', '—')}; "
                     f"{row.get('reason') or 'причина не сообщена'}; "
                     f"нужно: {', '.join(str(item) for item in row.get('needs_data') or []) or '—'}.")
    return lines
