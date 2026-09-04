"""Independent LLM trade-management opinion with zero production authority.

The deterministic policy manager remains the only source of production execution
state. This module asks the configured LLM for a separate, machine-readable
opinion so disagreements can be observed before any future authority change.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx


SHADOW_VERSION = "llm-decision-shadow-v1"
VALID_POLICIES = ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
_MAX_REASON_CHARS = 1200
_MAX_EVIDENCE_ITEMS = 6
_MAX_EVIDENCE_CHARS = 320
_DEFAULT_SHADOW_TIMEOUT_SEC = 10.0
_MAX_SHADOW_TIMEOUT_SEC = 15.0

SHADOW_SYSTEM_PROMPT = """Ты — независимый риск-менеджер уже ОТКРЫТОЙ сделки.
Это SHADOW-анализ: твой ответ НЕ исполняется и НЕ меняет production policy.

Самостоятельно выбери ровно одну политику:
HOLD, CLOSE_10, CLOSE_25, CLOSE_50 или EXIT.

Цель: максимизировать ожидаемый итог сделки с учётом хвостового риска и качества
данных. Не копируй mechanically quant decision: можешь согласиться или не согласиться.
При этом hard CVaR feasible set является обязательным ограничением. Нельзя расширять
стоп, усреднять убыточную позицию, добавлять позицию, менять TAKE/STOP или придумывать
шестую политику.

Анализируй СОВОКУПНОСТЬ доступных фактов: текущую геометрию и R; Expected/median/CVaR
всех политик; execution-MC и scenario geometry; option-distribution и её производные
(IV/RV/VRP/skew/term/GEX/barrier/hazard); live tape/order-flow; cross-asset/regime;
изменения метрик относительно предыдущего состояния; качество и свежесть источников;
Active Edge и EDE только в пределах явно опубликованного authority. Не считай missing
или compacted значение нулём. Proxy/delayed источник должен уменьшать уверенность, но
не может автоматически превращаться ни в bullish, ни в bearish аргумент. Не считай
несколько коррелированных метрик одной семьи независимыми голосами.

quant_management_decision дан только для сравнения. Сначала сформируй собственное
решение по данным, затем объясни, почему оно совпадает или расходится с quant.

Ответ ТОЛЬКО валидным JSON-объектом без markdown и без текста снаружи:
{
  "policy": "HOLD|CLOSE_10|CLOSE_25|CLOSE_50|EXIT",
  "confidence": 0.0,
  "reason_ru": "краткое числовое объяснение решения",
  "key_evidence": ["3-6 самых важных аргументов с числами, если они доступны"],
  "counter_evidence": ["0-4 важных аргумента против собственного решения"]
}
confidence — число от 0 до 1. Не придумывай отсутствующие числа."""


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _quant_policy(snapshot: dict[str, Any]) -> str | None:
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    recommendation = manager.get("recommendation") or {}
    policy = decision.get("policy") or recommendation.get("policy")
    return str(policy) if policy in VALID_POLICIES else None


def _shadow_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep decision-relevant compact facts without duplicating research ledgers."""
    manager = snapshot.get("policy_manager") or {}
    manager_keys = (
        "management_decision",
        "recommendation",
        "policies",
        "selection_rule",
        "risk_constraint",
        "execution_cost_model",
        "scenario_geometry",
        "risk_tradeoff",
        "economic_indifference",
        "raw_optimizer_stability",
        "stability",
        "gate",
        "management_arbiter",
        "evidence",
        "option_derivative_state",
        "option_center",
        "state_change_attribution",
        "counterfactual_attribution",
        "metric_changes",
        "monte_carlo_validation",
        "active_edge_provisional_weight",
        "inputs",
        "input_audit",
        "management_model_scope",
    )
    root_keys = (
        "captured_ts",
        "trade_id",
        "time_context",
        "strategy",
        "trade_geometry",
        "position_state",
        "observation",
        "metric_coverage",
        "metric_availability_contract",
        "report_integrity",
        "ede_causal_context",
        "ede_prospective_shadow",
        "active_edge_context",
    )
    projection = {key: snapshot[key] for key in root_keys if key in snapshot}
    projection["policy_manager"] = {
        key: manager[key] for key in manager_keys if key in manager
    }
    projection["shadow_contract"] = {
        "version": SHADOW_VERSION,
        "production_authority": False,
        "automatic_execution_allowed": False,
        "quant_management_decision": manager.get("management_decision"),
        "valid_policies": list(VALID_POLICIES),
    }
    return projection


def _extract_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("shadow_invalid_json") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("shadow_invalid_payload")
    return payload


def _bounded_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_chars]


def _bounded_text_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value[:max_items]:
        text = _bounded_text(item, max_chars=_MAX_EVIDENCE_CHARS)
        if text:
            output.append(text)
    return output


def _validate_model_payload(payload: dict[str, Any]) -> dict[str, Any]:
    policy = str(payload.get("policy") or "").strip().upper()
    if policy not in VALID_POLICIES:
        raise RuntimeError("shadow_invalid_policy")
    confidence = _number(payload.get("confidence"))
    if confidence is None or not 0.0 <= confidence <= 1.0:
        raise RuntimeError("shadow_invalid_confidence")
    reason = _bounded_text(payload.get("reason_ru"), max_chars=_MAX_REASON_CHARS)
    if not reason:
        raise RuntimeError("shadow_missing_reason")
    return {
        "policy": policy,
        "confidence": round(confidence, 4),
        "reason_ru": reason,
        "key_evidence": _bounded_text_list(
            payload.get("key_evidence"), max_items=_MAX_EVIDENCE_ITEMS),
        "counter_evidence": _bounded_text_list(
            payload.get("counter_evidence"), max_items=4),
    }


def _hard_guard(snapshot: dict[str, Any], policy: str) -> tuple[bool, list[str]]:
    """Fail closed against the published hard-risk/CVaR contract."""
    manager = snapshot.get("policy_manager") or {}
    rule = manager.get("selection_rule") or {}
    policies = manager.get("policies") or {}
    if not policies:
        policies = ((snapshot.get("report_integrity") or {}).get("policies") or {})

    reasons: list[str] = []
    eligible = rule.get("eligible")
    row = policies.get(policy) if isinstance(policies, dict) else None
    floor = _number(rule.get("cvar_floor_r"))
    cvar = _number((row or {}).get("cvar10_r")) if isinstance(row, dict) else None

    if isinstance(eligible, list):
        # An explicitly published empty feasible set means no shadow policy is
        # admissible. Do not silently turn [] into "guard unavailable" or PASS.
        if policy not in eligible:
            reasons.append("POLICY_OUTSIDE_PUBLISHED_CVAR_FEASIBLE_SET")
    elif floor is None or cvar is None:
        # The report may still display the LLM opinion, but it must never call
        # the risk check PASS when the hard constraint cannot be evaluated.
        reasons.append("HARD_CVAR_GUARD_UNAVAILABLE")

    if floor is not None and cvar is not None and cvar < floor - 1e-12:
        reasons.append("POLICY_CVAR10_BELOW_HARD_FLOOR")

    return (not reasons), reasons


def unavailable_shadow(snapshot: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "version": SHADOW_VERSION,
        "status": "unavailable",
        "production_authority": False,
        "automatic_execution_allowed": False,
        "quant_policy": _quant_policy(snapshot),
        "policy": None,
        "confidence": None,
        "agreement": None,
        "blocked_by_hard_guard": False,
        "hard_guard_reasons": [],
        "reason_code": _bounded_text(reason, max_chars=96) or "SHADOW_UNAVAILABLE",
    }


def request_shadow_decision(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Ask the configured provider for an independent, non-authoritative policy."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("shadow_provider_not_configured")
    model = (
        os.environ.get("OPENROUTER_SHADOW_MODEL", "").strip()
        or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    )
    timeout = (
        _number(os.environ.get("OPENROUTER_SHADOW_TIMEOUT_SEC"))
        or _DEFAULT_SHADOW_TIMEOUT_SEC
    )
    # The primary verdict is already a provider call. Keep shadow latency
    # tightly bounded so this additive research layer cannot create a gateway
    # timeout on an otherwise successful verdict request.
    timeout = max(5.0, min(timeout, _MAX_SHADOW_TIMEOUT_SEC))
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": SHADOW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Текущий bounded snapshot для независимого SHADOW-решения:\n"
                    + json.dumps(
                        _shadow_projection(snapshot),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            },
        ],
    }
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    try:
        with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Seiltanzer-Terminal/1.0",
                    "HTTP-Referer": "https://seiltanzer-terminal.local",
                    "X-Title": "Seiltanzer Terminal LLM Decision Shadow",
                },
            )
            response.raise_for_status()
            try:
                provider_payload = response.json()
            except (TypeError, ValueError) as exc:
                raise RuntimeError("shadow_provider_bad_response") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"shadow_provider_http_{exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("shadow_provider_connection_failed") from exc

    content = (
        provider_payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content")
    )
    parsed = _validate_model_payload(_extract_json_object(content))
    quant_policy = _quant_policy(snapshot)
    guard_ok, guard_reasons = _hard_guard(snapshot, parsed["policy"])
    return {
        "version": SHADOW_VERSION,
        "status": "ok" if guard_ok else "blocked",
        "production_authority": False,
        "automatic_execution_allowed": False,
        "model": provider_payload.get("model") or model,
        "quant_policy": quant_policy,
        "policy": parsed["policy"],
        "confidence": parsed["confidence"],
        "agreement": (
            parsed["policy"] == quant_policy if quant_policy is not None else None),
        "blocked_by_hard_guard": not guard_ok,
        "hard_guard_reasons": guard_reasons,
        "reason_ru": parsed["reason_ru"],
        "key_evidence": parsed["key_evidence"],
        "counter_evidence": parsed["counter_evidence"],
    }


def append_shadow_section(report: str, shadow: dict[str, Any]) -> str:
    """Append one explicit research-only section to the human report."""
    lines = [report.rstrip(), "", "**LLM SHADOW DECISION · БЕЗ PRODUCTION AUTHORITY** —"]
    status = shadow.get("status")
    quant_policy = shadow.get("quant_policy") or "—"
    if status == "unavailable":
        lines.append(
            f"Shadow LLM: UNAVAILABLE ({shadow.get('reason_code') or 'SHADOW_UNAVAILABLE'}). "
            f"Текущее production-решение quant остаётся {quant_policy} без изменений."
        )
        return "\n".join(lines).strip()

    policy = shadow.get("policy") or "—"
    confidence = _number(shadow.get("confidence"))
    confidence_text = "—" if confidence is None else f"{confidence * 100:.1f}%"
    agreement = shadow.get("agreement")
    agreement_text = (
        "совпадает" if agreement is True
        else ("расходится" if agreement is False else "не сопоставлено")
    )
    guard = (
        "BLOCKED/UNVERIFIED hard-risk guard"
        if shadow.get("blocked_by_hard_guard")
        else "PASS hard-risk guard"
    )
    lines.append(
        f"Quant: {quant_policy}. Независимый LLM: {policy}; confidence {confidence_text}; "
        f"с quant {agreement_text}; {guard}."
    )
    if shadow.get("hard_guard_reasons"):
        lines.append("Hard guard: " + "; ".join(shadow["hard_guard_reasons"]) + ".")
    if shadow.get("reason_ru"):
        lines.append("Почему LLM так решил: " + str(shadow["reason_ru"]))
    if shadow.get("key_evidence"):
        lines.append("Ключевые аргументы LLM: " + " | ".join(shadow["key_evidence"]))
    if shadow.get("counter_evidence"):
        lines.append("Контраргументы LLM: " + " | ".join(shadow["counter_evidence"]))
    lines.append(
        "Это исследовательское сравнение. Оно не меняет management_decision, "
        "не создаёт ордер и не расширяет execution authority."
    )
    return "\n".join(lines).strip()
