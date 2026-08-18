"""Cheap, on-demand explanation of deterministic historical analog results.

The LLM never chooses analogs and never receives production decision authority.
The exact deterministic analog set is hashed first; one explanation is cached per
observation/analog-set/prompt/model.  This path is explicit POST-only and separate
from the latency-critical AI verdict provider guard/worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable

import httpx

from .g1_historical_analog import historical_analogs


ANALYST_CONTRACT_VERSION = "g1s-historical-analog-analyst-v1"
ANALYST_PROMPT_VERSION = "g1s-analog-explanation-v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_TIMEOUT_SEC = 10.0
MAX_OUTPUT_TOKENS = 480

SYSTEM_PROMPT = """Ты объясняешь уже рассчитанный набор исторических аналогов рынка.
Аналоги выбраны детерминированным causal-алгоритмом; нельзя добавлять, удалять или переоценивать их.
Нельзя давать BUY/SELL, менять Position Manager, стоп, размер, CVaR или любое торговое действие.
Нельзя превращать историческую частоту в гарантированную вероятность будущего.
Кратко объясни: насколько аналоги согласованы между собой, что обычно происходило после них,
какие текущие признаки сильнее всего отличаются от медианы аналогов и почему это ограничивает вывод.
Пиши по-русски, 120–220 слов, без выдуманных чисел. Используй только переданный JSON."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _model() -> str:
    return (
        os.environ.get("ANALOG_LLM_MODEL", "").strip()
        or os.environ.get("DATA_FACTORY_MODEL", "").strip()
        or os.environ.get("OPENROUTER_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def _provider(summary: dict[str, Any], model: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY_NOT_CONFIGURED")
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "DETERMINISTIC ANALOG SUMMARY:\n" + _json(summary)},
        ],
    }
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    timeout_raw = os.environ.get("ANALOG_LLM_TIMEOUT_SEC", "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SEC
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SEC
    timeout = max(3.0, min(20.0, timeout))
    try:
        with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Seiltanzer-Analog-Analyst/1.0",
                    "HTTP-Referer": "https://seiltanzer-terminal.local",
                    "X-Title": "Seiltanzer Historical Analog Analyst",
                },
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"PROVIDER_HTTP_{exc.response.status_code}") from exc
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise RuntimeError(f"PROVIDER_ERROR_{type(exc).__name__}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("PROVIDER_EMPTY_RESPONSE")
    return content.strip()


def _ensure_table(runtime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_analog_explanations(
                explanation_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL,
                analog_set_sha256 TEXT NOT NULL,
                cache_key TEXT NOT NULL UNIQUE,
                prompt_version TEXT NOT NULL,
                model TEXT NOT NULL,
                explanation TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_analog_explanation_observation "
            "ON g1s_analog_explanations(observation_id,created_ts)")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1s_analog_explanations_immutable_update
            BEFORE UPDATE ON g1s_analog_explanations
            BEGIN SELECT RAISE(ABORT,'immutable analog explanation row'); END""")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1s_analog_explanations_immutable_delete
            BEFORE DELETE ON g1s_analog_explanations
            BEGIN SELECT RAISE(ABORT,'immutable analog explanation row'); END""")


def _compact(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": report.get("contract_version"),
        "instrument": report.get("instrument"),
        "horizon_minutes": report.get("horizon_minutes"),
        "analog_n": report.get("analog_n"),
        "median_distance": report.get("median_distance"),
        "median_feature_overlap": report.get("median_feature_overlap"),
        "up_n": report.get("up_n"),
        "down_n": report.get("down_n"),
        "flat_n": report.get("flat_n"),
        "positive_rate_nonflat": report.get("positive_rate_nonflat"),
        "mean_terminal_log_return": report.get("mean_terminal_log_return"),
        "median_terminal_log_return": report.get("median_terminal_log_return"),
        "terminal_log_return_std": report.get("terminal_log_return_std"),
        "median_mfe_log_return": report.get("median_mfe_log_return"),
        "median_mae_log_return": report.get("median_mae_log_return"),
        "top_feature_differences": (report.get("top_feature_differences") or [])[:8],
        "analogs": (report.get("analogs") or [])[:20],
        "causal_rules": report.get("causal_rules"),
        "warning": "historical analog frequencies are descriptive, not guaranteed future probabilities",
    }


def explain_historical_analogs(runtime, observation_id: str, *, k: int = 20,
                               provider: Callable[[dict[str, Any], str], str] | None = None
                               ) -> dict[str, Any]:
    report = historical_analogs(runtime, observation_id, k=k)
    if report.get("status") != "OK":
        return {
            "contract_version": ANALYST_CONTRACT_VERSION,
            "status": "UNAVAILABLE",
            "reason": "ANALOG_REPORT_UNAVAILABLE",
            "analog_report": report,
            "research_only": True,
            "production_authority": False,
        }
    _ensure_table(runtime)
    model = _model()
    analog_hash = str(report["analog_set_sha256"])
    cache_key = _sha(
        f"{observation_id}|{analog_hash}|{ANALYST_PROMPT_VERSION}|{model}"
    )
    with runtime._lock:
        cached = runtime._conn.execute(
            "SELECT * FROM g1s_analog_explanations WHERE cache_key=? LIMIT 1",
            (cache_key,),
        ).fetchone()
    if cached is not None:
        row = dict(cached)
        return {
            "contract_version": ANALYST_CONTRACT_VERSION,
            "status": "OK",
            "observation_id": str(observation_id),
            "analog_set_sha256": analog_hash,
            "prompt_version": ANALYST_PROMPT_VERSION,
            "model": str(row["model"]),
            "explanation": str(row["explanation"]),
            "cache_hit": True,
            "research_only": True,
            "production_authority": False,
            "may_change_position_manager": False,
        }

    compact = _compact(report)
    try:
        explanation = (provider or _provider)(compact, model).strip()
    except (RuntimeError, ValueError) as exc:
        return {
            "contract_version": ANALYST_CONTRACT_VERSION,
            "status": "UNAVAILABLE",
            "reason": str(exc)[:160],
            "observation_id": str(observation_id),
            "analog_set_sha256": analog_hash,
            "cache_hit": False,
            "research_only": True,
            "production_authority": False,
        }
    if not explanation:
        return {
            "contract_version": ANALYST_CONTRACT_VERSION,
            "status": "UNAVAILABLE", "reason": "EMPTY_EXPLANATION",
            "research_only": True, "production_authority": False,
        }
    explanation_id = "g1s-analog-exp-" + _sha(cache_key + explanation)[:26]
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_analog_explanations(explanation_id,observation_id,"
            "analog_set_sha256,cache_key,prompt_version,model,explanation,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (explanation_id, str(observation_id), analog_hash, cache_key,
             ANALYST_PROMPT_VERSION, model, explanation, time.time()),
        )
    return {
        "contract_version": ANALYST_CONTRACT_VERSION,
        "status": "OK",
        "observation_id": str(observation_id),
        "analog_set_sha256": analog_hash,
        "prompt_version": ANALYST_PROMPT_VERSION,
        "model": model,
        "explanation": explanation,
        "cache_hit": False,
        "research_only": True,
        "production_authority": False,
        "may_change_position_manager": False,
        "historical_frequency_is_not_forecast_probability": True,
    }
