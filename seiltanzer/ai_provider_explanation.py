"""Fast OpenRouter explanation layered on the authoritative policy report.

The deterministic renderer owns every action and number. The provider is asked only
for a short explanatory note and is never asked to regenerate policy arithmetic.
This keeps the synchronous public route inside its existing latency budget while
making provider availability observable to the user.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx


FAST_EXPLANATION_MAX_TOKENS = 420
FAST_EXPLANATION_PROMPT = """
PRODUCTION EXPLANATION_ONLY MODE. This instruction supersedes the older request to
produce a 320–520 word full report with every section. The deterministic report is
rendered by the server and already owns the action and all numbers.

Write 120–180 Russian words of explanation only. Do NOT issue, restate, replace or
invent a trading instruction. Do NOT recommend HOLD/CLOSE/EXIT, position sizing,
stop changes or new orders. Explain only why the already-calculated policy is
reasonable from the supplied evidence: 2–4 strongest supporting/limiting facts,
data quality, and what market/data change should trigger the next recalculation.
You may quote exact input numbers when useful, but never calculate or alter them.
Missing/unavailable is never zero. Research/EDE context without production authority
may be described only as context, never as an independent reason to close or exit.
No markdown table. No preamble about being an AI.
""".strip()

# Reject only clear new imperative/recommendation language. Merely discussing why
# an alternative CLOSE/EXIT was rejected is valid explanation and must not be
# mistaken for a new trading command.
_FORBIDDEN_INSTRUCTION_PHRASES = (
    "нужно закрыть",
    "следует закрыть",
    "рекомендую закрыть",
    "необходимо закрыть",
    "закройте позицию",
    "нужно сократить",
    "следует сократить",
    "рекомендую сократить",
    "необходимо сократить",
    "сократите позицию",
    "увеличьте позицию",
    "добавьте позицию",
    "перенесите стоп",
    "расширьте стоп",
    "откройте позицию",
    "войдите в позицию",
)


def _provider_headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Seiltanzer-Terminal/1.0",
        "HTTP-Referer": "https://seiltanzer-terminal.local",
        "X-Title": "Seiltanzer Terminal",
    }


def _explanation_facts(snapshot: dict[str, Any]) -> dict[str, Any]:
    manager = snapshot.get("policy_manager") or {}
    recommendation = manager.get("recommendation") or {}
    policies = manager.get("policies") or {}
    return {
        "selected_policy": recommendation.get("policy"),
        "automatic_execution_allowed": recommendation.get("automatic_execution_allowed"),
        "policy_metrics": {
            name: {
                key: row.get(key)
                for key in (
                    "expected_final_r", "cvar10_r", "p_next_rung_before_stop",
                    "p_stop_before_next_rung", "eligible", "reason",
                )
                if key in row
            }
            for name, row in policies.items()
            if isinstance(row, dict)
        },
        "gate": manager.get("gate") or {},
        "stability": manager.get("stability") or {},
        "input_audit": manager.get("input_audit") or {},
        "scenario_geometry": manager.get("scenario_geometry") or {},
        "provider_projection": snapshot.get("provider_projection") or {},
    }


def _sanitize_explanation(content: str) -> str:
    text = " ".join(str(content or "").strip().split())
    if not text:
        raise RuntimeError("OpenRouter вернул пустое объяснение")
    lowered = text.casefold()
    forbidden = [phrase for phrase in _FORBIDDEN_INSTRUCTION_PHRASES if phrase in lowered]
    if forbidden:
        raise RuntimeError("provider_explanation_attempted_trading_instruction")
    return text


def request_explanation(
    snapshot: dict[str, Any],
    *,
    authoritative_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return authoritative deterministic report plus real LLM commentary.

    ``snapshot`` is the bounded provider projection. ``authoritative_snapshot``
    stays process-local and is never uploaded; when supplied, it owns deterministic
    rendering and integrity validation.
    """
    from . import ai_verdict

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не настроен на сервере")
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    authority = authoritative_snapshot if isinstance(authoritative_snapshot, dict) else snapshot

    deterministic = ai_verdict.render_policy_report(authority)
    facts = _explanation_facts(snapshot)
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": FAST_EXPLANATION_MAX_TOKENS,
        "messages": [
            {
                "role": "system",
                "content": ai_verdict.SYSTEM_PROMPT + "\n\n" + FAST_EXPLANATION_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "EXPLANATION_ONLY quantitative snapshot:\n"
                    + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                    + "\n\nAuthoritative control facts (read-only):\n"
                    + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        ],
    }

    try:
        with httpx.Client(proxy=proxy, timeout=20, trust_env=False) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body,
                headers=_provider_headers(key),
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
    explanation = _sanitize_explanation(content)
    combined = (
        deterministic.rstrip()
        + "\n\n**LLM EXPLANATION · OPENROUTER** —\n"
        + explanation
    )
    # The model never owns the deterministic portion, but validate the composed
    # response anyway: all action/policy arithmetic must still be present exactly.
    violations = ai_verdict._validate_model_report(combined, authority)
    hard_violations = [
        violation for violation in violations
        if violation == "изменено рассчитанное действие"
        or violation.startswith("нет политики ")
        or violation.startswith("изменено или пропущено ")
    ]
    if hard_violations:
        raise RuntimeError("provider_explanation_hard_integrity_failure")

    return {
        "verdict": combined,
        "model": result.get("model", model),
        "captured_ts": authority.get("captured_ts"),
        "provider_mode": "llm_explanation_over_deterministic_policy",
        "validation_warnings": [
            violation for violation in violations if violation not in hard_violations
        ],
    }


def install_ai_provider_explanation() -> None:
    """Replace the legacy full-report provider call with the fast explanation layer.

    This is installed immediately after ``install_ai_provider_guard``. It reuses
    that guard's projection, timeout, executor and circuit; only the provider work
    performed inside the existing bounded call changes.
    """
    from . import app as app_module
    from .ai_provider_guard import bounded_provider_call, compact_provider_snapshot

    def guarded_explanation(snapshot: dict[str, Any]) -> dict[str, Any]:
        provider_snapshot = compact_provider_snapshot(snapshot)
        return bounded_provider_call(
            lambda projected: request_explanation(
                projected, authoritative_snapshot=snapshot),
            provider_snapshot,
        )

    guarded_explanation.__name__ = "request_verdict"
    guarded_explanation.__doc__ = (
        "Gateway-safe OpenRouter explanation over authoritative deterministic policy."
    )
    app_module.request_verdict = guarded_explanation
