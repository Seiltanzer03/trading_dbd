"""Independent, structured LLM input to the guarded management ensemble.

The opinion has no direct execution authority. The common ranking may use its
preferences, and the ordinary risk and publication guards still own execution.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any

import httpx


SHADOW_VERSION = "llm-decision-shadow-v1"
BASE_POLICIES = ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
EXTENDED_DYNAMIC_POLICIES = (
    "MOVE_TO_BE",
    "TRAIL_GAMMA_FLIP",
    "TIGHTEN_STOP",
    "EXTEND_TAKE",
    "REDUCE_TAKE",
    "SCALE_OUT_ON_SPIKE",
    "TIME_STOP",
)
VALID_POLICIES = BASE_POLICIES + EXTENDED_DYNAMIC_POLICIES
_MAX_REASON_CHARS = 1200
_MAX_EVIDENCE_ITEMS = 6
_MAX_EVIDENCE_CHARS = 320
_DEFAULT_SHADOW_TIMEOUT_SEC = 10.0
_MAX_SHADOW_TIMEOUT_SEC = 15.0

SHADOW_SYSTEM_PROMPT = """Ты — независимый риск-менеджер уже ОТКРЫТОЙ сделки.
Твой ответ является входом общего ансамбля; он самостоятельно НЕ исполняется.

Самостоятельно выбери ровно одну профессиональную политику:
1. Базовые:
   - HOLD: удержание позиции по текущей стратегии без изменения ордеров;
   - CLOSE_10 / CLOSE_25 / CLOSE_50: частичное закрытие 10%, 25% или 50% объема по рынку;
   - EXIT: немедленное полное закрытие позиции по рынку.
2. Динамический стоп (Stop Management):
   - MOVE_TO_BE: перенос стоп-ордера на цену входа в безубыток;
   - TRAIL_GAMMA_FLIP: перемещение стоп-ордера на расчетный уровень Zero-Gamma дилеров;
   - TIGHTEN_STOP: подтягивание стоп-ордера ближе текущей цены (под ближайшую структуру/ступень).
3. Управление тейком (Take-Profit Management):
   - EXTEND_TAKE: перенос лимитного тейк-профита дальше первоначальной цели;
   - REDUCE_TAKE: подтягивание лимитного тейк-профита ближе к текущей цене.
4. Кондициональные и временные (Conditional / Time-based):
   - SCALE_OUT_ON_SPIKE: частичный сброс объема лимитным ордером в ликвидность на импульсе;
   - TIME_STOP: принудительный выход из позиции по истечении допустимого времени удержания.

Цель: самостоятельно максимизировать ожидаемый итог сделки с учётом хвостового риска и качества данных.
Не копируй mechanically quant decision: оценивай ситуацию автономно.
При этом hard CVaR feasible set является обязательным ограничением. Нельзя расширять
стоп, усреднять убыточную позицию или добавлять позицию.

Анализируй СОВОКУПНОСТЬ доступных фактов: текущую геометрию и R; Expected/median/CVaR
всех политик; execution-MC и scenario geometry; option-distribution и её производные
(IV/RV/VRP/skew/term/GEX/barrier/hazard); live tape/order-flow; cross-asset/regime;
изменения метрик относительно предыдущего состояния; качество и свежесть источников;
Active Edge и EDE только в пределах явно опубликованного authority. Не считай missing
или compacted значение нулём. Proxy/delayed источник должен уменьшать уверенность, но
не может автоматически превращаться ни в bullish, ни в bearish аргумент. Не считай
несколько коррелированных метрик одной семьи независимыми голосами.

Выбранная quant-политика скрыта, чтобы не привязывать тебя к чужому решению.
Оцени каждое действие независимо по доступным фактам и его экономике.

Ответ ТОЛЬКО валидным JSON-объектом без markdown и без текста снаружи:
{
  "policy": "HOLD|CLOSE_10|CLOSE_25|CLOSE_50|EXIT|MOVE_TO_BE|TRAIL_GAMMA_FLIP|TIGHTEN_STOP|EXTEND_TAKE|REDUCE_TAKE|SCALE_OUT_ON_SPIKE|TIME_STOP",
  "confidence": 0.0,
  "policy_scores": {"HOLD": 0.0, "CLOSE_10": 0.0, "CLOSE_25": 0.0, "CLOSE_50": 0.0, "EXIT": 0.0, "MOVE_TO_BE": 0.0, "TRAIL_GAMMA_FLIP": 0.0, "TIGHTEN_STOP": 0.0, "EXTEND_TAKE": 0.0, "REDUCE_TAKE": 0.0, "SCALE_OUT_ON_SPIKE": 0.0, "TIME_STOP": 0.0},
  "reason_ru": "краткое числовое объяснение решения",
  "key_evidence": ["3-6 самых важных аргументов с числами, если они доступны"],
  "counter_evidence": ["0-4 важных аргумента против собственного решения"],
  "evidence_families": ["фактические семейства из shadow_contract.available_evidence_family_ids; option_distribution, price_path"],
  "invalidation_conditions": ["проверяемые условия отмены предпочтения"]
}
confidence — самооценка от 0 до 1, она не задаёт вес модели. policy_scores —
относительные предпочтения от -1 до 1 для всех 12 действий, НЕ вероятность и
НЕ Expected в R. Лучшее допустимое действие должно иметь наибольшую оценку.
Недоступные данные нельзя выдумывать; недопустимое действие не выбирай."""


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
        "evidence",
        "option_derivative_state",
        "option_center",
        "state_change_attribution",
        "counterfactual_attribution",
        "metric_changes",
        "monte_carlo_validation",
        "active_edge_provisional_weight",
        "llm_edge_exploratory_weight",
        "combined_edge_soft_weight",
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
        "macro_context_v1",
        "edge_regime", "market_regime",
    )
    projection = {key: snapshot[key] for key in root_keys if key in snapshot}
    projection["policy_manager"] = {
        key: manager[key] for key in manager_keys if key in manager
    }
    projection["shadow_contract"] = {
        "version": SHADOW_VERSION,
        "production_authority": False,
        "automatic_execution_allowed": False,
        "quant_selection_masked": True,
        "valid_policies": list(VALID_POLICIES),
    }
    from .edge_family_adapters import build_edge_family_evidence
    observed = build_edge_family_evidence(snapshot).get("families") or {}
    projection["edge_family_facts"] = {family: {
        key: row[key] for key in ("available", "reason", "features", "feature_provenance", "source_ids",
                                  "evidence_family_ids", "observed_ts", "published_ts",
                                  "received_ts", "needs_data", "budget_excluded_roots")
        if key in row} for family, row in observed.items()}
    families = {"option_distribution", "price_path"}
    for row in observed.values():
        if row.get("available"):
            for family in row.get("evidence_family_ids") or []:
                families.add("option_distribution" if str(family).endswith(":option_distribution") else str(family))
    projection["shadow_contract"]["available_evidence_family_ids"] = sorted(families)
    if isinstance(projection["policy_manager"].get("gate"), dict):
        projection["policy_manager"]["gate"] = {key: value for key, value in projection["policy_manager"]["gate"].items()
                                                 if key != "policy"}
    projection["active_management_candidates"] = snapshot.get("active_management_candidates") or []
    # Some nested audit structures also repeat the final picked policy. Keep
    # economics and source facts, but withhold all server preference outputs.
    hidden = {"management_decision", "effective_management_decision", "recommendation",
              "management_arbiter", "winner", "selected_policy", "effective_policy",
              "raw_optimizer_policy", "candidate_policy", "quant_policy",
              "raw_policy", "provisional_policy", "execution_policy", "model_policy",
              "raw_policy_with_edge", "raw_policy_without_edge", "raw_policy_without_mathematical_edge",
              "selected", "selected_candidate", "unified_edge_ensemble", "strategy_next_step"}
    def mask(value):
        if isinstance(value, dict):
            return {key: mask(child) for key, child in value.items() if key not in hidden}
        if isinstance(value, list):
            return [mask(child) for child in value]
        return value
    return mask(projection)


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
    result = {
        "policy": policy,
        "confidence": round(confidence, 4),
        "reason_ru": reason,
        "key_evidence": _bounded_text_list(
            payload.get("key_evidence"), max_items=_MAX_EVIDENCE_ITEMS),
        "counter_evidence": _bounded_text_list(
            payload.get("counter_evidence"), max_items=4),
        "evidence_families": _bounded_text_list(payload.get("evidence_families"), max_items=8),
        "invalidation_conditions": _bounded_text_list(payload.get("invalidation_conditions"), max_items=4),
    }
    scores = payload.get("policy_scores")
    if scores is not None:
        if not isinstance(scores, dict) or set(scores) != set(VALID_POLICIES):
            raise RuntimeError("shadow_invalid_policy_scores")
        parsed_scores = {name: _number(value) for name, value in scores.items()}
        if any(isinstance(scores[name], bool) or value is None or not -1 <= value <= 1
               for name, value in parsed_scores.items()):
            raise RuntimeError("shadow_invalid_policy_scores")
        result["policy_scores"] = parsed_scores
    if 'family_assessments' in payload:
        from .edge_family_working import isolate_family_assessments
        result['family_assessments'], result['family_assessment_rejections'] = isolate_family_assessments(
            payload['family_assessments'])
    return result


def _disagreement_category(shadow_policy: str, quant_policy: str | None) -> str | None:
    if quant_policy is None or shadow_policy == quant_policy:
        return None
    if shadow_policy in ("MOVE_TO_BE", "TRAIL_GAMMA_FLIP", "TIGHTEN_STOP"):
        return "DYNAMIC_STOP_MANAGEMENT"
    if shadow_policy in ("EXTEND_TAKE", "REDUCE_TAKE"):
        return "TAKE_PROFIT_MANAGEMENT"
    if shadow_policy in ("SCALE_OUT_ON_SPIKE", "TIME_STOP"):
        return "CONDITIONAL_TIME_MANAGEMENT"
    if quant_policy == "HOLD" and shadow_policy in ("CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        return "EARLY_DERISK"
    if quant_policy in ("CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT") and shadow_policy == "HOLD":
        return "HIGHER_CONVICTION_HOLD"
    return "POLICY_DIVERGENCE"


def _hard_guard(snapshot: dict[str, Any], policy: str) -> tuple[bool, list[str]]:
    """Check base feasible set; extended policies need a second path replay."""
    manager = snapshot.get("policy_manager") or {}
    rule = manager.get("selection_rule") or {}
    policies = manager.get("policies") or {}
    if not policies:
        policies = ((snapshot.get("report_integrity") or {}).get("policies") or {})

    reasons: list[str] = []
    eligible = rule.get("eligible")
    floor = _number(rule.get("cvar_floor_r"))

    if policy in EXTENDED_DYNAMIC_POLICIES:
        # This is only a preliminary HOLD feasibility check. Altering a stop,
        # take or holding time can change the outcome distribution. The caller
        # must also run finalize_extended_shadow below before publication.
        effective_base = "HOLD"
        row = policies.get(effective_base) if isinstance(policies, dict) else None
        cvar = _number((row or {}).get("cvar10_r")) if isinstance(row, dict) else None

        if isinstance(eligible, list):
            if effective_base not in eligible:
                reasons.append("POLICY_OUTSIDE_PUBLISHED_CVAR_FEASIBLE_SET")
        elif floor is None or cvar is None:
            reasons.append("HARD_CVAR_GUARD_UNAVAILABLE")

        if floor is not None and cvar is not None and cvar < floor - 1e-12:
            reasons.append("POLICY_CVAR10_BELOW_HARD_FLOOR")
    else:
        row = policies.get(policy) if isinstance(policies, dict) else None
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


def finalize_extended_shadow(snapshot: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    """Block extended actions unless their own paths pass the quantitative gate."""
    policy = shadow.get("policy")
    if policy not in EXTENDED_DYNAMIC_POLICIES:
        return shadow
    from .extended_policy_evaluation import evaluate_extended_action
    from .llm_shadow_working_action import build_working_action

    evaluation = evaluate_extended_action(snapshot, shadow.get("working_action") or {})
    shadow["quant_evaluation"] = evaluation
    if evaluation["status"] != "eligible":
        shadow["status"] = "blocked"
        shadow["blocked_by_hard_guard"] = True
        shadow["hard_guard_reasons"] = list(dict.fromkeys([
            *(shadow.get("hard_guard_reasons") or []), evaluation["reason"]]))
        shadow["working_action"] = build_working_action(snapshot, shadow)
    else:
        # Eligible paths alone do not constitute a registered trade decision.
        shadow["quant_eligible_manual_candidate"] = True
    return shadow


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
        "disagreement_category": None,
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
    independent_input = _shadow_projection(snapshot)
    input_captured_ts = independent_input.get('captured_ts')
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 1100,
        "messages": [
            {"role": "system", "content": SHADOW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Текущий bounded snapshot для независимого SHADOW-решения:\n"
                    + json.dumps(
                        independent_input,
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
    agreement = (
        parsed["policy"] == quant_policy if quant_policy is not None else None
    )
    disagreement_cat = _disagreement_category(parsed["policy"], quant_policy)
    result = {
        "version": SHADOW_VERSION,
        "status": "ok" if guard_ok else "blocked",
        "captured_ts": input_captured_ts,
        "production_authority": False,
        "automatic_execution_allowed": False,
        "model": provider_payload.get("model") or model,
        "quant_policy": quant_policy,
        "policy": parsed["policy"],
        "confidence": parsed["confidence"],
        "agreement": agreement,
        "disagreement_category": disagreement_cat,
        "blocked_by_hard_guard": not guard_ok,
        "hard_guard_reasons": guard_reasons,
        "reason_ru": parsed["reason_ru"],
        "key_evidence": parsed["key_evidence"],
        "counter_evidence": parsed["counter_evidence"],
        "evidence_families": parsed["evidence_families"],
        "invalidation_conditions": parsed["invalidation_conditions"],
        "selection_masked": True,
    }
    if "policy_scores" in parsed:
        result["policy_scores"] = parsed["policy_scores"]
    for key in ('family_assessments', 'family_assessment_rejections'):
        if key in parsed:
            result[key] = parsed[key]
    from .llm_shadow_working_action import build_working_action
    result["working_action"] = build_working_action(snapshot, result)
    finalize_extended_shadow(snapshot, result)
    audit_report_claims(snapshot, result)
    record_shadow_decision(result)
    return result


_SHADOW_HISTORY_LOCK = threading.Lock()
_LATEST_SHADOW_DECISION: dict[str, Any] | None = None
_SHADOW_HISTORY: list[dict[str, Any]] = []


def record_shadow_decision(decision: dict[str, Any]) -> None:
    global _LATEST_SHADOW_DECISION
    if not isinstance(decision, dict):
        return
    with _SHADOW_HISTORY_LOCK:
        _LATEST_SHADOW_DECISION = dict(decision)
        entry = {
            **decision,
            "recorded_ts": time.time(),
        }
        _SHADOW_HISTORY.append(entry)
        if len(_SHADOW_HISTORY) > 50:
            _SHADOW_HISTORY.pop(0)


def get_latest_shadow_decision() -> dict[str, Any] | None:
    with _SHADOW_HISTORY_LOCK:
        return dict(_LATEST_SHADOW_DECISION) if _LATEST_SHADOW_DECISION else None


def get_shadow_history(limit: int = 20) -> list[dict[str, Any]]:
    with _SHADOW_HISTORY_LOCK:
        return [dict(x) for x in _SHADOW_HISTORY[-limit:]]


def append_shadow_section(report: str, shadow: dict[str, Any]) -> str:
    """Append one explicit research-only section to the human report."""
    approved = shadow.get("production_authority") is True and bool(
        (shadow.get("working_action") or {}).get("action_id"))
    header = ("**РАСШИРЕННЫЙ МЕНЕДЖМЕНТ · РУЧНОЕ ПОДТВЕРЖДЕНИЕ** —"
              if approved else "**LLM SHADOW DECISION · БЕЗ PRODUCTION AUTHORITY** —")
    lines = [report.rstrip(), "", header]
    status = shadow.get("status")
    quant_policy = shadow.get("quant_policy") or "—"
    if status == "unavailable":
        lines.append(
            f"Shadow LLM: UNAVAILABLE ({shadow.get('reason_code') or 'SHADOW_UNAVAILABLE'}). "
            "Текущий LLM не добавляет предпочтения в общий выбор; итоговое действие публикует сервер."
        )
        return "\n".join(lines).strip()

    if shadow.get("source") == "deterministic_active_management":
        action = shadow.get("working_action") or {}
        evaluation = shadow.get("quant_evaluation") or {}
        lines.append(f"Deterministic: {shadow.get('policy')}. "
            f"Expected против HOLD {evaluation.get('expected_delta_vs_hold_r')}R; "
            f"нижний MC CI {evaluation.get('paired_delta_ci95_lower_r')}R. "
            "Это расчётный эффект, статистический перевес на реальных сделках не подтверждён.")
        lines.append(str(action.get("instruction_ru") or "Параметры действия недоступны"))
        return "\n".join(lines).strip()

    policy = shadow.get("policy") or "—"
    confidence = _number(shadow.get("confidence"))
    confidence_text = "—" if confidence is None else f"{confidence * 100:.1f}%"
    agreement = shadow.get("agreement")
    disagreement_cat = shadow.get("disagreement_category")
    agreement_text = (
        "совпадает" if agreement is True
        else ("расходится" if agreement is False else "не сопоставлено")
    )
    if disagreement_cat:
        agreement_text += f" [{disagreement_cat}]"
    guard = (
        "BLOCKED/UNVERIFIED hard-risk guard"
        if shadow.get("blocked_by_hard_guard")
        else "PASS hard-risk guard"
    )
    lines.append(
        f"Quant: {quant_policy}. Независимый LLM: {policy}; confidence {confidence_text}; "
        f"с quant {agreement_text}; {guard}."
    )
    lines.append("Confidence — самооценка модели, не калиброванная вероятность "
                 "успеха сделки и не независимое подтверждение решения.")
    if confidence is not None and confidence < 0.65:
        lines.append("Низкая самооценка LLM: совпадение с quant не добавляет "
                     "авторитета действующему плану.")
    if shadow.get("hard_guard_reasons"):
        lines.append("Hard guard: " + "; ".join(shadow["hard_guard_reasons"]) + ".")
    evaluation = shadow.get("quant_evaluation") or {}
    if evaluation:
        lines.append(
            "Расширенная политика: " + str(evaluation.get("status"))
            + "; причина " + str(evaluation.get("reason"))
            + (f"; ΔExpected {evaluation['expected_delta_vs_hold_r']:+.3f}R; "
               f"нижняя 95% граница {evaluation['paired_delta_ci95_lower_r']:+.3f}R; "
               f"gross CVaR10 worst seed {evaluation['worst_seed_cvar10_gross_r']:+.3f}R."
               if all(key in evaluation for key in (
                   "expected_delta_vs_hold_r", "paired_delta_ci95_lower_r",
                   "worst_seed_cvar10_gross_r")) else ".")
        )
    safe_claims = shadow.get("audited_key_evidence", shadow.get("key_evidence"))
    if shadow.get("reason_ru") and not shadow.get("report_claim_conflicts"):
        lines.append("Непроверенный аргумент LLM: " + str(shadow["reason_ru"]))
    if safe_claims:
        lines.append("Заявленные моделью аргументы: " + " | ".join(safe_claims))
    if shadow.get("report_claim_conflicts"):
        lines.append("Противоречащие снимку утверждения LLM исключены из объяснения: "
                     + "; ".join(shadow["report_claim_conflicts"]) + ". Действует проверенный вывод сервера.")
    if shadow.get("counter_evidence"):
        lines.append("Контраргументы LLM: " + " | ".join(shadow["counter_evidence"]))
    action = shadow.get("working_action") or {}
    if action.get("status") == "READY_FOR_MANUAL_CONFIRMATION":
        lines.append("LLM ACTION VARIANT: " + str(action.get("instruction_ru") or policy) + ".")
        lines.append("Кандидат прошёл отдельную проверку путей, Expected и CVaR; "
                     "сервер сам ордер не создаёт. До подтверждения у брокера "
                     "действует прежний стоп/БУ и лестница.")
    else:
        lines.append(
            "Самостоятельный вариант LLM не готов к действию: "
            + str(action.get("reason") or "PARAMETERS_UNAVAILABLE")
            + ". Допуск предпочтений к общему ранжированию показан отдельно "
            "в едином выборе действия; этот вариант не создаёт ордер."
        )
    return "\n".join(lines).strip()



def audit_report_claims(snapshot: dict, shadow: dict) -> None:
    """Withhold explicit contradictions to frozen authority facts from prose."""
    from .management_contract import decision_reliability
    manager = snapshot.get("policy_manager") or {}
    quality = decision_reliability(snapshot)
    stability = manager.get("stability") or {}
    verified_stress = isinstance(stability, dict) and isinstance(stability.get("checks"), (int, float)) and stability["checks"] > 0
    safe, conflicts = [], []
    claims = [shadow.get("reason_ru") or "", *(shadow.get("key_evidence") or [])]
    for index, claim in enumerate(claims):
        text = str(claim).lower()
        conflict = None
        if quality["level"] == "низкая" and "запрещ" in text and ("надежност" in text or "надёжност" in text):
            conflict = "низкая надёжность требует degraded-manual gate и не является абсолютным запретом"
        if "устойчив" in text and ("стресс" in text or "stress" in text) and not verified_stress:
            conflict = "численная стресс-устойчивость в снимке не опубликована"
        if conflict:
            if conflict not in conflicts:
                conflicts.append(conflict)
        elif index > 0:
            safe.append(claim)
    shadow["audited_key_evidence"] = safe
    shadow["report_claim_conflicts"] = conflicts
