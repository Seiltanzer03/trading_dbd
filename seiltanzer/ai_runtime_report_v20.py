"""Production report semantics + one-call LLM shadow overlay.

V20 is presentation/observability only.  It does not alter policy math,
management_decision, execution authority or research authority.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx

from . import ai_verdict
from . import ai_verdict_v19 as _v19
from . import ai_provider_explanation as _provider
from .llm_decision_shadow import (
    _disagreement_category,
    _extract_json_object,
    _hard_guard,
    _quant_policy,
    _validate_model_payload,
    append_shadow_section,
    record_shadow_decision,
)


REPORT_VERSION = "ai-runtime-report-v20"
COMBINED_PROVIDER_MAX_TOKENS = 720
_INSTALLED = False

_COMBINED_PROMPT = """
PRODUCTION EXPLANATION + INDEPENDENT SHADOW MODE.
The server owns the production management_decision and every deterministic number.
You MUST NOT change, execute, or present the shadow policy as the production action.

Return ONLY one valid JSON object, no markdown:
{
  "explanation_ru": "120-180 Russian words explaining why the existing production policy is reasonable, its strongest evidence/limitations, data quality, and next recalculation trigger; do not issue a new trading instruction here",
  "shadow_decision": {
    "policy": "HOLD|CLOSE_10|CLOSE_25|CLOSE_50|EXIT|MOVE_TO_BE|TRAIL_GAMMA_FLIP|TIGHTEN_STOP|EXTEND_TAKE|REDUCE_TAKE|SCALE_OUT_ON_SPIKE|TIME_STOP",
    "confidence": 0.0,
    "reason_ru": "brief independent numerical rationale",
    "key_evidence": ["3-6 strongest facts"],
    "counter_evidence": ["0-4 facts against your own shadow choice"]
  }
}

For shadow_decision independently synthesize trade geometry, Expected/median/CVaR,
execution-MC/scenario geometry when actually available, option distribution and
IV/RV/VRP/skew/term/GEX/barrier/hazard derivatives, live tape/order-flow,
cross-asset/regime, metric changes, freshness/source quality, Active Edge/EDE only
within their published authority. Missing/UNAVAILABLE/COMPACTED is never zero.
Delayed/proxy data reduces confidence and is not automatically directional.
Correlated metrics from one family are not independent votes. Hard-CVaR eligibility
is mandatory. Never widen stops, average down, or add to a losing position.
First form the independent shadow opinion; quant_management_decision is only for
comparison. Shadow has zero production and zero automatic-execution authority.
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
    return {
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
    states = _operational_availability(snapshot)
    partial = any(value != "AVAILABLE" for value in states.values())
    operational = "PARTIAL" if partial else "FULL"
    detail = "; ".join(f"{key}={value}" for key, value in states.items())
    lines.insert(1, f"Операционная численная доступность: {operational}; {detail}.")
    lines.insert(
        2,
        "12/12 означает, что все decision-family имеют определённый контракт/роль; "
        "это НЕ означает, что каждая производная и каждая вероятность численно доступна в текущем snapshot.",
    )
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
        if ":" in line:
            displayed_names.add(line.split(":", 1)[0].strip())

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


def _provider_payload(content: str) -> tuple[str, dict[str, Any]]:
    payload = _extract_json_object(content)
    explanation = _provider._sanitize_explanation(payload.get("explanation_ru"))
    shadow_raw = payload.get("shadow_decision")
    if not isinstance(shadow_raw, dict):
        raise RuntimeError("combined_provider_missing_shadow")
    shadow = _validate_model_payload(shadow_raw)
    return explanation, shadow


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

    deterministic = ai_verdict.render_policy_report(authority)
    facts = _provider._explanation_facts(snapshot)
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": COMBINED_PROVIDER_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": ai_verdict.SYSTEM_PROMPT + "\n\n" + _COMBINED_PROMPT},
            {
                "role": "user",
                "content": (
                    "Bounded production snapshot:\n"
                    + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                    + "\n\nAuthoritative control facts (read-only):\n"
                    + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        ],
    }
    try:
        with httpx.Client(proxy=proxy, timeout=8, trust_env=False) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body,
                headers=_provider._provider_headers(key),
            )
            response.raise_for_status()
            try:
                result = response.json()
            except (TypeError, ValueError) as exc:
                raise RuntimeError("provider_bad_response: malformed JSON") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"OpenRouter HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter connection failed: {type(exc).__name__}") from exc

    content = ((result.get("choices") or [{}])[0].get("message") or {}).get("content")
    explanation, parsed_shadow = _provider_payload(content)
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
    }
    record_shadow_decision(shadow)
    combined = (
        deterministic.rstrip()
        + "\n\n**LLM EXPLANATION · OPENROUTER** —\n"
        + explanation
    )
    combined = append_shadow_section(combined, shadow)
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
