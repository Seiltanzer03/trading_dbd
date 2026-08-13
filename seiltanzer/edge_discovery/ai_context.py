"""Bounded causal EDE context for AI explanation, never action authority."""
from __future__ import annotations

import json
from typing import Any

from .maturity import data_maturity


CONTRACT_VERSION = "g1s-ede-ai-causal-context-v1.2"


def _available(value: Any) -> bool:
    if not value:
        return False
    return not isinstance(value, dict) or value.get("available") is not False


def _pick(source: Any, names: tuple[str, ...]) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    return {name: source.get(name) for name in names if name in source}


def _compact_derivatives(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    keys = ("available", "value", "slope", "acceleration", "rolling_rank",
            "rolling_zscore", "direction_consistency", "sample_count",
            "time_span_minutes", "confidence", "source_quality")
    return {name: _pick(row, keys) if isinstance(row, dict) else row
            for name, row in source.items()}


def _compact_policy_regime(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    return {
        "atr": _pick(source.get("atr"), ("phase", "ratio", "value")),
        "sigma": _pick(source.get("sigma"), ("phase", "ratio", "annual", "value")),
        "vrp": _pick(source.get("vrp"), ("iv_rv_ratio", "phase", "value")),
        "regime": _pick(source.get("regime"), ("regime", "phase", "confidence")),
    }


def _data_readiness(engine: Any) -> tuple[str, list[dict[str, Any]]]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return "INSUFFICIENT_DATA", []
    try:
        from seiltanzer.g1_short_horizon_status_materialization import _horizon_summary
        horizons = [_horizon_summary(runtime, horizon) for horizon in (15, 30, 60, 120, 240)]
    except Exception:
        return "INSUFFICIENT_DATA", []
    statuses = [
        data_maturity(
            raw_n=int(row.get("raw_resolved") or 0),
            effective_n=int(row.get("effective_n") or 0),
            temporal_blocks=int(row.get("trading_days") or 0))
        for row in horizons]
    rank = {
        "INSUFFICIENT_DATA": 0, "DATA_READY_EARLY": 1,
        "DATA_READY_RESEARCH": 2, "DATA_READY_PROVISIONAL": 3,
        "DATA_READY_ROBUST": 4,
    }
    best = max(statuses, key=lambda item: rank[item], default="INSUFFICIENT_DATA")
    compact = [{
        "horizon_minutes": row["horizon_minutes"],
        "raw": row.get("raw_resolved"), "effective": row.get("effective_n"),
        "temporal_blocks": row.get("trading_days"), "data_maturity": status,
    } for row, status in zip(horizons, statuses)]
    return best, compact


def _latest_frozen_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    instrument = str((snapshot.get("strategy") or {}).get("instrument") or "")
    cutoff = snapshot.get("captured_ts")
    if runtime is None or not instrument or cutoff is None:
        return {}
    try:
        with runtime._lock:
            row = runtime._conn.execute(
                "SELECT captured_ts,frozen_features_json FROM g1s_observations "
                "WHERE instrument=? AND captured_ts<=? "
                "ORDER BY captured_ts DESC LIMIT 1", (instrument, float(cutoff))).fetchone()
        if row is None:
            return {}
        captured = float(row["captured_ts"])
        frozen = json.loads(row["frozen_features_json"])
        block = frozen.get("g1s_evidence_v3") or {}
        if float(block.get("captured_ts", captured)) > captured+1e-6:
            return {}
        result = dict(block)
        result["observation_t0"] = captured
        result["v2_option_context"] = (
            (frozen.get("g1s_evidence_v2") or {}).get("option_context") or {})
        return result
    except Exception:
        return {}


def build_ai_ede_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose already-observed context after the action has been frozen.

    No edge candidate is inferred from coverage. Until a separately promoted
    artifact is wired in, EDGE_MATURITY is deliberately insufficient and the
    confidence modifier is exactly zero.
    """
    manager = snapshot.get("policy_manager") or {}
    evidence = manager.get("evidence") or {}
    option_state = (manager.get("option_derivative_state")
                    or evidence.get("option_derivative_state") or {})
    metrics = option_state.get("metrics") or {}
    frozen = _latest_frozen_context(engine, snapshot)
    frozen_options = frozen.get("option_static") or {}
    frozen_dynamics = frozen.get("option_dynamics") or {}
    frozen_gex = frozen.get("gex") or {}
    frozen_v2_options = frozen.get("v2_option_context") or {}
    derivatives = (frozen_dynamics.get("derivatives")
                   or option_state.get("named_derivatives") or {})
    frozen_cross = frozen.get("cross_asset") or {}
    policy_cross = evidence.get("correlation") or {}
    frozen_regime = frozen.get("macro") or {}
    policy_regime = evidence.get("atr_regime") or {}
    frozen_price = frozen.get("price_volatility") or {}
    policy_price = evidence.get("live_price") or {}
    families = {
        "OPTIONS": {
            "available": bool(option_state.get("available") or metrics
                              or frozen_options.get("available")),
            "metrics": {name: {
                key: row.get(key) for key in (
                    "value", "slope", "acceleration", "sample_count",
                    "confidence", "source_quality")
            } for name, row in metrics.items()
                        if name in {
                            "iv", "skew", "term_slope", "gex_force", "gex_stiffness",
                            "distance_to_zero_gamma", "barrier_ev", "bop",
                        } and isinstance(row, dict)},
            "frozen_t0": {
                "iv": frozen_options.get("iv"), "iv_rv_ratio": frozen_options.get("iv_rv_ratio"),
                "skew": frozen_options.get("skew"), "term_slope": frozen_options.get("term_slope"),
                "delta": frozen_options.get("delta"), "vanna": frozen_options.get("vanna"),
                "charm": frozen_options.get("charm_per_day"),
                "gex_net_balance": frozen_v2_options.get("gex_net_balance"),
                "gex_field": frozen_gex.get("field_score"),
                "gex_force": frozen_gex.get("force_score"),
                "gex_stiffness": frozen_gex.get("stiffness_score"),
                "zero_gamma": frozen_gex.get("zero_gamma_log_moneyness"),
                "asof": frozen.get("observation_t0"),
            },
        },
        "OPTION_DYNAMICS": {
            "available": bool(option_state.get("named_derivatives")
                              or frozen_dynamics.get("derivatives")),
            "metrics": _compact_derivatives(derivatives),
        },
        "CROSS_ASSET": {
            "available": _available(frozen.get("cross_asset"))
                         or _available(evidence.get("correlation")),
            "context": _pick(
                frozen_cross or policy_cross,
                ("available", "source_ts", "status", "source", "systemic_coupling",
                 "network_tension", "fragmentation", "active_breaks_count",
                 "dominant_stress_node", "instrument_node")),
        },
        "REGIME": {
            "available": _available(frozen.get("macro"))
                         or _available(evidence.get("atr_regime")),
            "context": (_pick(
                frozen_regime,
                ("available", "regime", "x", "y", "z", "boundary_distance",
                 "transition_velocity", "transition_acceleration"))
                if frozen_regime else _compact_policy_regime(policy_regime)),
        },
        "PRICE": {
            "available": _available(frozen.get("price_volatility"))
                         or _available(evidence.get("live_price")),
            "context": _pick(
                frozen_price or policy_price,
                ("available", "ret_5m", "ret_15m", "realized_vol_15m",
                 "realized_vol_60m", "trend_efficiency_60", "range_60",
                 "drawdown_60", "drawup_60", "move_5m", "move_15m", "move_60m")),
        },
        "VOLATILITY": {
            "available": _available(evidence.get("iv_surface"))
                         or _available(evidence.get("atr_regime"))
                         or _available(frozen.get("price_volatility")),
            "context": {
                "iv_surface": _pick(evidence.get("iv_surface"),
                                    ("available", "atm_iv", "skew", "term_slope")),
                "atr_sigma_vrp": _compact_policy_regime(policy_regime),
                "frozen_t0": _pick(frozen_price, (
                    "realized_vol_15m", "realized_vol_60m", "rv15_over_rv60")),
            },
        },
    }
    readiness, horizons = _data_readiness(engine)
    # Dataset readiness is a ceiling. A family absent in this causal snapshot
    # is not advertised as context-ready merely because history exists.
    for family in families.values():
        family["data_maturity"] = readiness if family["available"] else "INSUFFICIENT_DATA"
        family["edge_maturity"] = "INSUFFICIENT_DATA"

    score = option_state.get("option_state_score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = None
    if score is not None and score >= 0.05:
        option_read = "IV/GEX/skew подтверждают удержание как causal context."
        stance = "SUPPORTS_HOLD"
    elif score is not None and score <= -0.05:
        option_read = "Опционный контекст против позиции."
        stance = "OPPOSES_POSITION"
    elif families["OPTIONS"]["available"]:
        option_read = "IV/GEX/skew дают смешанный контекст без самостоятельного сигнала."
        stance = "MIXED"
    else:
        option_read = "Опционный контекст сейчас недоступен."
        stance = "UNAVAILABLE"
    lines = [option_read]
    if readiness != "INSUFFICIENT_DATA":
        lines.append("Данных достаточно для контекста, но edge ещё не доказан.")
    else:
        lines.append("Накопленных causal T0 данных пока недостаточно даже для раннего data-ready статуса.")

    edge_status = "INSUFFICIENT_DATA"
    return {
        "contract_version": CONTRACT_VERSION,
        "asof": snapshot.get("captured_ts"),
        "families": families,
        "data_maturity": readiness,
        "edge_maturity": edge_status,
        "horizon_data_maturity": {
            str(row["horizon_minutes"]): row["data_maturity"] for row in horizons},
        "option_context_stance": stance,
        "context_lines_ru": lines,
        "confidence_modifier": 0.0,
        "authority": {
            "role": "EXPLANATION_AND_CONFIDENCE_CONTEXT",
            "production_directional_authority": False,
            "may_trigger_exit_or_close": False,
            "explicit_promotion_required": True,
            "auto_promotion": False,
        },
    }
