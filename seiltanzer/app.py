"""FastAPI-приложение: API, WebSocket-пуш тиков, раздача фронтенда."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import threading
import time
import base64
import secrets

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import INSTRUMENTS, SETUPS, Settings, settings_from_env
from .decision_research import canonical_snapshot
from .position_state import EXTENDED_POLICIES, StaleDecisionError
from .engine import Engine
from .ai_verdict import build_snapshot, render_policy_report, request_verdict
from .ai_api import (
    deterministic_result,
    error_body as ai_error_body,
    log_event as log_ai_event,
    provider_error as normalize_provider_error,
    request_id as new_ai_request_id,
    success_body as ai_success_body,
)
from .g1_management_edge_frequency import current_edge_management_payload

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


def _validate_reference_source(market) -> None:
    """Do not save a broker basis that the engine will immediately ignore."""
    if getattr(market, "demo", False):
        return
    price = market.price
    source = str(price.get("source") or "")
    if price.get("fallback"):
        raise ValueError("резервная цена Bybit не является котировкой брокера; basis сейчас недоступен")
    if price.get("status") != "live" or price.get("fresh") is False:
        raise ValueError("нет свежей котировки брокера; basis сейчас недоступен")
    if market.instrument.tradingview_symbol and not source.startswith("TradingView "):
        raise ValueError("нет прямой котировки брокера; basis по ориентиру сейчас недоступен")
    if market.instrument.swissquote_pair and not source.startswith("Swissquote OTC"):
        raise ValueError("нет прямой spot-котировки; basis по ориентиру сейчас недоступен")

# The live tick remains frequent, but the complete /api/state payload is a
# cached journal/ridge/setup snapshot. A short production refresh interval and
# a post-build idle gap prevent SQLite/JSON work from retrying in a tight loop
# while the research worker is active.
LIVE_STATE_REFRESH_PERIOD_PRODUCTION_SEC = 10.0
LIVE_STATE_REFRESH_PERIOD_FAST_SEC = 2.0


def _refresh_management_decision(engine, snapshot: dict, trade: dict) -> dict:
    """Refresh executable geometry without discarding the frozen arbiter result."""
    manager = snapshot.get("policy_manager") or {}
    existing = manager.get("management_decision") or {}
    if not isinstance(existing, dict):
        existing = {}

    # ``preview_decision`` historically reads recommendation.policy.  Once the
    # arbiter has resolved a single effective policy, that frozen decision must
    # own the operational preview as well.
    preview_snapshot = snapshot
    breached_stop = bool((snapshot.get("trade_geometry") or {}).get(
        "active_risk_barrier_breached"))
    price_row = (((manager.get("input_audit") or {}).get("rows") or {})
                 .get("instrument_price") or {})
    indicative_price = (str(price_row.get("source") or "").startswith("Bybit ")
                        or price_row.get("production_authority") is False)
    effective_policy = "HOLD" if breached_stop or indicative_price else existing.get("policy")
    if effective_policy:
        preview_manager = dict(manager)
        preview_recommendation = dict(preview_manager.get("recommendation") or {})
        preview_recommendation["policy"] = effective_policy
        preview_manager["recommendation"] = preview_recommendation
        preview_snapshot = {**snapshot, "policy_manager": preview_manager}

    operational = engine.position.preview_decision(preview_snapshot, trade)
    decision = {**existing, **operational}
    from .management_economics import repeat_intervention_gate
    repeat_gate = repeat_intervention_gate(snapshot, decision)
    if repeat_gate is not None:
        manager["repeat_intervention_gate"] = repeat_gate
        if not repeat_gate["allowed"]:
            hold_manager = {**manager, "recommendation": {**(manager.get("recommendation") or {}), "policy": "HOLD"}}
            operational = engine.position.preview_decision({**snapshot, "policy_manager": hold_manager}, trade)
            decision = {**existing, **operational, "authority": "STRATEGY",
                        "arbiter_winner": "STRATEGY",
                        "arbiter_reason": "повторное сокращение требует существенного нового основания",
                        "model_policy": repeat_gate["candidate_policy"],
                        "reason": "REPEAT_REDUCTION_REQUIRES_MATERIAL_CHANGE",
                        "continuity": "previous_reduction_accounted_no_new_material_change"}
            manager["recommendation"] = hold_manager["recommendation"]
            manager["management_arbiter"] = {
                **(manager.get("management_arbiter") or {}),
                "winner": "STRATEGY", "effective_policy": "HOLD",
                "reason": "после исполненного сокращения нет существенного нового основания для повторного вмешательства",
            }
    if breached_stop:
        decision.update({
            "authority": "STRATEGY", "policy": "HOLD",
            "execution_status": "not_required", "manual_execution_required": False,
            "automatic_execution_allowed": False,
            "risk_barrier_execution_unverified": True,
            "reason": "BROKER_STOP_EXECUTION_UNVERIFIED",
        })
    elif indicative_price:
        decision.update({
            "authority": "STRATEGY", "policy": "HOLD",
            "execution_status": "not_required", "manual_execution_required": False,
            "automatic_execution_allowed": False,
            "indicative_fallback_price": True,
            "reason": "BYBIT_REFERENCE_PRICE_NOT_BROKER_QUOTE",
        })
    manager["management_decision"] = decision
    snapshot["policy_manager"] = manager
    from .management_economics import attach_position_economics
    attach_position_economics(snapshot)
    return decision


def _extended_manual_decision(base: dict, action: dict) -> dict:
    """Offer one quantified manual action while the strategy remains active."""
    if base.get("policy") != "HOLD" or base.get("manual_execution_required"):
        return base
    if (action.get("execution_status") != "pending_execution"
            or action.get("production_authority") is not True):
        return base
    return {
        **action,
        "decision_id": action["action_id"],
        "authority": "AI_RISK_OVERLAY_EXTENDED",
        "fraction_semantics": "fraction_of_current_remaining_position",
        "incremental_close_fraction": 0.0,
        "remaining_fraction_before_action": base["remaining_fraction_before_action"],
        "remaining_fraction_after_action": base["remaining_fraction_before_action"],
        "quant_baseline_policy": "HOLD",
        "manual_execution_required": True,
        "automatic_execution_allowed": False,
    }


def _unified_operational_choice(engine, snapshot: dict, trade: dict,
                                audit: dict) -> tuple[dict, dict | None]:
    """Translate one ensemble winner through the existing execution guards."""
    manager = snapshot["policy_manager"]
    previous = dict(manager.get("management_decision") or {})
    policy = audit.get("selected_policy")
    candidate = next((row for row in audit.get("candidates") or []
                      if row.get("candidate_id") == audit.get("selected_candidate_id")), None)
    protected = (previous.get("strategy_terminal_event")
                 or previous.get("risk_barrier_execution_unverified")
                 or previous.get("indicative_fallback_price"))
    if protected or not candidate or candidate.get("eligible") is not True:
        audit["operational_guard_reason"] = (
            "MANDATORY_STRATEGY_OR_PRICE_GUARD" if protected else "ENSEMBLE_WINNER_NOT_ELIGIBLE")
        return previous, None
    if policy not in {"HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"} | EXTENDED_POLICIES:
        raise ValueError("unsupported unified ensemble policy")
    # Extended registration needs a non-executing baseline. The chosen action
    # is then the only manual command, even when the legacy base chose a close.
    operational_policy = "HOLD" if policy in EXTENDED_POLICIES else policy
    manager["management_decision"] = {**previous, "policy": operational_policy,
        "authority": "UNIFIED_EDGE_ENSEMBLE", "arbiter_winner": "UNIFIED_EDGE_ENSEMBLE",
        "arbiter_reason": "единое ранжирование допустимых действий",
        "automatic_execution_allowed": False}
    manager["recommendation"] = {**(manager.get("recommendation") or {}),
                                  "policy": operational_policy}
    manager["management_arbiter"] = {**(manager.get("management_arbiter") or {}),
        "winner": "UNIFIED_EDGE_ENSEMBLE", "effective_policy": policy,
        "selection_mechanism": "unified_guarded_weighted_ranking",
        "scores_determine_winner": True, "single_authority": True,
        "reason": "единое ранжирование всех допустимых обычных и расширенных действий"}
    decision = _refresh_management_decision(engine, snapshot, trade)
    if policy not in EXTENDED_POLICIES:
        if decision.get("policy") != policy:
            audit["operational_guard_reason"] = decision.get("reason") or "OPERATIONAL_GUARD_CHANGED_POLICY"
        return decision, None
    if (decision.get("risk_barrier_execution_unverified")
            or decision.get("indicative_fallback_price")):
        audit["operational_guard_reason"] = decision.get("reason")
        return decision, None
    source_row = next((row for row in snapshot.get("active_management_candidates") or []
                       if row.get("policy") == policy and row.get("parameters") == candidate.get("parameters")), None)
    if not source_row or source_row.get("status") != "eligible":
        raise ValueError("unified extended winner lacks quantified frozen action")
    assessment = dict(candidate.get("quant_evaluation") or source_row)
    if assessment.get("status") != "eligible" or assessment.get("production_authority") is not True:
        raise ValueError("unified extended winner did not pass execution risk gate")
    proposal = {"policy": policy, "status": "ok", "source": "unified_edge_ensemble",
        "confidence": .65, "confidence_semantics": "manual_registration_contract_not_probability",
        "automatic_execution_allowed": False, "production_authority": False,
        "quant_evaluation": assessment, "working_action": {
            "contract_version": "llm-shadow-manual-action-v1", "status": "READY_FOR_MANUAL_CONFIRMATION",
            "policy": policy, "confidence": .65,
            "instruction_ru": source_row.get("instruction_ru"),
            "parameters": dict(source_row.get("parameters") or {}),
            "manual_confirmation_required": True, "automatic_execution_allowed": False,
            "may_widen_stop": False, "may_increase_position": False}}
    return decision, proposal


def _publish_unified_review(engine, snapshot: dict, result: dict, review_id: str,
                            trade: dict, audit: dict) -> dict:
    """Publish the exact ranked decision and its frozen audit, or restore pending work."""
    if audit.get("common_economics_invalid") is True:
        raise ValueError("invalid common economics cannot publish a management decision")
    with engine.journal._lock, engine.position.decision_publication(int(trade["id"]), review_id):
        active = engine.journal.active_trade()
        if not active or int(active["id"]) != int(trade["id"]) or active.get("status") != "open":
            raise StaleDecisionError("active trade changed before unified publication")
        frozen = (snapshot.get("policy_manager") or {}).get("management_decision") or {}
        current_version = engine.position._geometry_version(active, engine.position.state(active))
        if frozen.get("geometry_version") != current_version:
            raise StaleDecisionError("position geometry changed before unified publication")
        decision, proposal = _unified_operational_choice(engine, snapshot, active, audit)
        engine.position.register_decision(snapshot, review_id, active)
        if proposal:
            registered = engine.position.register_shadow_action(snapshot, review_id, active, proposal)
            if registered is None:
                audit["operational_guard_reason"] = "EXTENDED_ACTION_NOT_REGISTERED"
            else:
                decision = _extended_manual_decision(decision, registered)
                proposal = {**proposal, "production_authority": True, "working_action": registered}
                result["selected_management_action"] = proposal
                snapshot["selected_management_action"] = proposal
        # Keep the model's independent opinion distinct from the selected
        # deterministic action. Confidence never becomes its ensemble weight.
        if isinstance(result.get("llm_shadow_decision"), dict):
            snapshot["llm_shadow_decision"] = result["llm_shadow_decision"]
        snapshot["policy_manager"]["management_decision"] = decision
        snapshot["effective_management_decision"] = decision
        audit["ranking_selected_policy"] = audit.get("selected_policy")
        audit["ranking_selected_candidate_id"] = audit.get("selected_candidate_id")
        audit["ranking_available"] = audit.get("available") is True
        audit["selected_policy"] = decision["policy"]
        operational_row = next((row for row in audit.get("candidates") or []
            if row.get("policy") == decision["policy"]
            and (not decision.get("parameters") or row.get("parameters") == decision["parameters"])), None)
        audit["selected_candidate_id"] = (operational_row or {}).get("candidate_id") or decision["policy"]
        audit["applied"] = audit["selected_candidate_id"] == audit["ranking_selected_candidate_id"]
        audit["operational_decision_id"] = decision["decision_id"]
        audit["automatic_execution_allowed"] = False
        snapshot["policy_manager"]["unified_edge_ensemble"] = audit
        result["management_decision"] = decision
        result["unified_edge_ensemble"] = audit
        engine.position.supersede_other_pending_actions(int(active["id"]), decision["decision_id"])
        from .ai_verdict_v19 import normalize_final_report
        result["verdict"] = normalize_final_report(result["verdict"], snapshot)
        if proposal and proposal.get("production_authority"):
            result["verdict"] = (
                "**РАСШИРЕННОЕ РЕШЕНИЕ · РУЧНОЕ ПОДТВЕРЖДЕНИЕ** — "
                + str(decision.get("instruction_ru") or decision["policy"])
                + ". Выполните действие у брокера и подтвердите в терминале. "
                  "До подтверждения действует текущий стоп/БУ и лестница.\n\n"
                + result["verdict"])
        from .unified_edge_audit import render_unified_ensemble_lines
        if "**ЕДИНЫЙ ВЫБОР ДЕЙСТВИЯ**" not in result["verdict"]:
            result["verdict"] += "\n\n" + "\n".join(render_unified_ensemble_lines(audit))
        from .active_management import render_active_management
        rows = snapshot.get("active_management_candidates") or []
        result["active_management_candidates"] = rows
        if rows:
            remaining = (snapshot.get("position_state") or {}).get("remaining_position_fraction")
            result["verdict"] += "\n\n" + render_active_management(rows, remaining)
        from .management_contract import calculation_audit
        result["management_calculation_audit"] = calculation_audit(snapshot)
        engine.journal.record_ai_verdict(int(active["id"]), snapshot, result["verdict"], result.get("model"))
    return decision


def _attach_family_source_bundle(engine, snapshot: dict) -> None:
    """Merge bounded local source facts without replacing existing T0 records."""
    from .edge_family_source_runtime import load_family_source_context
    from .runtime_git_identity import runtime_git_sha
    loaded = load_family_source_context(engine, snapshot, expected_sha=runtime_git_sha())
    audit = loaded["edge_family_source_bundle_audit"]
    snapshot["edge_family_source_bundle_audit"] = audit
    incoming = loaded.get("edge_family_sources") or {}
    if not incoming:
        return
    existing = snapshot.get("edge_family_sources")
    if existing is None:
        existing = {}
        snapshot["edge_family_sources"] = existing
    if not isinstance(existing, dict):
        audit["existing_sources_preserved_unmergeable"] = True
        return
    skipped = []
    for family, records in incoming.items():
        prior = existing.get(family)
        if prior is None:
            merged = []
        elif isinstance(prior, dict):
            merged = [prior]
        elif isinstance(prior, list):
            merged = list(prior)
        else:
            skipped.append(family)
            continue
        seen = {str(record["source_id"]) for record in merged
                if isinstance(record, dict) and record.get("source_id")}
        for record in records:
            source_id = str(record.get("source_id") or "")
            if source_id and source_id in seen:
                continue
            merged.append(record)
            if source_id:
                seen.add(source_id)
        existing[family] = merged
    if skipped:
        audit["existing_families_preserved_unmergeable"] = skipped[:8]


def _refresh_intraday_archive(engine) -> None:
    """Persist only bars obtained by the existing configured feed refresh."""
    engine.market.refresh_intraday()
    recorder = getattr(getattr(engine, "passive", None), "record_configured_intraday_archive", None)
    if callable(recorder):
        recorder(engine.market)


def _acknowledged_execution(trade: dict, tick: dict,
                            broker_price: float | None) -> tuple[float | None, float | None]:
    if broker_price is None:
        return (
            ((tick.get("feeds") or {}).get("price") or {}).get("value"),
            (tick.get("prob") or {}).get("r"),
        )
    if not math.isfinite(broker_price) or broker_price <= 0:
        raise ValueError("broker execution price must be positive and finite")
    risk = abs(float(trade["entry"]) - float(trade["stop"]))
    direction = 1 if str(trade.get("direction") or "").lower() in {"long", "buy"} else -1
    if risk <= 0:
        raise ValueError("trade risk must be positive")
    return broker_price, direction * (broker_price - float(trade["entry"])) / risk


async def broadcast_live_tick(clients, payload, *, timeout=2.0):
    """Isolate disconnected/slow consumers from the shared live tick owner."""
    async def send(ws):
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=timeout)
        except Exception:
            clients.discard(ws)
            # Closing is bounded too: a stalled transport must not pin polling.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(code=1013), timeout=timeout)

    # Clients can join/leave at every await; never iterate the mutable registry.
    await asyncio.gather(*(send(ws) for ws in tuple(clients)))


class TradeOpen(BaseModel):
    setup: int
    direction: str
    entry: float
    stop: float
    take: float
    notes: str = ""
    zones: list[dict] = Field(default_factory=list)
    # Текущая цена у брокера/на торгуемом CFD в момент открытия формы.
    # Если отличается от бесплатного фьючерса Yahoo, сохраняем постоянный basis
    # и продолжаем вести сделку живыми изменениями бесплатного ряда.
    reference_price: float | None = None


class TradeClose(BaseModel):
    trade_id: int
    result_r: float | None = None  # Explicit whole-trade override for old clients.
    execution_price: float | None = None
    notes: str | None = None


class TradeFill(BaseModel):
    trade_id: int
    request_id: str
    close_fraction_current: float
    execution_price: float
    ladder: bool = False
    expected_state_version: int | None = None


class ZonesUpdate(BaseModel):
    trade_id: int
    zones: list[dict] = Field(default_factory=list)


class TradeEdit(BaseModel):
    trade_id: int
    setup: int | None = None
    direction: str | None = None
    entry: float | None = None
    stop: float | None = None
    take: float | None = None
    result_r: float | None = None
    notes: str | None = None


class TradeDelete(BaseModel):
    trade_id: int


class AccountUpdate(BaseModel):
    name: str | None = None
    phase: str | None = None
    acc_size: float | None = None
    balance: float | None = None


class JournalAdd(BaseModel):
    """Ручное добавление закрытой сделки (бэкфилл истории)."""
    setup: int
    direction: str
    entry: float
    stop: float
    take: float
    result_r: float
    notes: str = ""
    opened_at: float | None = None


class HumanDecisionRecord(BaseModel):
    review_id: str
    policy: str
    reason_category: str
    note: str = ""


class ManagementExecution(BaseModel):
    decision_id: str
    trade_id: int
    executed: bool
    execution_price: float | None = None


class ShadowActionExecution(BaseModel):
    action_id: str
    trade_id: int
    executed: bool
    execution_price: float | None = None


class ExperimentRegister(BaseModel):
    experiment_id: str
    hypothesis: str
    features: list[str] = Field(default_factory=list)
    formula: str
    thresholds: dict = Field(default_factory=dict)
    train_period: tuple[float, float]
    validation_period: tuple[float, float]
    test_period: tuple[float, float]


class ExperimentResult(BaseModel):
    experiment_id: str
    result: dict = Field(default_factory=dict)


def _validation_report_from_score(score: dict, policy_shadow: dict) -> dict:
    """Project the canonical Q score into the legacy validation response shape."""
    take = score.get("take") or {}
    return {
        "version": score.get("version"),
        "n": score.get("n", 0),
        "brier": take.get("q_model_brier"),
        "log_loss": take.get("q_model_log_loss"),
        "calibration": take.get("reliability_curve") or [],
        "censored_n": score.get("censored_n", 0),
        "outcome_counts": score.get("outcome_counts") or {},
        "oos_scorecard": score.get("oos_scorecard") or {},
        "message": ("horizon-aligned forecast outcomes; manual-close paths "
                    "without coverage to H are censored"),
        "policy_shadow": policy_shadow,
        "promotion_allowed": False,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or settings_from_env()
    engine = Engine(settings)
    clients: set[WebSocket] = set()
    ai_last_call = 0.0
    ai_lock = asyncio.Lock()

    # ``tick_payload()`` is the canonical live-state calculation and may run
    # option/scenario math for an active trade.  One background materializer
    # owns that calculation; HTTP and WebSocket readers consume the latest
    # atomically-published process-local snapshot instead of recomputing the
    # same payload on their request threads.  Dict reference replacement is
    # atomic under CPython and the published payload is never mutated here.
    live_tick_snapshot = {
        "payload": None,
        "refreshed_at": None,
        "build_ms": None,
        "build_n": 0,
    }

    # Demo/unit-test applications have no active production trade and their
    # initial payload is cheap.  Prewarming it keeps the standalone create_app
    # contract deterministic; production is warmed only by the background
    # owner after the server starts.
    if getattr(settings, "demo", False):
        started = time.monotonic()
        live_tick_snapshot["payload"] = engine.tick_payload()
        live_tick_snapshot["refreshed_at"] = time.time()
        live_tick_snapshot["build_ms"] = (
            time.monotonic() - started
        ) * 1000.0
        live_tick_snapshot["build_n"] = 1

    app = FastAPI(title="Seiltanzer Terminal", version="0.1.0")
    app.state.engine = engine
    app.state.settings = settings
    app.state.live_tick_snapshot = live_tick_snapshot

    def publish_live_tick(payload: dict, *, build_ms: float) -> None:
        live_tick_snapshot["payload"] = payload
        live_tick_snapshot["refreshed_at"] = time.time()
        live_tick_snapshot["build_ms"] = float(build_ms)
        live_tick_snapshot["build_n"] = int(
            live_tick_snapshot["build_n"] or 0
        ) + 1

    def materialized_live_tick() -> dict:
        payload = live_tick_snapshot["payload"]
        if payload is None:
            raise HTTPException(
                status_code=503,
                detail="live tick snapshot is warming",
            )
        return payload

    # AI/Position Manager must consume this exact immutable generation instead
    # of running a second market calculation which can observe a different feed
    # state during a transient refresh failure.
    bind_canonical_tick = getattr(engine, "bind_canonical_tick_provider", None)
    if callable(bind_canonical_tick):
        bind_canonical_tick(materialized_live_tick)

    instruments_payload = {
        code: {
            "yahoo": instrument.yahoo,
            "quote_pair": instrument.swissquote_pair,
            "broker_symbol": instrument.tradingview_symbol,
            "options_proxy": instrument.options_proxy,
        }
        for code, instrument in INSTRUMENTS.items()
    }

    def build_live_state(tick: dict) -> tuple[dict, bytes]:
        """Build and encode the complete bootstrap state away from HTTP."""
        active = engine.journal.active_trade()
        payload = {
            "tick": tick,
            "ridge": engine.ridge_payload(),
            "journal": engine.journal.list_trades(),
            "edge_track": engine.journal.edge_track(),
            # Full validation reconstructs horizon-aligned outcomes and policy
            # diagnostics from the growing journal. It is research/UI data, not
            # live decision state, and loads independently after bootstrap.
            "validation": {
                "available": True,
                "summary_endpoint": "/api/validation/summary",
                "message": "validation summary loads independently",
                "production_authority": False,
            },
            "ai_history": (
                engine.journal.recent_ai_verdicts(active["id"], limit=10)
                if active else []
            ),
            "setups": _setups_payload(),
            "instruments": instruments_payload,
        }
        return payload, JSONResponse(content=payload).body

    # One background owner now materializes the complete ``/api/state``
    # contract, including its JSON bytes. HTTP and WebSocket readers consume
    # one atomically-published process-local generation; no request-time
    # journal/ridge/setup work or serialization remains on the bootstrap path.
    live_state_snapshot = {
        "current": None,
        "error": None,
        "revision": 0,
        "build_n": 0,
    }
    live_state_build_lock = asyncio.Lock()
    live_state_meta_lock = threading.Lock()

    def publish_live_state(
        payload: dict, encoded: bytes, *, build_ms: float,
        expected_revision: int | None = None,
    ) -> dict | None:
        with live_state_meta_lock:
            if (
                expected_revision is not None
                and expected_revision != live_state_snapshot["revision"]
            ):
                return None
            live_state_snapshot["build_n"] = int(
                live_state_snapshot["build_n"]
            ) + 1
            current = {
                "payload": payload,
                "encoded": encoded,
                "refreshed_at": time.time(),
                "build_ms": float(build_ms),
                "build_n": live_state_snapshot["build_n"],
                "revision": live_state_snapshot["revision"],
            }
            live_state_snapshot["current"] = current
            live_state_snapshot["error"] = None
            return current

    def invalidate_live_state() -> None:
        with live_state_meta_lock:
            live_state_snapshot["revision"] = int(
                live_state_snapshot["revision"]
            ) + 1
            live_state_snapshot["current"] = None
            live_state_snapshot["error"] = None

    def live_state_revision() -> int:
        with live_state_meta_lock:
            return int(live_state_snapshot["revision"])

    def record_live_state_error(
        exc: Exception, *, expected_revision: int,
    ) -> None:
        with live_state_meta_lock:
            if expected_revision == live_state_snapshot["revision"]:
                live_state_snapshot["error"] = type(exc).__name__

    async def refresh_live_state(
        tick: dict | None = None, *, expected_revision: int,
    ) -> dict | None:
        """Serialize one complete generation without blocking the event loop."""
        async with live_state_build_lock:
            if tick is None:
                tick = materialized_live_tick()
            started = time.monotonic()
            worker = asyncio.create_task(asyncio.to_thread(
                build_live_state, tick,
            ))
            try:
                payload, encoded = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # ``to_thread`` cannot be cancelled once running. Keep the
                # single-flight lock until its SQLite reader has really exited
                # so shutdown and a second refresh cannot race the orphan.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    error = worker.exception()
                    if error is not None:
                        record_live_state_error(
                            error, expected_revision=expected_revision,
                        )
                raise
            except Exception as exc:
                record_live_state_error(
                    exc, expected_revision=expected_revision,
                )
                raise
            return publish_live_state(
                payload,
                encoded,
                build_ms=(time.monotonic() - started) * 1000.0,
                expected_revision=expected_revision,
            )

    def materialized_live_state() -> dict:
        with live_state_meta_lock:
            error = live_state_snapshot["error"]
            current = live_state_snapshot["current"]
        if error is not None:
            raise HTTPException(
                status_code=503,
                detail="live state snapshot refresh failed",
            )
        if current is None:
            raise HTTPException(
                status_code=503,
                detail="live state snapshot is warming",
            )
        return current

    app.state.live_state_snapshot = live_state_snapshot
    app.state.live_state_build_lock = live_state_build_lock

    @app.middleware("http")
    async def auth_and_no_cache(request, call_next):
        # Basic Auth check
        auth_user = os.environ.get("TERMINAL_USER")
        auth_pass = os.environ.get("TERMINAL_PASS")
        if auth_user and auth_pass:
            auth_header = request.headers.get("Authorization")
            authorized = False
            if auth_header and auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, _, password = decoded.partition(":")
                    if (secrets.compare_digest(username.encode("utf8"), auth_user.encode("utf8")) and
                        secrets.compare_digest(password.encode("utf8"), auth_pass.encode("utf8"))):
                        authorized = True
                except Exception:
                    pass
            if not authorized:
                return Response("Unauthorized", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Seiltanzer Terminal"'})

        # запрет кэша на фронт: гарантирует, что браузер получит свежий JS/CSS
        # (иначе после git pull старый app.js мог остаться в кэше)
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        return resp

    # ------------------------------------------------------------ background

    async def poll_loop():
        last = {"price": 0.0, "proxy_price": 0.0, "intraday": 0.0, "vols": 0.0,
                "daily": 0.0, "chain": 0.0, "iv_surface": 0.0, "correlation": 0.0, "bybit": 0.0}
        running: dict[str, asyncio.Task] = {}
        state_task: asyncio.Task | None = None
        state_last_finished = 0.0
        state_refresh_period = (
            LIVE_STATE_REFRESH_PERIOD_FAST_SEC
            if (settings.demo or settings.stream)
            else LIVE_STATE_REFRESH_PERIOD_PRODUCTION_SEC
        )
        # при живом стриме цену «опрашиваем» часто (берём свежий тик из памяти)
        price_period = 1.0 if (settings.demo or settings.stream) else settings.price_poll_sec
        periods = {
            "price": price_period,
            "proxy_price": 1.0 if (settings.demo or settings.stream)
                           else settings.proxy_poll_sec,
            "intraday": 60.0,
            "vols": 5.0 if settings.demo else settings.vol_poll_sec,
            "daily": 30.0 if settings.demo else 300.0,
            "chain": 1.0 if settings.demo else settings.chain_poll_sec,
            "iv_surface": 1.0 if settings.demo else settings.chain_poll_sec,
            "correlation": 30.0 if settings.demo else 300.0,
            "bybit": 15.0,
        }
        jobs = {
            "price": engine.market.refresh_price,
            "proxy_price": engine.market.refresh_proxy_price,
            "intraday": lambda: _refresh_intraday_archive(engine),
            "vols": engine.market.refresh_vols,
            "daily": engine.market.refresh_daily,
            "chain": engine.market.refresh_chain,
            "iv_surface": engine.market.refresh_iv_surface,
            "correlation": engine.market.refresh_correlation,
            "bybit": engine.market.refresh_bybit,
        }
        try:
            while True:
                now = time.time()
                # Долгий option_chain/IV запрос больше не останавливает тиковый
                # WebSocket: каждый фид работает максимум в одном background task.
                for name, task in list(running.items()):
                    if task.done():
                        with contextlib.suppress(Exception):
                            task.result()
                        del running[name]
                if state_task is not None and state_task.done():
                    with contextlib.suppress(Exception):
                        state_task.result()
                    state_task = None
                    # Count the cooldown from completion, not submission. If a
                    # SQLite reader is slow or fails, the next attempt cannot
                    # form a retry storm and starve the live request path.
                    state_last_finished = time.monotonic()
                for name, fn in jobs.items():
                    if name not in running and now - last[name] >= periods[name]:
                        last[name] = now
                        running[name] = asyncio.create_task(asyncio.to_thread(fn))

                # Keep canonical tick work off the uvicorn event loop.
                build_started = time.monotonic()
                payload = await asyncio.to_thread(engine.tick_payload)
                publish_live_tick(
                    payload,
                    build_ms=(time.monotonic() - build_started) * 1000.0,
                )
                await broadcast_live_tick(clients, payload)
                # Full journal/ridge/setup materialization may wait on SQLite.
                # The HTTP route serves the last encoded generation directly, so
                # production does not need a new build for every 2s live tick.
                # Keep one generation in flight and an idle gap after completion
                # to avoid retry pressure while research is using the same DB.
                if (
                    state_task is None
                    and (
                        state_last_finished <= 0.0
                        or time.monotonic() - state_last_finished
                        >= state_refresh_period
                    )
                ):
                    state_task = asyncio.create_task(refresh_live_state(
                        payload,
                        expected_revision=live_state_revision(),
                    ))
                await asyncio.sleep(
                    1.0 if (settings.demo or settings.stream) else 2.0)
        finally:
            # Не закрываем sqlite, пока уже запущенный фид ещё может писать кэш.
            pending = list(running.values())
            if state_task is not None:
                pending.append(state_task)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def passive_loop():
        while True:
            await asyncio.to_thread(engine.passive.step)
            await asyncio.sleep(2.0 if settings.demo else 10.0)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if engine.stream_hub is not None:
            engine.stream_hub.start()      # живой WS-стрим цены (нужен event loop)
        task = asyncio.create_task(poll_loop())
        passive_task = asyncio.create_task(passive_loop())
        yield
        task.cancel()
        passive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(asyncio.CancelledError):
            await passive_task
        if engine.stream_hub is not None:
            await engine.stream_hub.stop()
        engine.close()

    app.router.lifespan_context = lifespan

    # ------------------------------------------------------------------- api

    @app.get("/api/state")
    async def api_state(fresh: bool = False):
        if fresh:
            try:
                revision = live_state_revision()
                current = await refresh_live_state(
                    expected_revision=revision,
                )
                if current is None:
                    raise RuntimeError("live state refresh superseded")
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="live state refresh failed",
                ) from exc
        else:
            current = materialized_live_state()
        return Response(content=current["encoded"], media_type="application/json")

    @app.get("/api/market/bybit")
    async def api_bybit_status():
        return {"instrument": engine.market.instrument_code, **engine.market.bybit_payload(),
                "active_price": {key: engine.market.price.get(key)
                                 for key in ("value", "status", "source", "fallback")}}

    @app.get("/api/ai/history")
    def api_ai_history():
        active = engine.journal.active_trade()
        return {
            "trade_id": active["id"] if active else None,
            "items": (engine.journal.recent_ai_verdicts(active["id"], limit=10)
                      if active else []),
        }

    def _setups_payload():
        out = []
        for num, s in SETUPS.items():
            stats = engine.journal.setup_stats(num, settings.journal_min_trades)
            jn, jw = engine.journal.journal_counts(num)
            out.append({
                "num": num, "name": s.name, "instrument": s.instrument,
                "rr": s.rr, "builtin_n": s.n, "builtin_wins": s.wins,
                "winrate": stats.winrate, "n": stats.n, "wins": stats.wins,
                "calibration": stats.source, "journal_n": jn, "journal_wins": jw,
                "filters": list(s.filters),
                "efficiency": stats.efficiency,
            })
        return out

    # Demo/unit-test applications have no active production trade and their
    # initial payload is cheap. Prewarming keeps direct route calls
    # deterministic; production is warmed only by the background owner.
    if getattr(settings, "demo", False):
        started = time.monotonic()
        payload, encoded = build_live_state(materialized_live_tick())
        publish_live_state(
            payload,
            encoded,
            build_ms=(time.monotonic() - started) * 1000.0,
        )

    @app.get("/api/setups")
    def api_setups():
        return _setups_payload()

    @app.get("/api/diagnostics")
    def api_diagnostics():
        return engine.diagnostics_payload()

    @app.get("/api/validation")
    def api_validation():
        # The full Q score is the expensive part. Build it exactly once and
        # project that same immutable result into both compatibility views.
        score = engine.journal.q_calibration_report()
        report = _validation_report_from_score(
            score, engine.journal.policy_shadow_report())
        report["counterfactual_replay"] = engine.journal.counterfactual_report()
        report["q_calibration"] = score
        return report

    @app.get("/api/validation/summary")
    def api_validation_summary():
        """Detailed display summary, deliberately isolated from ``/api/state``."""
        return engine.journal.validation_report()

    @app.get("/api/research/passive/status")
    def api_passive_status():
        return engine.passive.status()

    @app.get("/api/research/passive/calibration")
    def api_passive_calibration():
        return engine.passive.calibration_report()

    @app.get("/api/research/passive/observations")
    def api_passive_observations(limit: int = 100,
                                 instrument: str | None = None):
        return engine.passive.observations(limit=limit, instrument=instrument)

    @app.get("/api/research/passive/edge")
    def api_passive_edge():
        return engine.passive.edge_report(engine.journal.counterfactual_report())

    @app.get("/api/research/counterfactual")
    def api_counterfactual_research(trade_id: int | None = None,
                                    limit: int = 100):
        return engine.journal.counterfactual_report(trade_id, limit=limit)

    @app.post("/api/research/human-decision")
    def api_human_decision(req: HumanDecisionRecord):
        try:
            return engine.journal.record_human_decision(
                req.review_id, req.policy, req.reason_category, req.note)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/research/experiments")
    def api_experiment_report():
        return engine.journal.experiment_report()

    @app.post("/api/research/experiments")
    def api_experiment_register(req: ExperimentRegister):
        try:
            return engine.journal.register_experiment(
                experiment_id=req.experiment_id, hypothesis=req.hypothesis,
                features=req.features, formula=req.formula,
                thresholds=req.thresholds, train_period=req.train_period,
                validation_period=req.validation_period,
                test_period=req.test_period,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/research/experiments/result")
    def api_experiment_result(req: ExperimentResult):
        try:
            return engine.journal.record_experiment_result(
                req.experiment_id, req.result)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/chain")
    def api_chain(ticker: str | None = None):
        # тикер сейчас определяется активным инструментом; параметр — для явности
        ridge = engine.ridge_payload()
        if ticker and ridge.get("proxy") not in (None, ticker):
            raise HTTPException(400, f"активный прокси: {ridge.get('proxy')}, "
                                     f"запрошен {ticker}")
        return ridge

    @app.get("/api/analytics/gex-migration")
    def api_analytics_gex_migration():
        return engine.gex_migration_payload()

    @app.get("/api/analytics/regime-phase")
    def api_analytics_regime_phase():
        return engine.macro_regime_payload()

    @app.get("/api/analytics/wavelet")
    def api_analytics_wavelet():
        return engine.wavelet_payload()

    @app.get("/api/analytics/correlation-graph")
    def api_analytics_correlation_graph():
        return engine.cross_asset_payload()

    @app.get("/api/journal")
    def api_journal():
        return engine.journal.list_trades()

    @app.post("/api/journal")
    def api_journal_add(req: JournalAdd):
        try:
            t = engine.journal.add_closed(req.setup, req.direction, req.entry,
                                          req.stop, req.take, req.result_r,
                                          req.notes, req.opened_at)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        invalidate_live_state()
        return t

    @app.get("/api/journal.csv", response_class=PlainTextResponse)
    def api_journal_csv():
        return PlainTextResponse(engine.journal.export_csv(),
                                 media_type="text/csv; charset=utf-8")

    @app.post("/api/trade")
    def api_trade_open(req: TradeOpen):
        setup = SETUPS.get(req.setup)
        if setup is None:
            raise HTTPException(400, f"неизвестный сетап: {req.setup}")
        try:
            # Basis имеет смысл только против правильного бесплатного ряда.
            # Без активной сделки движок мог всё ещё стоять, например, на NAS100,
            # пока пользователь открывает XAU.
            if engine.market.instrument_code != setup.instrument:
                engine.market.set_instrument(setup.instrument)
                engine.market.refresh_price()
            raw_price = engine.market.price.get("value")
            reference = req.reference_price
            if reference is not None and (not math.isfinite(reference) or reference <= 0):
                raise ValueError("текущая цена брокера должна быть положительным числом")
            if reference is not None:
                _validate_reference_source(engine.market)
            if reference is not None and raw_price is None:
                raise ValueError(
                    "не удалось получить бесплатную котировку выбранного "
                    "инструмента — basis сейчас зафиксировать нельзя")
            quote_offset = ((reference - raw_price)
                            if reference is not None and raw_price is not None else 0.0)
            trade = engine.journal.open_trade(
                setup=req.setup, instrument=setup.instrument,
                direction=req.direction, entry=req.entry, stop=req.stop,
                take=req.take, notes=req.notes, zones=req.zones,
                quote_offset=quote_offset, raw_price_at_open=raw_price,
                quote_source=engine.market.price.get("source"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        engine.position.open_trade(trade)
        engine.on_trade_opened(trade)
        invalidate_live_state()
        return {**trade, "position_state": engine.position.state(trade)}

    @app.post("/api/trade/close")
    def api_trade_close(req: TradeClose):
        try:
            live_trade = engine.journal.get_trade(req.trade_id)
            if live_trade['status'] == 'closed':
                return {**live_trade, 'idempotent': True,
                        'position_state': engine.position.state(live_trade)}
            if req.execution_price is not None and req.result_r is not None:
                raise ValueError('укажите цену остатка или общий результат сделки, не оба значения')
            if req.result_r is not None:
                if not math.isfinite(req.result_r):
                    raise ValueError('результат R должен быть конечным числом')
                state = engine.position.state(live_trade)
                realized = state['realized_r_weighted']
                remaining = state['remaining_position_fraction']
                execution_r = ((req.result_r - realized) / remaining
                               if realized is not None and remaining > 0 else None)
                execution_price = None
                source = 'user_supplied_whole_trade_result'
            else:
                execution_price, execution_r = _acknowledged_execution(
                    live_trade, engine.tick_payload(), req.execution_price)
                if execution_r is None:
                    raise ValueError('нет цены исполнения; укажите фактическую цену закрытия остатка')
                source = ('user_supplied_broker_fill' if req.execution_price is not None
                          else 'quote_at_acknowledgement_estimate')
            engine.position.terminal_exit(
                live_trade, event_type="MANUAL_EXIT",
                execution_price=execution_price, execution_r=execution_r,
                execution_price_source=source, manual_total_r=req.result_r)
            if req.notes is not None:
                engine.journal.edit_trade(req.trade_id, notes=req.notes)
            engine.journal.resolve_decision_replays(req.trade_id)
            closed = engine.journal.get_trade(req.trade_id)
            invalidate_live_state()
            return {**closed, "position_state": engine.position.state(live_trade)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/trade/zones")
    def api_trade_zones(req: ZonesUpdate):
        try:
            updated = engine.journal.update_zones(req.trade_id, req.zones)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        invalidate_live_state()
        return updated

    @app.post('/api/trade/fill')
    def api_trade_fill(req: TradeFill):
        try:
            trade = engine.journal.get_trade(req.trade_id)
            price, execution_r = _acknowledged_execution(trade, {}, req.execution_price)
            result = engine.position.record_manual_fill(
                trade, request_id=req.request_id, close_fraction_current=req.close_fraction_current,
                execution_price=price, execution_r=execution_r, ladder=req.ladder,
                expected_state_version=req.expected_state_version)
        except StaleDecisionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return complete_ack(trade, result)

    @app.post("/api/trade/edit")
    def api_trade_edit(req: TradeEdit):
        try:
            previous = engine.journal.get_trade(req.trade_id)
            trade = engine.journal.edit_trade(
                req.trade_id, setup=req.setup, direction=req.direction,
                entry=req.entry, stop=req.stop, take=req.take,
                result_r=req.result_r, notes=req.notes)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        geometry_changed = any(
            previous.get(key) != trade.get(key)
            for key in ("setup", "instrument", "direction", "entry", "stop", "take"))
        if geometry_changed:
            engine.position.supersede_trade(req.trade_id, "trade_geometry_changed")
        if trade["status"] == "open":
            engine.on_trade_edited(trade)
            response = {**trade, "position_state": engine.position.state(trade)}
        else:
            response = trade
        invalidate_live_state()
        return response

    @app.post("/api/trade/delete")
    def api_trade_delete(req: TradeDelete):
        try:
            engine.journal.delete_trade(req.trade_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        invalidate_live_state()
        return {"ok": True}

    @app.post("/api/account")
    def api_account(req: AccountUpdate):
        try:
            account = engine.journal.update_account(**req.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        invalidate_live_state()
        return account

    @app.post("/api/ai/verdict")
    async def api_ai_verdict():
        nonlocal ai_last_call
        req_id = new_ai_request_id()
        started = time.monotonic()
        if ai_lock.locked():
            return JSONResponse(
                status_code=429,
                content=ai_error_body(
                    "ai_request_in_progress", "ИИ уже анализирует предыдущий снимок",
                    req_id, retriable=True),
            )
        if time.monotonic() - ai_last_call < 15:
            return JSONResponse(
                status_code=429,
                content=ai_error_body(
                    "ai_rate_limited", "Повторный анализ доступен через 15 секунд",
                    req_id, retriable=True),
            )
        async with ai_lock:
            try:
                snapshot = build_snapshot(engine)
            except Exception as exc:
                log_ai_event(req_id=req_id, stage="snapshot_error", started=started, exc=exc)
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "snapshot_error", "Не удалось зафиксировать снимок сделки",
                        req_id, retriable=False),
                )
            if snapshot.get("trade_id") is None:
                return JSONResponse(
                    status_code=400,
                    content=ai_error_body(
                        "no_active_trade", "Нет активной сделки для ИИ-разбора",
                        req_id, retriable=False),
                )
            # Reject explicit missing price authority before state changes or
            # expensive enrichment. Raw quote presence is not source authority.
            price_manager = snapshot.get("policy_manager") or {}
            price_rows = ((price_manager.get("input_audit") or {}).get("rows") or {})
            if isinstance(price_rows, dict) and "instrument_price" in price_rows:
                from .ai_report_semantics_guard import authoritative_current_price_available
                if (not isinstance(price_rows["instrument_price"], dict)
                        or not authoritative_current_price_available(snapshot)):
                    return JSONResponse(
                        status_code=503,
                        content=ai_error_body(
                            "authoritative_price_unavailable",
                            "Авторитетная текущая цена инструмента недоступна",
                            req_id, retriable=True),
                    )
            ai_last_call = time.monotonic()
            trade_id = int(snapshot["trade_id"])
            # Finalize the economic state at the API boundary. Policy analysis
            # may have advanced max_r/BE while constructing the snapshot.
            active_trade = engine.journal.active_trade()
            if active_trade and int(active_trade["id"]) == trade_id:
                position_state = engine.position.sync_be(active_trade)
                snapshot["position_state"] = position_state
                geometry = snapshot.get("trade_geometry") or {}
                geometry.update({
                    "entry": active_trade["entry"],
                    "original_stop": active_trade["stop"],
                    "active_risk_barrier": position_state["active_stop_price"],
                    "active_risk_barrier_type": position_state["active_stop_type"],
                    "final_take": position_state["take"],
                    "remaining_position_fraction":
                        position_state["remaining_position_fraction"],
                    "realized_position_fraction":
                        position_state["realized_position_fraction"],
                })
                snapshot["trade_geometry"] = geometry
                from .ai_report_semantics_guard import repair_snapshot_geometry
                repair_snapshot_geometry(snapshot)
                decision = _refresh_management_decision(
                    engine, snapshot, active_trade)
            else:
                decision = ((snapshot.get("policy_manager") or {})
                            .get("management_decision"))
                return JSONResponse(status_code=409, content=ai_error_body(
                    "stale_decision", "Активная сделка изменилась во время расчёта",
                    req_id, retriable=True))
            # Evaluate every extended action alongside the base policies. The
            # independent provider sees quantified candidates, not a picked winner.
            await asyncio.to_thread(_attach_family_source_bundle, engine, snapshot)
            from .unified_edge_runtime_context import attach_unified_edge_context
            from .runtime_git_identity import runtime_git_sha
            await asyncio.to_thread(attach_unified_edge_context, engine, snapshot,
                                    expected_sha=runtime_git_sha())
            macro_factory = getattr(getattr(engine, "passive", None), "_macro_data_factory", None)
            if macro_factory is not None:
                from .macro_t0_context import build_macro_t0_context
                snapshot["macro_context_v1"] = await asyncio.to_thread(
                    build_macro_t0_context, macro_factory, float(snapshot["captured_ts"]))
            from .edge_family_event_reaction import attach_observed_event_reaction
            await asyncio.to_thread(attach_observed_event_reaction, engine, snapshot)
            from .edge_regime import refine_regime_with_events
            refine_regime_with_events(snapshot)
            from .active_management import select_active_management
            await asyncio.to_thread(select_active_management, snapshot)
            try:
                review_id = canonical_snapshot(snapshot)["review_id"]
                # The review identity is frozen before provider output and manual
                # action registration; the final persisted bytes still get their
                # own independently verified content hash.
                snapshot["review_id"] = review_id
            except Exception as exc:
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="snapshot_error",
                    started=started, exc=exc)
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "snapshot_error", "Снимок сделки не прошёл проверку целостности",
                        req_id, retriable=False),
                )
            degraded = False
            provider_failure = None
            try:
                result = await asyncio.to_thread(request_verdict, snapshot)
                if not isinstance(result, dict) or not isinstance(result.get("verdict"), str):
                    raise RuntimeError("provider_invalid_payload")
            except RuntimeError as exc:
                provider_failure = normalize_provider_error(exc)
                if "invalid_payload" in str(exc).lower():
                    provider_failure = {"code": "provider_invalid_payload", "retriable": True}
                result = deterministic_result(snapshot, render_policy_report)
                degraded = True
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="provider_fallback",
                    review_id=review_id,
                    started=started, provider="openrouter", mode="deterministic_fallback",
                    exc=exc,
                )
            except Exception as exc:
                # ValueError/TypeError and other unexpected application failures
                # must remain visible as programming errors, not provider outages.
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="internal_error",
                    review_id=review_id,
                    started=started, provider="openrouter", exc=exc,
                )
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "ai_internal_error", "Не удалось сформировать ИИ-разбор",
                        req_id, retriable=False),
                )
            try:
                from .unified_edge_ensemble import build_unified_ensemble
                audit = await asyncio.to_thread(
                    build_unified_ensemble, snapshot, result.get("llm_shadow_decision"))
                if audit.get("common_economics_invalid") is True:
                    return JSONResponse(status_code=422, content={
                        **ai_error_body("invalid_common_economics",
                            "Экономика действий не прошла проверку: обновите данные издержек и повторите разбор",
                            req_id, retriable=True),
                        "common_economics_reason": audit.get("common_economics_reason"),
                    })
                decision = await asyncio.to_thread(
                    _publish_unified_review, engine, snapshot, result, review_id,
                    active_trade, audit)
            except StaleDecisionError as exc:
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="stale_decision",
                    review_id=review_id, started=started, exc=exc)
                return JSONResponse(
                    status_code=409,
                    content=ai_error_body(
                        "stale_decision", "Состояние позиции изменилось во время расчёта; запросите новый разбор",
                        req_id, retriable=True))
            except Exception as exc:
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="journal_error",
                    review_id=review_id,
                    started=started, mode=("deterministic_fallback" if degraded else "llm"),
                    exc=exc,
                )
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "journal_error", "Разбор рассчитан, но не удалось сохранить снимок",
                        req_id, retriable=False),
                )
            invalidate_live_state()
            body = ai_success_body(
                result, req_id, degraded=degraded,
                provider_failure=provider_failure,
            )
            body["unified_edge_ensemble"] = result["unified_edge_ensemble"]
            if result.get("selected_management_action"):
                body["selected_management_action"] = result["selected_management_action"]
            body["edge_management"] = current_edge_management_payload(snapshot)
            body["edge_management"]["unified_edge_ensemble"] = result["unified_edge_ensemble"]
            body["edge_management"]["available"] = True
            body["context_reviews"] = len(snapshot.get("previous_reviews") or [])
            log_ai_event(
                req_id=req_id, trade_id=trade_id, stage="complete", started=started,
                review_id=review_id,
                provider="openrouter", mode=body["mode"],
            )
            return JSONResponse(content=body)

    @app.post("/api/ai/decision/ack")
    def api_ai_decision_ack(req: ManagementExecution):
        try:
            trade = execution_trade(req.trade_id, req.decision_id, req.executed)
            tick = engine.tick_payload()
            execution_price, execution_r = _acknowledged_execution(
                trade, tick, req.execution_price)
            if req.decision_id.startswith(("shadow-action-", "management-action-")):
                acknowledged = engine.position.acknowledge_shadow_action(
                    action_id=req.decision_id, trade=trade, executed=req.executed,
                    execution_price=execution_price, execution_r=execution_r,
                    execution_price_source=execution_source(req.execution_price))
                acknowledged["decision_id"] = req.decision_id
            else:
                acknowledged = engine.position.acknowledge(
                    decision_id=req.decision_id, trade=trade, executed=req.executed,
                    execution_price=execution_price, execution_r=execution_r,
                    execution_price_source=("user_supplied_broker_fill" if req.execution_price is not None
                                            else "quote_at_acknowledgement_estimate"))
        except StaleDecisionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return complete_ack(trade, acknowledged)

    @app.post("/api/ai/shadow-action/ack")
    def api_ai_shadow_action_ack(req: ShadowActionExecution):
        try:
            trade = execution_trade(req.trade_id, req.action_id, req.executed)
            tick = engine.tick_payload()
            execution_price, execution_r = _acknowledged_execution(
                trade, tick, req.execution_price)
            acknowledged = engine.position.acknowledge_shadow_action(
                action_id=req.action_id, trade=trade, executed=req.executed,
                execution_price=execution_price, execution_r=execution_r,
                execution_price_source=execution_source(req.execution_price),
            )
        except StaleDecisionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return complete_ack(trade, acknowledged)

    def execution_source(price):
        return ('user_supplied_broker_fill' if price is not None
                else 'quote_at_acknowledgement_estimate')

    def execution_trade(trade_id, action_id, executed):
        trade = engine.journal.get_trade(trade_id)
        if trade['status'] == 'open':
            active = engine.journal.active_trade()
            if active and active['id'] == trade_id:
                return trade
        elif executed:
            # Network retries remain idempotent even after automatic closure,
            # including when the user has already opened the next trade.
            table, key = ('llm_shadow_manual_actions', 'action_id') if action_id.startswith(
                ('shadow-action-', 'management-action-')) else ('management_decisions', 'decision_id')
            with engine.position._lock:
                row = engine.position._conn.execute(
                    f'SELECT status FROM {table} WHERE {key}=? AND trade_id=?',
                    (action_id, trade_id),
                ).fetchone()
            if row and row['status'] == 'executed':
                return trade
        raise StaleDecisionError('active trade changed')

    def complete_ack(trade, acknowledged):
        closed = engine.journal.get_trade(trade['id'])
        if closed['status'] == 'closed':
            engine.position.supersede_trade(trade['id'], 'position_closed')
            if not acknowledged.get('idempotent'):
                engine.journal.resolve_decision_replays(trade['id'])
            acknowledged.update(trade_closed=True, trade=closed,
                                journal_result_r=closed['result_r'])
        invalidate_live_state()
        return acknowledged

    @app.get('/api/trade/management')
    def api_trade_management(trade_id: int):
        from .trade_settlement import management_summary
        try:
            trade = engine.journal.get_trade(trade_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        events = engine.position.events(trade_id)
        return {'trade': trade, 'events': events, 'summary': management_summary(events)}

    @app.get("/api/position")
    def api_position_state():
        trade = engine.journal.active_trade()
        return {
            "trade_id": trade["id"] if trade else None,
            "position_state": engine.position.state(trade) if trade else None,
            "events": engine.position.events(trade["id"]) if trade else [],
            "shadow_actions": engine.position.shadow_actions(trade["id"]) if trade else [],
        }

    # -------------------------------------------------------------------- ws

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        
        # WebSocket Auth (если включено)
        auth_user = os.environ.get("TERMINAL_USER")
        auth_pass = os.environ.get("TERMINAL_PASS")
        if auth_user and auth_pass:
            auth_header = ws.headers.get("Authorization")
            authorized = False
            if auth_header and auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, _, password = decoded.partition(":")
                    if (secrets.compare_digest(username.encode("utf8"), auth_user.encode("utf8")) and
                        secrets.compare_digest(password.encode("utf8"), auth_pass.encode("utf8"))):
                        authorized = True
                except Exception:
                    pass
            if not authorized:
                await ws.close(code=1008)
                return

        try:
            # The background owner may still be producing the first production
            # snapshot.  Wait boundedly without running canonical scenario math
            # on the event loop or on a second request thread.
            deadline = time.monotonic() + 30.0
            while live_tick_snapshot["payload"] is None:
                if time.monotonic() >= deadline:
                    await ws.close(code=1013)
                    return
                await asyncio.sleep(0.05)
            await asyncio.wait_for(
                ws.send_json(live_tick_snapshot["payload"]), timeout=2.0,
            )
            clients.add(ws)
            while True:
                await ws.receive_text()  # клиент ничего не шлёт; держим сокет
        except WebSocketDisconnect:
            pass
        except TimeoutError:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(code=1013), timeout=2.0)
        finally:
            clients.discard(ws)

    # ---------------------------------------------------------------- static

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
