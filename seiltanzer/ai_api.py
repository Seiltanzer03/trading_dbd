"""Stable, failure-isolated API orchestration for AI trade reviews."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable


AI_API_VERSION = "ai-verdict-api-f1-v1"
logger = logging.getLogger("seiltanzer.ai_verdict")


def request_id() -> str:
    return "ai-" + uuid.uuid4().hex[:20]


def provider_error(exc: Exception) -> dict:
    """Map provider-facing failures without exposing response bodies/secrets."""
    message = str(exc).lower()
    name = type(exc).__name__.lower()
    if "timeout" in message or "timeout" in name:
        code, retriable = "provider_timeout", True
    elif any(token in message for token in ("401", "403", "auth", "api_key", "api key", "не настроен")):
        code, retriable = "provider_auth", False
    elif "429" in message or "rate limit" in message:
        code, retriable = "provider_rate_limit", True
    elif any(token in message for token in ("json", "empty", "пуст", "bad response", "invalid payload")):
        code, retriable = "provider_bad_response", True
    else:
        code, retriable = "provider_unavailable", True
    return {"code": code, "retriable": retriable}


def error_body(code: str, message: str, req_id: str, *, retriable: bool) -> dict:
    return {
        "ok": False,
        "api_version": AI_API_VERSION,
        "error": {
            "code": code,
            "message": message,
            "request_id": req_id,
            "retriable": bool(retriable),
        },
    }


def success_body(result: dict, req_id: str, *, degraded: bool = False,
                 provider_failure: dict | None = None) -> dict:
    body = {
        "ok": True,
        "api_version": AI_API_VERSION,
        "verdict": str(result["verdict"]),
        "model": str(result.get("model") or "unknown"),
        "mode": "deterministic_fallback" if degraded else "llm",
        "degraded": bool(degraded),
        "request_id": req_id,
    }
    if provider_failure:
        body["provider_error"] = provider_failure
    if result.get("captured_ts") is not None:
        body["captured_ts"] = result["captured_ts"]
    return body


def log_event(*, req_id: str, stage: str, started: float,
              trade_id: int | None = None, review_id: str | None = None,
              provider: str | None = None, mode: str | None = None,
              exc: Exception | None = None) -> None:
    # A compact JSON line is searchable across proxy/app logs and deliberately
    # excludes prompt text, headers, cookies and credentials.
    event: dict[str, Any] = {
        "request_id": req_id,
        "trade_id": trade_id,
        "review_id": review_id,
        "stage": stage,
        "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
        "provider": provider,
        "result_mode": mode,
        "exception_class": type(exc).__name__ if exc else None,
    }
    (logger.exception if exc else logger.info)(
        "ai_verdict_event %s", json.dumps(event, separators=(",", ":")),
        exc_info=exc if exc else None,
    )


def deterministic_result(snapshot: dict, render: Callable[[dict], str]) -> dict:
    return {
        "verdict": render(snapshot),
        "model": "deterministic-policy-fallback",
        "captured_ts": snapshot.get("captured_ts"),
    }
