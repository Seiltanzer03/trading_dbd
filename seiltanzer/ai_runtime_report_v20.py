"""Production report semantics + one-call LLM shadow overlay.

V20 is presentation/observability only.  It does not alter policy math,
management_decision, execution authority or research authority.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import math
import os
import time
from typing import Any

import httpx

from . import ai_verdict
from . import ai_verdict_v19 as _v19
from . import ai_provider_explanation as _provider
from .llm_decision_shadow import (
    _disagreement_category,
    _extract_json_object,
    finalize_extended_shadow,
    _hard_guard,
    _quant_policy,
    _shadow_projection,
    _validate_model_payload,
    append_shadow_section,
    record_shadow_decision,
)
from .llm_shadow_working_action import build_working_action
from .ai_provider_contract import (POLICY_SCORE_ORDER, provider_response_format,
    normalize_family_wire, normalize_score_wire)


REPORT_VERSION = "ai-runtime-report-v20"
COMBINED_PROVIDER_MAX_TOKENS = 2400
INDEPENDENT_INPUT_MAX_BYTES = 60_000
PROVIDER_RESPONSE_MAX_BYTES = 20_000
_INSTALLED = False

_COMBINED_PROMPT = """
INDEPENDENT SHADOW MODE.
The server owns the production management_decision and every deterministic number.
The selected quant policy and all server winner outputs are deliberately withheld.
You MUST NOT change, execute, or present the shadow policy as the production action.

Return only JSON matching the supplied response schema, without markdown.
policy must be exactly one canonical action name from the schema, never a joined
list, an explanation, an unavailable label or a parameterized candidate ID.
Return policy_scores as exactly12 numbers in this fixed order:
""" + ', '.join(POLICY_SCORE_ORDER) + """.
The server writes the displayed report.
Keep reason_ru under100 characters; use1-2 concise key facts and at most1
counter-fact/invalidation. Empty evidence lists are allowed when facts are absent.
family_assessments is an array: use[] when no observed features support an opinion.

For shadow_decision independently synthesize trade geometry, Expected/median/CVaR,
execution-MC/scenario geometry when actually available, option distribution and
IV/RV/VRP/skew/term/GEX/barrier/hazard derivatives, live tape/order-flow,
cross-asset/regime, metric changes, freshness/source quality, Active Edge/EDE only
within their published authority. Missing/UNAVAILABLE/COMPACTED is never zero.
Delayed/proxy data reduces confidence and is not automatically directional.
Low reliability is not an absolute ban: only the server degraded-manual gate can authorize an override.
Never assert stress stability when its numeric checks are UNAVAILABLE.
Correlated metrics from one family are not independent votes. Hard-CVaR eligibility
is mandatory. Never widen stops, average down, or add to a losing position.
Return all 12 policy_scores as the ordered array, each from -1 to 1: relative preferences, not
probabilities or Expected R. Self-confidence never determines ensemble weight.
Assess all eight families using ONLY the exact numeric features supplied in
edge_family_facts, preserving observed versus preliminary proxy/scenario roles.
Include only supported opinions in family_assessments, with family_id,1-4 exact
feature_names,1-3 policy_preferences entries (one canonical policy and score per
entry), and reason_ru under100 characters. Omit unsupported families. These are current
manual LLM preferences sharing your existing budget, not validated historical
forecasts or additional independent experts. Do not invent missing consensus,
broker flow/carry, physical option forecasts or causal direction from a sign.
Labelled manual_preliminary/context_only features are permitted as preliminary
preferences with reduced attribution. BAR_PRESSURE_PROXY is price information,
not a reconstructed book/OFI; previous-release reference is not pre-news market
consensus; financing sensitivity uses stated assumptions, not verified swaps.
research_working_context contains observations and hypotheses, not calibrated
accuracy, profitability or another expert's chosen action. Preserve these roles
in explanations; never turn assumed costs into common Expected/CVaR or a risk pass.
No quant winner is supplied for comparison. A hard-guarded choice may become an exact
manual-confirmation action variant. It still has zero automatic-execution authority;
missing numeric stop/take/time parameters block the variant.
""".strip()


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _policy_metric(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _operational_availability(snapshot: dict[str, Any]) -> dict[str, str]:
    manager = snapshot.get("policy_manager") or {}
    geometry = snapshot.get("trade_geometry") or {}
    execution_mc = all(
        _number(geometry.get(key)) is not None
        for key in ("take_first", "stop_or_be_first", "no_touch")
    )
    scenario = manager.get("scenario_geometry") or {}
    scenario_ok = (_number(scenario.get("scenario_count")) or 0) > 0
    audit = manager.get("input_audit") or {}
    audit_rows = audit.get("rows")
    audit_detail = isinstance(audit_rows, dict) and bool(audit_rows)
    rule = manager.get("selection_rule") or {}
    indifference_ok = _number(rule.get("indifference_band_r")) is not None
    from .ai_report_semantics_guard import authoritative_current_price_available
    return {
        "authoritative_price": "AVAILABLE" if authoritative_current_price_available(snapshot) else "UNAVAILABLE",
        "execution_mc": "AVAILABLE" if execution_mc else "UNAVAILABLE",
        "scenario_geometry": "AVAILABLE" if scenario_ok else "UNAVAILABLE_OR_COMPACTED",
        "detailed_input_audit": "AVAILABLE" if audit_detail else "COMPACTED_OR_UNAVAILABLE",
        "economic_indifference_band": "AVAILABLE" if indifference_ok else "UNAVAILABLE",
    }


def _quality_lines(snapshot: dict) -> list[str]:
    lines = list(_BASE_QUALITY_LINES(snapshot))
    if lines:
        lines[0] = lines[0].replace(
            "Покрытие decision metrics:",
            "Контрактное покрытие семейств decision metrics:",
        )
        lines[0] = lines[0].replace('Input audit:', 'Контрактное наличие групп входов (не торговый допуск):')
    states = _operational_availability(snapshot)
    partial = any(value != "AVAILABLE" for value in states.values())
    operational = "PARTIAL" if partial else "FULL"
    detail = "; ".join(f"{key}={value}" for key, value in states.items())
    lines.insert(1, f"Операционная численная доступность: {operational}; {detail}.")
    lines.insert(2, "Покрытие семейств означает наличие контракта и роли; "
                 "НЕ означает численную доступность каждой производной и вероятности.")
    failures = []
    for key, label in (("edge_family_source_bundle_audit", "локальные факты"),
                       ("edge_family_market_context", "рыночные модели"),
                       ("edge_family_budget_status", "бюджет снимка")):
        row = snapshot.get(key) or {}
        if row.get("available") is False or row.get("excluded_roots"):
            failures.append(f'{label}: {str(row.get("reason") or "UNAVAILABLE")[:160]}')
            if row.get('failed_source_checks'):
                failures.append('не пройдены проверки: ' + ', '.join(row['failed_source_checks']))
    if failures:
        lines.append("Причины недоступности входов edge: " + "; ".join(failures) + ".")
    unified = (snapshot.get('policy_manager') or {}).get('unified_edge_ensemble') or {}
    if unified:
        components = unified.get('components') or []
        active = sum((_number(row.get('effective_weight')) or 0.) > 0. for row in components)
        families = unified.get('edge_families') or {}
        families = list(families.values()) if isinstance(families, dict) else families
        forecasts = sum(row.get('forecast_available') is True for row in families)
        working = sum(row.get('working_assessment_available') is True for row in families)
        facts = sum(row.get('available') is True for row in families)
        lines.insert(3, f'Активные компоненты ансамбля: {active}/{len(components)}. '
                     f'Доступные сейчас обученные прогнозы семейств: {forecasts}/{len(families)}; '
                     f'рабочие интерпретации: {working}/{len(families)}; входные факты: {facts}/{len(families)}. '
                     'Это не число обученных моделей в опубликованном архиве. '
                     'FULL выше означает вычислимость перечисленного workspace, не готовность всех edge и не точность прогнозов.')
    geometry = snapshot.get("trade_geometry") or {}
    entry = _number(geometry.get("entry"))
    active = _number(geometry.get("active_risk_barrier"))
    if entry is not None and active is not None and abs(entry - active) < 1e-8:
        lines.insert(3, "Нулевой нижний исход HOLD в модели связан с активным БУ "
                     "на цене входа; проскальзывание и издержки могут изменить фактический результат.")
    return lines


def _economic_body(snapshot: dict[str, Any]) -> list[str] | None:
    manager = snapshot.get("policy_manager") or {}
    rule = manager.get("selection_rule") or {}
    policies = manager.get("policies") or {}
    band = _number(rule.get("indifference_band_r"))
    if band is None or not isinstance(policies, dict):
        return None
    hold = policies.get("HOLD") or {}
    hold_e = _policy_metric(hold, "expected_final_r_net", "expected_final_r")
    hold_c = _policy_metric(hold, "cvar10_r_net", "cvar10_r")
    lines = [f"Зона безразличия Expected: {band:+.3f}R."]
    if manager.get("unified_edge_ensemble"):
        lines.insert(0, "Контрольная базовая bridge-модель: её собственные HOLD/Δ и зона безразличия. Итоговая экономика ансамбля опубликована в едином сравнительном банке.")
        if (snapshot.get('market_refresh') or {}).get('available'):
            lines[0] = 'Базовые политики пересчитаны по текущей котировке в общем сравнительном банке; прежние bridge-результаты не используются в этой таблице.'

    eligible = rule.get("eligible")
    alternatives: list[tuple[float, str, float, float | None]] = []
    if isinstance(eligible, list) and hold_e is not None:
        for name in eligible:
            if name == "HOLD" or name not in policies:
                continue
            row = policies.get(name) or {}
            expected = _policy_metric(row, "expected_final_r_net", "expected_final_r")
            cvar = _policy_metric(row, "cvar10_r_net", "cvar10_r")
            if expected is not None:
                alternatives.append((abs(expected - hold_e), str(name), expected, cvar))
    if alternatives:
        _distance, name, expected, cvar = min(alternatives)
        delta_e = expected - hold_e if hold_e is not None else None
        delta_c = cvar - hold_c if cvar is not None and hold_c is not None else None
        lines.append(
            f"Ближайшая другая NET-CVaR-eligible политика: {name}; "
            f"Expected против HOLD {delta_e:+.3f}R; "
            + (f"CVaR10 против HOLD {delta_c:+.3f}R." if delta_c is not None else "CVaR10 против HOLD —.")
        )
    else:
        lines.append("Другой NET-CVaR-eligible политики кроме HOLD сейчас нет.")

    exit_row = policies.get("EXIT") or {}
    exit_e = _policy_metric(exit_row, "expected_final_r_net", "expected_final_r")
    exit_c = _policy_metric(exit_row, "cvar10_r_net", "cvar10_r")
    if hold_e is not None and exit_e is not None:
        delta_e = exit_e - hold_e
        delta_c = exit_c - hold_c if exit_c is not None and hold_c is not None else None
        lines.append(
            f"Полный EXIT: Expected против HOLD {delta_e:+.3f}R; "
            + (f"CVaR10 против HOLD {delta_c:+.3f}R." if delta_c is not None else "CVaR10 против HOLD —.")
        )
    else:
        lines.append("Полный EXIT: сравнение с HOLD UNAVAILABLE в текущем compact snapshot.")
    return lines


def _repair_economic_section(text: str, snapshot: dict[str, Any]) -> str:
    body = _economic_body(snapshot)
    if not body:
        return text
    lines = text.splitlines()
    bounds = _v19._section(lines, "**ЭКОНОМИЧЕСКАЯ БЛИЗОСТЬ ПОЛИТИК**")
    if bounds is None:
        return text
    start, end = bounds
    lines[start + 1:end] = [*body, ""]
    return "\n".join(lines).strip()


def _metric_audit_lines(snapshot: dict) -> list[str]:
    base = list(_BASE_METRIC_AUDIT_LINES(snapshot))
    manager = snapshot.get("policy_manager") or {}
    evidence = manager.get("evidence") or {}
    state = manager.get("option_derivative_state") or evidence.get("option_derivative_state") or {}
    metrics = state.get("metrics") or {}
    probability_bounds = {"p_take", "p_stop", "p_no_touch", "h_take", "h_stop"}
    normalized_bounds = {"gex_force", "gex_stiffness"}
    boundary: set[str] = set()
    low_confidence: set[str] = set()
    for name, row in metrics.items() if isinstance(metrics, dict) else ():
        if not isinstance(row, dict):
            continue
        value = _number(row.get("value"))
        confidence = _number(row.get("confidence"))
        if confidence is not None and confidence < 0.25:
            low_confidence.add(str(name))
        if value is None:
            continue
        if name in probability_bounds and (value <= 1e-12 or value >= 1.0 - 1e-12):
            boundary.add(str(name))
        if name in normalized_bounds and abs(value) >= 1.0 - 1e-12:
            boundary.add(str(name))

    displayed_names: set[str] = set()
    for line in base[1:]:
        name = line.split(":", 1)[0].strip()
        if name in metrics:
            displayed_names.add(name)

    effective_boundary = boundary & displayed_names if displayed_names else boundary
    effective_low_confidence = low_confidence & displayed_names if displayed_names else low_confidence

    if len(base) > 1:
        base.insert(
            1,
            f"Audit summary: rows={len(displayed_names)}; boundary_values={len(effective_boundary)}; "
            f"confidence<25%={len(effective_low_confidence)}. Boundary value не означает 100% уверенности модели.",
        )
    for index, line in enumerate(base):
        name = line.split(":", 1)[0].strip() if ":" in line else ""
        notes = []
        if name in boundary:
            notes.append("BOUNDARY_VALUE: возможное насыщение/клиппинг; интерпретировать вместе с confidence/source quality")
        if name in low_confidence:
            notes.append("LOW_CONFIDENCE")
        if notes:
            base[index] = line.rstrip(".") + "; " + "; ".join(notes) + "."
    return base


def _normalize_structured_report(text: str, snapshot: dict) -> str:
    normalized = _BASE_NORMALIZE_STRUCTURED_REPORT(text, snapshot)
    normalized = _repair_economic_section(normalized, snapshot)
    return normalized


def _control_summary(snapshot: dict[str, Any]) -> str:
    """Server-verified facts; never publish unchecked provider prose as evidence."""
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    name = str(decision.get("policy") or "UNAVAILABLE")
    row = (manager.get("policies") or {}).get(name) or {}
    expected = _policy_metric(row, "expected_final_r_net", "expected_final_r")
    cvar = _policy_metric(row, "cvar10_r_net", "cvar10_r")
    gate = manager.get("gate") or {}
    from .management_contract import decision_reliability
    reliability = decision_reliability(snapshot)
    availability = _operational_availability(snapshot)
    number = lambda value: "нет расчёта" if value is None else f"{value:+.3f}R"
    return (
        f"Базовый quant-план до единого ранжирования: {name}; Expected {number(expected)}, CVaR10 {number(cvar)}. "
        f"Gate: {gate.get('status') or 'не опубликован'}; надёжность данных: "
        f"{reliability.get('level') or 'не опубликована'}. "
        f"Execution-MC: {availability['execution_mc']}; геометрия сценариев: "
        f"{availability['scenario_geometry']}. Текст комментария не является приказом; "
        "структурированный голос учитывается отдельно в едином выборе ниже. "
        "Исполнение у брокера требует отдельного подтверждения."
    )


def _decision_weights(snapshot: dict[str, Any], shadow: dict[str, Any]) -> str:
    manager = snapshot.get("policy_manager") or {}
    arbiter = manager.get("management_arbiter") or {}
    gate = manager.get("gate") or {}
    rule = manager.get("selection_rule") or {}
    combined_edge = (manager.get("combined_edge_soft_weight") or
                     rule.get("combined_edge_soft_weight") or {})
    exploratory = (manager.get("llm_edge_exploratory_weight") or
                   rule.get("llm_edge_exploratory_weight") or {})
    edge_weight = _number(combined_edge.get("weight_fraction"))
    exploratory_weight = _number(exploratory.get("weight_fraction"))
    if exploratory_weight is None:
        exploratory_weight = _number(exploratory.get("component_weight_fraction"))
    selected = (gate.get("degraded_authority_overlay") or {}).get("selected") or {}
    llm = shadow.get("policy") or "UNAVAILABLE"
    llm_role = ("структурированный независимый голос для единого ранжирования"
                if shadow.get("status") == "ok" and shadow.get("policy_scores")
                else "недоступный или заблокированный голос; активный вес 0")
    # This section is built before the final ensemble is attached. Inspect the
    # fresh validated preference vector, not an audit from a prior decision.
    scores = [_number(value) for value in (shadow.get("policy_scores") or {}).values()]
    scores = [value for value in scores if value is not None]
    if len(scores) > 1 and max(scores) == min(scores):
        families = shadow.get('family_assessments') or {}
        has_opinions = isinstance(families, dict) and any(isinstance(row, dict) and row for row in families.values())
        llm_role = ('общая оценка нейтральна; семейные оценки проходят отдельный допуск; итоговый вес показан в едином выборе'
                    if shadow.get('status') == 'ok' and has_opinions else
                    'голос не участвует; активный вес 0; причина CURRENT_LLM_NO_RELATIVE_PREFERENCE')
    return (
        "**ВЕСА И РОЛИ РЕШЕНИЯ** —\n"
        "Ниже — базовая диагностика до единого выбора, не итоговые веса компонентов. "
        "Фактические веса и окончательное действие опубликованы в разделе «ЕДИНЫЙ ВЫБОР ДЕЙСТВИЯ».\n"
        "Диагностический счёт: Expected + 0.35 × CVaR10; бонус +0.015R "
        "публикуется для диагностики и не определяет победителя. "
        "Подтверждённый overlay получает приоритет только после gate.\n"
        f"Active Edge, исторические LLM-гипотезы и mathematical edge: мягкий общий вес "
        f"{f'{edge_weight:.1%}' if edge_weight is not None else 'UNAVAILABLE'} "
        f"(лимит 40%); исследовательский LLM-компонент "
        f"{f'{exploratory_weight:.1%}' if exploratory_weight is not None else 'UNAVAILABLE'} "
        "(общий лимит 40%). Они меняют только ранжирование прошедших hard CVaR "
        "базовых политик и не меняют риск-порог.\n"
        f"Арбитр: {arbiter.get('winner') or 'UNAVAILABLE'}; "
        f"gate={gate.get('status') or 'UNAVAILABLE'}; "
        f"degraded overlay={'выбран' if selected else 'не выбран'}. "
        "Семейства подтверждений учитываются gate, а качество и свежесть "
        "ограничивают их авторитет; производные одной опционной цепочки "
        "не становятся независимыми голосами.\n"
        f"LLM-разбор текущего снимка: {llm}; {llm_role}. Это отдельный голос "
        "от исторических LLM-гипотез. Самооценка LLM не является "
        "калиброванной вероятностью и не отменяет hard CVaR."
    )


def _provider_payload(content: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(content, str) or len(content.encode("utf-8")) > PROVIDER_RESPONSE_MAX_BYTES:
        raise RuntimeError("combined_provider_invalid_response_budget")
    payload = _extract_json_object(content)
    # Commentary is never displayed as authority; the server renders the
    # report. Its absence/invalid imperative must not erase a valid independent
    # preference vector from the same response.
    try:
        explanation = _provider._sanitize_explanation(payload.get("explanation_ru"))
    except RuntimeError:
        explanation = ""
    shadow_raw = payload.get("shadow_decision")
    if not isinstance(shadow_raw, dict):
        raise RuntimeError("combined_provider_missing_shadow")
    shadow = _validate_model_payload(normalize_family_wire(normalize_score_wire(shadow_raw)))
    if "policy_scores" not in shadow:
        raise RuntimeError("combined_provider_missing_policy_scores")
    return explanation, shadow


def _independent_provider_input(snapshot: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
    """Restore bounded facts that the older explanation-only projection drops."""
    projected = _shadow_projection(snapshot)
    authoritative = _shadow_projection(authority)
    candidate_fields = (
        "policy", "status", "reason", "parameters", "expected_variant_net_r",
        "expected_hold_net_r", "expected_delta_vs_hold_r", "execution_cost_r",
        "worst_seed_cvar10_net_r", "worst_seed_hold_cvar10_net_r",
        "paired_delta_ci95_lower_r", "materiality_band_r", "hard_net_floor_r",
        "matched_seed_tail_gain_lower_r", "benefit_assessment",
        "production_authority", "automatic_execution_allowed",
    )
    projected["active_management_candidates"] = [
        {key: row[key] for key in candidate_fields if key in row}
        for row in authoritative.get("active_management_candidates") or []
        if isinstance(row, dict)
    ][:7]
    for key in ("edge_family_facts", "shadow_contract", "edge_regime", "market_regime", "research_working_context"):
        if key in authoritative:
            projected[key] = authoritative[key]
    from .ai_provider_guard import _bounded
    for key in ("execution_cost_model", "scenario_geometry", "management_model_scope"):
        value = (authoritative.get("policy_manager") or {}).get(key)
        if isinstance(value, dict):
            projected["policy_manager"][key] = _bounded(value)
    if isinstance(authority.get("edge_family_source_bundle_audit"), dict):
        projected["edge_family_source_bundle_audit"] = _bounded(authority["edge_family_source_bundle_audit"])
    # The observed family adapter supplies exact numeric macro facts and their
    # clocks; a compact event summary supplies context without a research ledger.
    if "macro_context_v1" in authoritative:
        projected["macro_context_v1"] = _bounded(authoritative["macro_context_v1"])
    return projected


def request_explanation_with_shadow(
    snapshot: dict[str, Any],
    *,
    authoritative_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One bounded provider call returns explanation + independent shadow."""
    from .ai_report_semantics_guard import authoritative_current_price_available

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не настроен на сервере")
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    authority = authoritative_snapshot if isinstance(authoritative_snapshot, dict) else snapshot
    if not authoritative_current_price_available(authority):
        raise RuntimeError("provider_explanation_blocked_missing_authoritative_price")

    from .ai_request_trace import current_trace
    trace = current_trace()
    phase = trace.span if trace is not None else lambda _stage: nullcontext()
    with phase('provider_render'):
        deterministic = ai_verdict.render_policy_report(authority)
    with phase('provider_prepare'):
        # Mask at the actual transport boundary. The deterministic renderer and
        # post-response guard retain full authority locally; no winner-bearing
        # explanation facts or verdict system prompt are uploaded to the model.
        independent_input = _independent_provider_input(snapshot, authority)
        from .decision_research import validate_no_future_timestamps
        validate_no_future_timestamps(independent_input, float(authority["captured_ts"]))
        frozen_input_json = json.dumps(independent_input, ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(frozen_input_json.encode("utf-8")) > INDEPENDENT_INPUT_MAX_BYTES:
            raise RuntimeError("combined_provider_independent_input_budget_exceeded")
        body = {
            "model": model,
            "temperature": 0.0,
            "max_tokens": COMBINED_PROVIDER_MAX_TOKENS,
            "response_format": provider_response_format(),
            "provider": {"require_parameters": True, "sort": "latency"},
            "messages": [
                {"role": "system", "content": _COMBINED_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Bounded independent decision input:\n" + frozen_input_json
                    ),
                },
            ],
        }
        provider_request_json = json.dumps(body, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False)
    if trace is not None:
        trace.provider_metrics(input_bytes=len(frozen_input_json.encode('utf-8')),
            request_bytes=len(provider_request_json.encode('utf-8')),
            max_output_tokens=COMBINED_PROVIDER_MAX_TOKENS)
    try:
        with phase('provider_http'):
            from .ai_provider_transport import post_json
            response = post_json("https://openrouter.ai/api/v1/chat/completions",
                                 body=body, proxy=proxy,
                                 headers=_provider._provider_headers(key), timeout=8)
            response.raise_for_status()
        with phase('provider_decode'):
            try:
                result = response.json()
                response_received_ts = time.time()
            except (TypeError, ValueError) as exc:
                raise RuntimeError("provider_bad_response: malformed JSON") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"OpenRouter HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter connection failed: {type(exc).__name__}") from exc

    if trace is not None:
        # Extract only bounded counters/enums. Missing or malformed usage must
        # never change response admission, and no body/model/header is logged.
        try:
            choices = result.get('choices') if isinstance(result, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = choice.get('message')
            output = message.get('content') if isinstance(message, dict) else None
            usage = result.get('usage') if isinstance(result, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            raw = getattr(response, 'content', None)
            trace.provider_metrics(
                response_bytes=len(raw) if isinstance(raw, bytes) else None,
                output_bytes=len(output.encode('utf-8')) if isinstance(output, str) else None,
                finish_reason=choice.get('finish_reason'),
                prompt_tokens=usage.get('prompt_tokens'),
                completion_tokens=usage.get('completion_tokens'),
                total_tokens=usage.get('total_tokens'))
        except Exception:
            pass  # Diagnostic extraction cannot affect the provider result.
    with phase('provider_validate'):
        content = ((result.get("choices") or [{}])[0].get("message") or {}).get("content")
        _explanation, parsed_shadow = _provider_payload(content)
        quant_policy = _quant_policy(authority)
        guard_ok, guard_reasons = _hard_guard(authority, parsed_shadow["policy"])
        disagreement_cat = _disagreement_category(parsed_shadow["policy"], quant_policy)
        shadow = {
            "version": "llm-decision-shadow-v1",
            "status": "ok" if guard_ok else "blocked",
            "production_authority": False,
            "automatic_execution_allowed": False,
            "model": result.get("model", model),
            "quant_policy": quant_policy,
            "policy": parsed_shadow["policy"],
            "confidence": parsed_shadow["confidence"],
            "agreement": parsed_shadow["policy"] == quant_policy if quant_policy else None,
            "disagreement_category": disagreement_cat,
            "blocked_by_hard_guard": not guard_ok,
            "hard_guard_reasons": guard_reasons,
            "reason_ru": parsed_shadow["reason_ru"],
            "key_evidence": parsed_shadow["key_evidence"],
            "counter_evidence": parsed_shadow["counter_evidence"],
            "policy_scores": parsed_shadow["policy_scores"],
            "evidence_families": parsed_shadow["evidence_families"],
            "family_assessments": parsed_shadow.get("family_assessments", {}),
            "family_assessment_rejections": parsed_shadow.get("family_assessment_rejections", {}),
            "invalidation_conditions": parsed_shadow["invalidation_conditions"],
            "selection_masked": True,
            "captured_ts": authority.get("captured_ts"),
            "input_captured_ts": authority.get("captured_ts"),
            "provider_response_received_ts": response_received_ts,
            "input_contract": {
                "version": "independent-llm-transport-v1",
                "quant_selection_masked": True,
                "all_policy_scores_required": True,
                "policy_score_wire_version": "ordered-vector-v1",
                "policy_score_order": list(POLICY_SCORE_ORDER),
                "input_captured_ts": authority.get("captured_ts"),
                "frozen_input_json": frozen_input_json,
                "frozen_input_sha256": hashlib.sha256(frozen_input_json.encode("utf-8")).hexdigest(),
                "provider_request_json": provider_request_json,
                "provider_request_sha256": hashlib.sha256(provider_request_json.encode("utf-8")).hexdigest(),
                "available_evidence_family_ids": (independent_input.get("shadow_contract") or {}).get(
                    "available_evidence_family_ids", []),
            },
            "provider_response_json": content,
            "provider_response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        with phase('provider_extended_validation'):
            shadow["working_action"] = build_working_action(authority, shadow)
            finalize_extended_shadow(authority, shadow)
        from .llm_decision_shadow import audit_report_claims
        audit_report_claims(authority, shadow)
        record_shadow_decision(shadow)
        combined = (
            deterministic.rstrip()
            + "\n\n**ПРОВЕРЕННЫЙ ВЫВОД** —\n"
            + _control_summary(authority)
        )
        combined = append_shadow_section(combined, shadow)
        combined = combined.replace("\n\n**ПРОВЕРЕННЫЙ ВЫВОД**",
                                    "\n\n" + _decision_weights(authority, shadow)
                                    + "\n\n**ПРОВЕРЕННЫЙ ВЫВОД**", 1)
        violations = ai_verdict._validate_model_report(combined, authority)
        hard_violations = [
            violation for violation in violations
            if violation == "изменено рассчитанное действие"
            or violation.startswith("нет политики ")
            or violation.startswith("изменено или пропущено ")
        ]
        if hard_violations:
            raise RuntimeError("combined_provider_hard_integrity_failure")
        return {
            "verdict": combined,
            "model": result.get("model", model),
            "captured_ts": authority.get("captured_ts"),
            "provider_mode": "llm_explanation_plus_decision_shadow",
            "llm_shadow_decision": shadow,
            "report_version": REPORT_VERSION,
            "validation_warnings": [
                violation for violation in violations if violation not in hard_violations
            ],
        }


def install_ai_runtime_report_v20() -> None:
    """Install after ai_provider_explanation so production route uses one-call shadow."""
    global _INSTALLED, _BASE_QUALITY_LINES, _BASE_METRIC_AUDIT_LINES
    global _BASE_NORMALIZE_STRUCTURED_REPORT
    if _INSTALLED:
        return
    _BASE_QUALITY_LINES = _v19._quality_lines
    _BASE_METRIC_AUDIT_LINES = _v19._metric_audit_lines
    _BASE_NORMALIZE_STRUCTURED_REPORT = _v19.normalize_structured_report
    _v19._quality_lines = _quality_lines
    _v19._metric_audit_lines = _metric_audit_lines
    _v19.normalize_structured_report = _normalize_structured_report
    _provider.request_explanation = request_explanation_with_shadow
    _INSTALLED = True
