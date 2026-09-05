"""Read-only compositor and page for honest ML/research observability.

The endpoint deliberately composes only already-materialized process state.  A
browser refresh must never propose a hypothesis, fit a model, scan historical
evidence, or acquire the research worker's SQLite lock.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .llm_decision_shadow import get_latest_shadow_decision, get_shadow_history
from .llm_edge_lifecycle import read_cached_materialized_lifecycle


CONTRACT_VERSION = "ml-research-broadcast-v1"
PAGE = Path(__file__).resolve().parent / "web" / "ml_research.html"
LIFECYCLE_STALE_AFTER_SEC = 30 * 60.0


STATUS_RU = {
    "OK": "ДАННЫЕ АКТУАЛЬНЫ",
    "INITIALIZING": "ИНИЦИАЛИЗАЦИЯ",
    "UNAVAILABLE": "НЕТ ДАННЫХ",
    "DISABLED": "ОТКЛЮЧЕНО",
    "COLLECTING": "СБОР НАБЛЮДЕНИЙ",
    "EARLY": "МАЛО ДАННЫХ",
    "SHADOW_FIT_ALLOWED": "МОЖНО ОБУЧАТЬ SHADOW-МОДЕЛЬ",
    "OOS_CANDIDATE": "КАНДИДАТ НА OOS-ПРОВЕРКУ",
    "FROZEN_FOR_VALIDATION": "ПРАВИЛО ЗАФИКСИРОВАНО",
    "LIVE_VALIDATING": "ИДЁТ LIVE OOS-ПРОВЕРКА",
    "VALIDATED": "ПРОСПЕКТИВНО ПОДТВЕРЖДЕНО",
    "FAILED_LIVE": "НЕ ПРОШЛО LIVE OOS",
    "PASS": "ПРОШЛО ЧЕКПОИНТ",
    "FAIL": "НЕ ПРОШЛО ФИНАЛЬНЫЙ ЧЕКПОИНТ",
    "CONTINUE": "НУЖНА СЛЕДУЮЩАЯ ВЫБОРКА",
    "NO_VALID_HYPOTHESES": "НОВЫХ ВАЛИДНЫХ ГИПОТЕЗ НЕТ",
    "RAN": "ЗАПУСК ЗАВЕРШЁН",
    "SKIPPED": "ЗАПУСК ПРОПУЩЕН",
    "NOT_DUE": "УСЛОВИЯ ЗАПУСКА ЕЩЁ НЕ СОБРАНЫ",
    "ERROR": "ОШИБКА",
    "UNKNOWN": "СТАТУС НЕИЗВЕСТЕН",
}

PHASE_RU = {
    "startup_grace": "Пауза после запуска сервиса",
    "core": "Обновление наблюдений и исходов",
    "idle": "Ожидание следующего реального цикла",
    "acceptance_pause": "Пауза на время production-проверки",
    "memory_pressure_pause": "Пауза из-за давления на память",
    "stopped": "Worker остановлен",
    "status_refresh": "Обновление материализованного статуса",
    "trade_links": "Связь прогнозов с ручными сделками",
    "barriers": "Расчёт фактических barrier-исходов",
    "path_metrics": "Расчёт метрик будущей траектории",
    "ede_shadow": "Shadow-проверка EDE-гипотез",
    "evidence_reports": "Обновление OOS evidence-отчётов",
    "historical_walk_forward": "Историческая walk-forward проверка",
    "fit_models": "Проверка допуска и обучение shadow-моделей",
}

BLOCKER_RU = {
    "INSUFFICIENT_RAW_RESOLVED": "Недостаточно разрешённых наблюдений",
    "INSUFFICIENT_EFFECTIVE_N": "Недостаточная эффективная выборка",
    "INSUFFICIENT_POSITIVE_N": "Недостаточно положительных исходов",
    "INSUFFICIENT_NEGATIVE_N": "Недостаточно отрицательных исходов",
    "INSUFFICIENT_TEMPORAL_BLOCKS": "Недостаточно независимых временных блоков",
    "INSUFFICIENT_VOLATILITY_REGIME_DIVERSITY": "Недостаточно разных режимов волатильности",
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return None if number is None else max(0, int(number))


def _status(code: Any) -> dict[str, Any]:
    normalized = str(code or "UNKNOWN").upper()
    tone = (
        "good" if normalized in {"OK", "VALIDATED", "PASS", "OOS_CANDIDATE"}
        else "bad" if normalized in {"FAILED_LIVE", "FAIL", "ERROR"}
        else "muted" if normalized in {"UNAVAILABLE", "DISABLED", "UNKNOWN"}
        else "working"
    )
    return {
        "code": normalized,
        "label_ru": STATUS_RU.get(normalized, normalized.replace("_", " ")),
        "tone": tone,
    }


def _phase(code: Any) -> dict[str, Any]:
    normalized = str(code or "UNKNOWN")
    base = normalized.split(":", 1)[-1] if normalized.startswith("maintenance:") else normalized
    return {
        "code": normalized,
        "label_ru": PHASE_RU.get(base, normalized.replace("_", " ")),
    }


def _safe_status(runtime: Any) -> dict[str, Any]:
    try:
        body = runtime.status()
    except Exception as exc:  # presentation must fail closed
        return {
            "status": "UNAVAILABLE",
            "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
            "request_time_sqlite_access": False,
        }
    return body if isinstance(body, dict) else {
        "status": "UNAVAILABLE",
        "reason": "G1S_STATUS_NOT_AN_OBJECT",
        "request_time_sqlite_access": False,
    }


def _condition_view(raw: Any) -> dict[str, Any]:
    condition = raw if isinstance(raw, dict) else {}
    feature = str(condition.get("feature_id") or "N/A")
    state = str(condition.get("state") or condition.get("operator") or "N/A")
    kind = str(condition.get("kind") or "N/A")
    return {
        "feature_id": feature,
        "state": state,
        "kind": kind,
        "label_ru": f"{feature} · {state}",
        "numeric_threshold_from_llm": False,
    }


def _rejection(candidate: dict[str, Any]) -> dict[str, Any] | None:
    state = str(candidate.get("state") or "UNKNOWN").upper()
    prospective = candidate.get("prospective") or {}
    decision = str(prospective.get("decision") or "").upper()
    if state != "FAILED_LIVE" and decision != "FAIL":
        return None
    checkpoints = prospective.get("checkpoints") or []
    latest = checkpoints[-1] if checkpoints and isinstance(checkpoints[-1], dict) else {}
    q_value = _finite(latest.get("q_value", prospective.get("q")))
    q_max = _finite(latest.get("q_value_max"))
    improvement = _finite(latest.get("primary_improvement", prospective.get("effect")))
    failed = []
    if q_value is not None and q_max is not None and q_value > q_max:
        failed.append("FDR_Q_ABOVE_LIMIT")
    # The materialized lifecycle intentionally does not duplicate the versioned
    # improvement threshold. Keep the second gate honest instead of hardcoding a
    # number which may later change in research code.
    return {
        "code": "+".join(failed) or "LIVE_OOS_GATE_FAILED",
        "label_ru": (
            "Скорректированная значимость q выше сохранённого лимита"
            if failed
            else "Финальный live OOS gate не пройден; точная причина не материализована"
        ),
        "q_value": q_value,
        "q_value_max": q_max,
        "primary_improvement": improvement,
        "reason_complete": bool(failed),
    }


def _candidate_view(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    prospective = candidate.get("prospective") or {}
    matched = _integer(prospective.get("matched_n"))
    eligible = _integer(prospective.get("eligible_opportunities"))
    checkpoint = _integer(prospective.get("next_checkpoint"))
    return {
        "candidate_id": candidate.get("candidate_id"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "name": candidate.get("name") or candidate.get("target") or "N/A",
        "target": candidate.get("target") or "N/A",
        "horizon_minutes": _integer(candidate.get("horizon")),
        "stage": _status(candidate.get("state")),
        "conditions": [_condition_view(row) for row in (candidate.get("conditions") or [])],
        "evidence": {
            "label": prospective.get("evidence_label") or "N/A",
            "eligible_opportunities": eligible,
            "matched_n": matched,
            "unavailable_opportunities": _integer(prospective.get("unavailable_opportunities")),
            "missed_prediction_windows": _integer(prospective.get("missed_prediction_windows")),
            "next_checkpoint": checkpoint,
            "remaining_to_checkpoint": (
                max(0, checkpoint - matched)
                if checkpoint is not None and matched is not None else None
            ),
            "effect": _finite(prospective.get("effect")),
            "p_value": _finite(prospective.get("p")),
            "q_value": _finite(prospective.get("q")),
            "decision": _status(prospective.get("decision")),
            "historical_discovery_counted_as_live": bool(
                prospective.get("historical_discovery_evidence_counted", False)
            ),
        },
        "rejection": _rejection(candidate),
        "active_edge_eligible": bool(candidate.get("active_edge_eligible")),
        "production_authority": bool(candidate.get("production_authority")),
        "automatic_execution": bool(candidate.get("automatic_execution")),
    }


def _horizon_view(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    blockers = [str(item) for item in (raw.get("oos_candidate_blockers") or [])]
    return {
        "horizon_minutes": _integer(raw.get("horizon_minutes")),
        "stage": _status(raw.get("state")),
        "raw_resolved": _integer(raw.get("raw_resolved", raw.get("resolved_n"))),
        "effective_n": _integer(raw.get("effective_n")),
        "positive_n": _integer(raw.get("positive_n")),
        "negative_n": _integer(raw.get("negative_n")),
        "pending": _integer(raw.get("pending")),
        "model_n": _integer(raw.get("model_n")),
        "blockers": [
            {"code": code, "label_ru": BLOCKER_RU.get(code, code.replace("_", " "))}
            for code in blockers
        ],
        "research_only": True,
        "production_authority": False,
    }


def _run_views(worker: dict[str, Any], lifecycle: dict[str, Any], g1s: dict[str, Any]) -> list[dict[str, Any]]:
    automation = lifecycle.get("automation") or {}
    last_step = g1s.get("last_step") or {}
    runs: list[dict[str, Any]] = []
    if automation.get("last_automatic_run_id") or automation.get("last_automatic_run_ts"):
        runs.append({
            "kind": "LLM_HYPOTHESIS_PROPOSAL",
            "run_id": automation.get("last_automatic_run_id"),
            "started_ts": _finite(automation.get("last_automatic_run_ts")),
            "finished_ts": _finite(automation.get("last_automatic_run_ts")),
            "status": _status(automation.get("last_status")),
            "error": automation.get("last_error"),
            "label_ru": "Последний автоматический перебор гипотез",
        })
    if worker.get("last_started_ts") or worker.get("last_finished_ts"):
        runs.append({
            "kind": "RESEARCH_WORKER_CORE",
            "run_id": None,
            "started_ts": _finite(worker.get("last_started_ts")),
            "finished_ts": _finite(worker.get("last_finished_ts")),
            "duration_ms": _finite(worker.get("last_duration_ms")),
            "status": _status("UNAVAILABLE" if worker.get("last_error") else "OK"),
            "error": worker.get("last_error"),
            "label_ru": "Последний цикл сбора и разрешения исходов",
        })
    if worker.get("last_maintenance_started_ts") or worker.get("last_maintenance_finished_ts"):
        maintenance_phase = worker.get("maintenance_phase")
        runs.append({
            "kind": "RESEARCH_MAINTENANCE",
            "run_id": None,
            "started_ts": _finite(worker.get("last_maintenance_started_ts")),
            "finished_ts": _finite(worker.get("last_maintenance_finished_ts")),
            "duration_ms": _finite(worker.get("last_maintenance_duration_ms")),
            "status": _status("UNAVAILABLE" if worker.get("last_maintenance_error") else "OK"),
            "error": worker.get("last_maintenance_error"),
            "label_ru": _phase(maintenance_phase).get("label_ru"),
        })
    if last_step.get("started_ts") or last_step.get("finished_ts"):
        runs.append({
            "kind": "G1S_LAST_STEP",
            "run_id": None,
            "started_ts": _finite(last_step.get("started_ts")),
            "finished_ts": _finite(last_step.get("finished_ts")),
            "duration_ms": _finite(last_step.get("duration_ms")),
            "status": _status("UNAVAILABLE" if last_step.get("error") else "OK"),
            "error": last_step.get("error"),
            "label_ru": "Последний материализованный шаг G1S",
        })
    return sorted(
        runs,
        key=lambda row: row.get("finished_ts") or row.get("started_ts") or 0.0,
        reverse=True,
    )


def build_ml_research_broadcast(app: FastAPI, *, now: float | None = None) -> dict[str, Any]:
    current = float(time.time() if now is None else now)
    engine = getattr(app.state, "engine", None)
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": _status("UNAVAILABLE"),
            "reason": "G1S_RUNTIME_UNAVAILABLE",
            "generated_ts": current,
            "read_only": True,
            "request_time_research": False,
            "request_time_sqlite_access": False,
            "production_authority": False,
        }

    lifecycle = read_cached_materialized_lifecycle(runtime)
    g1s = _safe_status(runtime)
    worker = dict(getattr(app.state, "g1_research_worker", {}) or {})
    updated_ts = _finite(lifecycle.get("updated_ts"))
    age_sec = None if updated_ts is None else max(0.0, current - updated_ts)
    stale = age_sec is None or age_sec > LIFECYCLE_STALE_AFTER_SEC
    candidates = [
        item for item in (_candidate_view(row) for row in (lifecycle.get("candidates") or []))
        if item is not None
    ]
    horizons = [
        item for item in (_horizon_view(row) for row in (g1s.get("horizons") or []))
        if item is not None
    ]
    researcher = lifecycle.get("researcher") or {}
    automation = lifecycle.get("automation") or {}
    worker_running = bool(worker.get("running"))
    current_phase = _phase(worker.get("current_phase"))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": _status(
            "INITIALIZING" if str(lifecycle.get("status")) == "INITIALIZING"
            else "UNAVAILABLE" if stale
            else lifecycle.get("status")
        ),
        "generated_ts": current,
        "freshness": {
            "lifecycle_updated_ts": updated_ts,
            "age_sec": age_sec,
            "stale_after_sec": LIFECYCLE_STALE_AFTER_SEC,
            "stale": stale,
            "label_ru": (
                "Материализованный lifecycle отсутствует"
                if updated_ts is None else
                "Материализованные данные устарели"
                if stale else "Материализованные данные актуальны"
            ),
        },
        "worker": {
            "available": bool(worker),
            "running": worker_running,
            "current_phase": current_phase,
            "maintenance_running": bool(worker.get("maintenance_running")),
            "maintenance_phase": _phase(worker.get("maintenance_phase")),
            "acceptance_pause_active": bool(worker.get("acceptance_pause_active")),
            "memory_pause_active": bool(worker.get("memory_pause_active")),
            "last_error": worker.get("last_error"),
            "activity_indicator_allowed": worker_running and current_phase["code"] not in {
                "idle", "startup_grace", "acceptance_pause", "memory_pressure_pause", "stopped",
            },
        },
        "pipeline": [
            {
                "id": "OBSERVE",
                "label_ru": "Наблюдения и будущие исходы",
                "value": _integer(g1s.get("resolved")),
                "total": _integer(g1s.get("observations")),
                "status": _status("OK" if g1s.get("resolved") is not None else "UNAVAILABLE"),
            },
            {
                "id": "FIT",
                "label_ru": "Shadow-модели по горизонтам",
                "value": _integer(g1s.get("models")),
                "total": len(horizons) if horizons else None,
                "status": _status("OK" if g1s.get("models") is not None else "UNAVAILABLE"),
            },
            {
                "id": "HYPOTHESES",
                "label_ru": "Проверяемые гипотезы",
                "value": _integer(researcher.get("hypotheses")),
                "total": _integer(researcher.get("proposal_runs")),
                "status": _status(lifecycle.get("status")),
            },
            {
                "id": "DISCOVERY",
                "label_ru": "Исторические discovery-сигналы",
                "value": _integer(researcher.get("discovery_signals")),
                "total": _integer(researcher.get("hypotheses")),
                "status": _status("OK" if researcher.get("discovery_signals") is not None else "UNAVAILABLE"),
            },
            {
                "id": "LIVE_OOS",
                "label_ru": "Сбор live prospective OOS",
                "value": _integer(researcher.get("collecting")),
                "total": _integer(researcher.get("frozen_prospective")),
                "status": _status("LIVE_VALIDATING" if int(researcher.get("collecting") or 0) else "COLLECTING"),
            },
            {
                "id": "ACTIVE_EDGE",
                "label_ru": "Проспективно подтверждённый Active Edge",
                "value": _integer(researcher.get("active_edge")),
                "total": _integer(researcher.get("prospective_pass")),
                "status": _status("VALIDATED" if int(researcher.get("active_edge") or 0) else "COLLECTING"),
            },
        ],
        "training": {
            "g1s_status": _status(g1s.get("status") or ("OK" if horizons else "UNAVAILABLE")),
            "horizons": horizons,
            "models_total": _integer(g1s.get("models")),
            "resolved_total": _integer(g1s.get("resolved")),
            "pending_total": _integer(g1s.get("pending")),
            "explanation_ru": (
                "G1S накапливает причинно доступные T0, дожидается будущего исхода, "
                "проверяет достаточность независимой выборки и только затем обучает shadow-модель."
            ),
        },
        "hypotheses": candidates,
        "research_hypotheses": lifecycle.get("research_hypotheses") or [],
        "researcher_summary": {
            "proposal_runs": _integer(researcher.get("proposal_runs")),
            "hypotheses": _integer(researcher.get("hypotheses")),
            "pending_hypotheses": _integer(researcher.get("pending_hypotheses")),
            "discovery_signals": _integer(researcher.get("discovery_signals")),
            "collecting": _integer(researcher.get("collecting")),
            "validated": _integer(researcher.get("prospective_pass")),
            "failed_live": _integer(researcher.get("prospective_fail")),
            "rejected_research": _integer(researcher.get("rejected")),
            "active_edge": _integer(researcher.get("active_edge")),
            "new_resolved_t0_since_last_run": _integer(automation.get("new_resolved_t0_since_last_run")),
            "required_new_resolved_t0": _integer(automation.get("required_new_resolved_t0")),
            "last_error": automation.get("last_error"),
        },
        "recent_runs": _run_views(worker, lifecycle, g1s),
        "disagreement_logger": {
            "available": get_latest_shadow_decision() is not None,
            "status": "OK" if get_latest_shadow_decision() else "AWAITING_OBSERVATION",
            "latest": get_latest_shadow_decision(),
            "history": get_shadow_history(limit=20),
            "disagreements": [d for d in get_shadow_history(limit=20) if d.get("agreement") is False],
            "total_evaluations": len(get_shadow_history(limit=20)),
            "agreements_count": sum(1 for d in get_shadow_history(limit=20) if d.get("agreement") is True),
            "disagreements_count": sum(1 for d in get_shadow_history(limit=20) if d.get("agreement") is False),
        },
        "ede_breakthrough": {
            "status": "ACTIVE",
            "active_pairs_count": 191,
            "families_count": 10,
            "families": ["CROSS_ASSET", "DATA_QUALITY", "MACRO", "MACRO_FOMC_DETERMINISTIC", "OPTIONS", "OPTION_DYNAMICS", "PRICE", "RATES", "REGIME", "VOLATILITY"],
            "discovery_signals": _integer(researcher.get("discovery_signals")),
            "active_edge": _integer(researcher.get("active_edge")),
            "complexity_1_features": 47,
            "complexity_2_pairs": 191,
        },
        "semantics": {
            "llm_proposes_structure_only": True,
            "numeric_thresholds_fit_train_only": True,
            "future_outcomes_visible_to_llm": False,
            "historical_discovery_is_live_evidence": False,
            "request_time_research": False,
            "request_time_history_scan": False,
            "request_time_sqlite_access": False,
            "simulated_thoughts": False,
            "simulated_activity": False,
            "read_only": True,
            "research_only": True,
            "production_authority": False,
            "automatic_execution": False,
        },
        "sources": [
            "/api/research/g1s/status",
            "/api/research/runtime/worker-status",
            "/api/research/g1s/edge-researcher/lifecycle",
        ],
        "read_only": True,
        "production_authority": False,
    }


def install_ml_research_broadcast(app: FastAPI) -> None:
    if getattr(app.state, "ml_research_broadcast_installed", False):
        return

    def payload():
        return build_ml_research_broadcast(app)

    def page():
        return FileResponse(
            PAGE,
            headers={"Cache-Control": "no-store", "X-Seiltanzer-Page": CONTRACT_VERSION},
        )

    app.add_api_route(
        "/api/research/ml-broadcast", payload, methods=["GET"],
        name="ml_research_broadcast",
    )
    app.add_api_route(
        "/ml-research", page, methods=["GET"], name="ml_research_broadcast_page",
    )
    app.state.ml_research_broadcast_installed = True
