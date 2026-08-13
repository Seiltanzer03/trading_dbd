"""Bounded causal EDE context for AI explanation, never action authority."""
from __future__ import annotations

import json
import math
from typing import Any

from seiltanzer.g1_short_horizon_p2e_segmented_persistence import (
    ASSET_FAMILY_BY_INSTRUMENT,
    session_utc,
)

from .evidence_ledger import (
    FAMILIES,
    MATURITY_RANK,
    evidence_ledger_path,
    latest_frozen_evidence,
)
from .filters import FittedCondition, condition_matches
from .feature_view import feature_value
from .prospective import EXTRACTORS, _block_meta
from .registry import FEATURES


CONTRACT_VERSION = "g1s-ede-ai-causal-context-v1.2.2"
CONFIDENCE_CAP = {
    "INSUFFICIENT_DATA": 0.0,
    "RESEARCH_SIGNAL": 0.05,
    "PROVISIONAL_EDGE": 0.15,
    "ROBUST_EDGE": 0.25,
}


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
            "rolling_zscore", "direction_consistency", "sample_count")
    allowed = {
        "iv", "skew", "gex_force_score", "vanna", "charm_per_day",
        "gex_zero_gamma_log_moneyness",
    }
    return {name: _pick(row, keys) if isinstance(row, dict) else row
            for name, row in source.items() if name in allowed}


def _compact_policy_regime(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    return {
        "atr": _pick(source.get("atr"), ("phase", "ratio", "value")),
        "sigma": _pick(source.get("sigma"), ("phase", "ratio", "annual", "value")),
        "vrp": _pick(source.get("vrp"), ("iv_rv_ratio", "phase", "value")),
        "regime": _pick(source.get("regime"), ("regime", "phase", "confidence")),
    }


def _latest_frozen_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    instrument = str((snapshot.get("strategy") or {}).get("instrument") or "")
    cutoff = snapshot.get("captured_ts")
    if runtime is None or not instrument or cutoff is None:
        return {}
    try:
        cutoff = float(cutoff)
        with runtime._lock:
            row = runtime._conn.execute(
                "SELECT captured_ts,frozen_features_json FROM g1s_observations "
                "WHERE instrument=? AND captured_ts<=? "
                "ORDER BY captured_ts DESC LIMIT 1", (instrument, cutoff)).fetchone()
        if row is None:
            return {}
        captured = float(row["captured_ts"])
        frozen = json.loads(row["frozen_features_json"])
        block = frozen.get("g1s_evidence_v3") or {}
        if float(block.get("captured_ts", captured)) > captured + 1e-6:
            return {}
        result = dict(block)
        result["observation_t0"] = captured
        result["v2_option_context"] = (
            (frozen.get("g1s_evidence_v2") or {}).get("option_context") or {})
        result["_raw_frozen"] = frozen
        return result
    except Exception:
        return {}


def _record(*, instrument: str, t0: float, feature_id: str, value: Any,
            block: dict[str, Any], live_applicability: str = "LIVE_APPLICABLE") -> dict[str, Any]:
    asof, quality, stale_after = _block_meta(block, t0)
    definition = next(item for item in FEATURES if item.feature_id == feature_id)
    value_record = feature_value(
        instrument=instrument, t0=t0, horizon=0, feature_id=feature_id,
        value=value, asof=asof, quality=quality,
        stale_after_seconds=stale_after,
        historical_available=definition.historical_availability == "AVAILABLE",
        live_available=definition.live_availability != "UNAVAILABLE",
        training_eligible=definition.training_eligibility,
        dependency_group=definition.dependency_family).as_dict()
    quality_block = block.get("quality") if isinstance(block, dict) else None
    if isinstance(quality_block, dict) and quality_block.get("stale") is True:
        value_record["stale"] = True
        value_record["training_eligible"] = False
    value_record["live_applicability"] = live_applicability
    return value_record


def canonical_current_feature_map(
        frozen: dict[str, Any], instrument: str) -> dict[str, dict[str, Any]]:
    """Return exact registry IDs with explicit live applicability and freshness."""
    raw = frozen.get("_raw_frozen") or {}
    family = ASSET_FAMILY_BY_INSTRUMENT.get(instrument, "UNKNOWN")
    try:
        t0 = float(frozen["observation_t0"])
    except (KeyError, TypeError, ValueError):
        t0 = 0.0
    definitions = [item for item in FEATURES
                   if item.research_scope == "G1S" and item.training_eligibility]
    values: dict[str, dict[str, Any]] = {
        definition.feature_id: {
            "feature_id": definition.feature_id, "value": None,
            "availability": "UNAVAILABLE", "available": False,
            "asof": None, "stale": False,
            "live_applicability": "NOT_LIVE_APPLICABLE",
        } for definition in definitions}
    for feature_id, extractor in EXTRACTORS.items():
        try:
            value, block = extractor(raw)
            record = _record(
                instrument=instrument, t0=t0, feature_id=feature_id,
                value=value, block=block, live_applicability="LIVE_APPLICABLE")
            record["available"] = record["availability"] == "AVAILABLE"
            values[feature_id] = record
        except Exception:
            continue

    identity_block = {"captured_ts": t0, "quality": {
        "source_ts": t0, "source_quality": 1.0, "stale": False}}
    for feature_id, value in (
        ("regime.asset", instrument),
        ("regime.asset_family", family),
        ("regime.session_utc", session_utc(t0)),
    ):
        record = _record(
            instrument=instrument, t0=t0, feature_id=feature_id,
            value=value, block=identity_block)
        record["available"] = record["availability"] == "AVAILABLE"
        values[feature_id] = record

    rv15, rv60 = values.get("vol.rv_15m") or {}, values.get("vol.rv_60m") or {}
    if rv15.get("available") and rv60.get("available") and float(rv60["value"]) > 0:
        asof = max(float(rv15["asof"]), float(rv60["asof"]))
        block = {"captured_ts": asof, "quality": {
            "source_ts": asof,
            "source_quality": min(
                float(rv15.get("quality") or 1.0), float(rv60.get("quality") or 1.0)),
            "stale": bool(rv15.get("stale") or rv60.get("stale")),
        }}
        record = _record(
            instrument=instrument, t0=t0, feature_id="vol.rv15_over_rv60",
            value=float(rv15["value"])/float(rv60["value"]), block=block)
        record["available"] = record["availability"] == "AVAILABLE"
        values["vol.rv15_over_rv60"] = record

    dynamics = ((raw.get("g1s_evidence_v3") or {}).get("option_dynamics") or {})
    derivative_rows = dynamics.get("derivatives") or {}
    metric_names = {
        "iv": "iv", "skew": "skew", "gex": "gex_force_score",
        "vanna": "vanna", "charm": "charm_per_day",
        "zero_gamma": "gex_zero_gamma_log_moneyness",
    }
    for metric, source_name in metric_names.items():
        source = derivative_rows.get(source_name) or {}
        for transform in (
                "acceleration", "rolling_rank", "rolling_zscore",
                "direction_consistency"):
            feature_id = f"option_dynamics.{metric}_{transform}"
            if feature_id not in values:
                continue
            record = _record(
                instrument=instrument, t0=t0, feature_id=feature_id,
                value=source.get(transform) if source.get("available") else None,
                block=dynamics, live_applicability="LIVE_APPLICABLE")
            record["available"] = record["availability"] == "AVAILABLE"
            values[feature_id] = record
    return values


def _condition_matches_values(values: dict[str, dict[str, Any]],
                              condition: dict[str, Any]) -> bool:
    try:
        fitted = FittedCondition(
            feature_id=str(condition["feature_id"]),
            kind=str(condition["kind"]), state=str(condition["state"]),
            lower=condition.get("lower"), upper=condition.get("upper"),
            train_cutoff_ts=condition.get("train_cutoff_ts"),
        )
    except (KeyError, TypeError, ValueError):
        return False
    scalar = {feature_id: row.get("value") for feature_id, row in values.items()
              if row.get("available")}
    return condition_matches({"ede_features": scalar}, fitted)


def _candidate_for_context(record: dict[str, Any] | None,
                           values: dict[str, dict[str, Any]], *, snapshot_ts: float,
                           observation_t0: float | None,
                           max_age_sec: float) -> tuple[dict[str, Any] | None, bool, str]:
    def eligible(candidate: dict[str, Any]) -> bool:
        if candidate.get("aggregate_scope") != "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY":
            return False
        primary_folds = int(candidate.get("primary_folds") or 0)
        maturity = candidate.get("edge_maturity")
        if maturity == "PROVISIONAL_EDGE" and primary_folds < 2:
            return False
        if maturity == "ROBUST_EDGE" and primary_folds < 4:
            return False
        return primary_folds > 0

    candidates = (record or {}).get("edge_candidates") or []
    for candidate in candidates:
        if not eligible(candidate):
            continue
        refit = candidate.get("deployment_refit") or {}
        conditions = refit.get("deployment_rule") or []
        if not conditions:
            return candidate, False, "DEPLOYMENT_RULE_MISSING"
        required = [str(row.get("feature_id")) for row in conditions]
        fresh = observation_t0 is not None and observation_t0 <= snapshot_ts + 1e-6
        fresh = fresh and 0.0 <= snapshot_ts-observation_t0 <= max_age_sec
        for feature_id in required:
            feature = values.get(feature_id)
            if (not feature or feature.get("live_applicability") != "LIVE_APPLICABLE"
                    or not feature.get("available") or feature.get("stale")):
                fresh = False
                break
            try:
                asof = float(feature["asof"])
            except (KeyError, TypeError, ValueError):
                fresh = False
                break
            if (asof > observation_t0 + 1e-6 or asof > snapshot_ts + 1e-6
                    or snapshot_ts-asof > max_age_sec):
                fresh = False
                break
        if not fresh:
            return candidate, False, "STALE_OR_UNAVAILABLE_CONTEXT"
        if all(_condition_matches_values(values, row) for row in conditions):
            return candidate, True, "MATCHED_FRESH_DEPLOYMENT_RULE"
        return candidate, False, "CONDITIONS_NOT_MATCHED"
    primary = [candidate for candidate in candidates if eligible(candidate)]
    return ((primary[0], False, "DEPLOYMENT_RULE_MISSING")
            if primary else (None, False, "NO_PRIMARY_EDGE_EVIDENCE"))


def _position_relation(snapshot: dict[str, Any], values: dict[str, dict[str, Any]],
                       candidate: dict[str, Any] | None, applies: bool) -> str:
    if not candidate or not applies:
        return "NOT_APPLICABLE"
    if candidate.get("directional_evidence") != "SUPPORTS_PERSISTENCE":
        return "MIXED"
    direction = str((snapshot.get("strategy") or {}).get("direction") or "").lower()
    try:
        ret5 = float(values["price.ret_5m"]["value"])
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"
    supports = (direction in {"long", "buy"} and ret5 > 0) or (
        direction in {"short", "sell"} and ret5 < 0)
    if direction not in {"long", "buy", "short", "sell"} or ret5 == 0:
        return "UNKNOWN"
    return "SUPPORTS_POSITION" if supports else "OPPOSES_POSITION"


def _family_evidence(record: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    ledger_families = (record or {}).get("family_data_maturity") or {}
    output: dict[str, Any] = {}
    for name in FAMILIES:
        source = ledger_families.get(name) or {}
        output[name] = {
            "data_maturity": source.get("data_maturity", "INSUFFICIENT_DATA"),
            "horizons": source.get("horizons") or {},
        }
    summary = max(
        (row["data_maturity"] for row in output.values()),
        key=lambda name: MATURITY_RANK.get(name, 0), default="INSUFFICIENT_DATA")
    return summary, output


def build_ai_ede_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach frozen context after the management action has been calculated."""
    manager = snapshot.get("policy_manager") or {}
    evidence = manager.get("evidence") or {}
    option_state = (manager.get("option_derivative_state")
                    or evidence.get("option_derivative_state") or {})
    metrics = option_state.get("metrics") or {}
    frozen = _latest_frozen_context(engine, snapshot)
    instrument = str((snapshot.get("strategy") or {}).get("instrument") or "")
    values = canonical_current_feature_map(frozen, instrument)
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

    try:
        cutoff = float(snapshot.get("captured_ts"))
        frozen_evidence = latest_frozen_evidence(evidence_ledger_path(engine), cutoff)
    except (TypeError, ValueError):
        frozen_evidence = None
    readiness, family_evidence = _family_evidence(frozen_evidence)
    current = {
        "OPTIONS": {
            "available": bool(option_state.get("available") or metrics
                              or frozen_options.get("available")),
            "metrics": {name: _pick(row, (
                "value", "slope", "acceleration", "sample_count", "confidence"))
                for name, row in metrics.items()
                if name in {"iv", "skew", "term_slope", "gex_force", "gex_stiffness",
                            "distance_to_zero_gamma"} and isinstance(row, dict)},
            "t0": {
                "iv": frozen_options.get("iv"),
                "iv_rv_ratio": frozen_options.get("iv_rv_ratio"),
                "skew": frozen_options.get("skew"),
                "term_slope": frozen_options.get("term_slope"),
                "delta": frozen_options.get("delta"), "vanna": frozen_options.get("vanna"),
                "charm": frozen_options.get("charm_per_day"),
                "gex": frozen_v2_options.get("gex_net_balance"),
                "gex_force": frozen_gex.get("force_score"),
                "zero_gamma": frozen_gex.get("zero_gamma_log_moneyness"),
                "asof": frozen.get("observation_t0"),
            },
        },
        "OPTION_DYNAMICS": {
            "available": bool(derivatives),
            "metrics": _compact_derivatives(derivatives),
        },
        "CROSS_ASSET": {
            "available": _available(frozen_cross) or _available(policy_cross),
            "context": _pick(frozen_cross or policy_cross, (
                "available", "source_ts", "status", "systemic_coupling",
                "network_tension", "fragmentation", "active_breaks_count")),
        },
        "REGIME": {
            "available": _available(frozen_regime) or _available(policy_regime),
            "context": (_pick(frozen_regime, (
                "available", "regime", "boundary_distance", "transition_velocity",
                "transition_acceleration")) if frozen_regime
                else _compact_policy_regime(policy_regime)),
        },
        "PRICE": {
            "available": _available(frozen_price) or _available(policy_price),
            "context": _pick(frozen_price or policy_price, (
                "available", "ret_5m", "ret_15m", "realized_vol_15m",
                "realized_vol_60m", "trend_efficiency_60", "range_60m",
                "drawdown_60m", "drawup_60m")),
        },
        "VOLATILITY": {
            "available": _available(evidence.get("iv_surface"))
                         or _available(policy_regime) or _available(frozen_price),
            "context": {
                "iv_surface": _pick(evidence.get("iv_surface"),
                                    ("available", "atm_iv", "skew", "term_slope")),
                "atr_sigma_vrp": _compact_policy_regime(policy_regime),
                "t0": _pick(frozen_price, (
                    "realized_vol_15m", "realized_vol_60m", "rv15_over_rv60")),
            },
        },
    }
    for name, family in current.items():
        family.update(family_evidence[name])
        family["edge_maturity"] = "INSUFFICIENT_DATA"

    try:
        snapshot_ts = float(snapshot.get("captured_ts"))
        observation_t0 = float(frozen["observation_t0"])
        max_age_sec = float(engine.settings.ede_context_max_age_sec)
        if not math.isfinite(max_age_sec) or max_age_sec <= 0:
            raise ValueError("invalid EDE max age")
    except (AttributeError, KeyError, TypeError, ValueError):
        snapshot_ts = float(snapshot.get("captured_ts") or 0.0)
        observation_t0 = None
        max_age_sec = 0.0
    candidate, applies, applicability_reason = _candidate_for_context(
        frozen_evidence, values, snapshot_ts=snapshot_ts,
        observation_t0=observation_t0, max_age_sec=max_age_sec)
    edge_status = str((candidate or {}).get("edge_maturity") or "INSUFFICIENT_DATA")
    relation = _position_relation(snapshot, values, candidate, applies)
    cap = CONFIDENCE_CAP.get(edge_status, 0.0)
    modifier = cap if relation == "SUPPORTS_POSITION" else -cap if relation == "OPPOSES_POSITION" else 0.0
    for family in (candidate or {}).get("feature_families") or []:
        if family in current:
            current[family]["edge_maturity"] = edge_status

    score = option_state.get("option_state_score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = None
    if score is not None and score >= 0.05:
        lines = ["IV/GEX/skew подтверждают удержание как causal context."]
        stance = "SUPPORTS_HOLD"
    elif score is not None and score <= -0.05:
        lines = ["Опционный контекст против позиции."]
        stance = "OPPOSES_POSITION"
    elif current["OPTIONS"]["available"]:
        lines = ["IV/GEX/skew дают смешанный контекст без самостоятельного сигнала."]
        stance = "MIXED"
    else:
        lines = ["Опционный контекст сейчас недоступен."]
        stance = "UNAVAILABLE"
    lines.append(
        "Conditional edge подтверждён замороженным primary evidence."
        if edge_status != "INSUFFICIENT_DATA"
        else "Данных может быть достаточно для контекста, но edge ещё не доказан.")

    edge = None
    if candidate:
        edge = {key: candidate.get(key) for key in (
            "candidate_id", "hypothesis_id", "horizon_minutes",
            "validation_conditions", "deployment_refit",
            "edge_maturity", "delta_brier", "delta_logloss", "q_value",
            "primary_folds", "folds_evaluated", "folds_positive",
            "directional_evidence", "feature_families", "aggregate_scope")}
        required_ids = list(((candidate.get("deployment_refit") or {}).get(
            "feature_ids") or []))
        edge.update({
            "applies_to_current_context": applies,
            "applicability_reason": applicability_reason,
            "position_relation": relation,
            "current_required_features": {
                feature_id: values.get(feature_id) for feature_id in required_ids},
        })
    parity = {
        "candidate_eligible_registry_features": len(values),
        "exact_id_records": len(values),
        "not_live_applicable": sorted(
            feature_id for feature_id, row in values.items()
            if row.get("live_applicability") == "NOT_LIVE_APPLICABLE"),
        "silent_missing_features": [],
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "asof": snapshot.get("captured_ts"),
        "evidence_frozen_at": (frozen_evidence or {}).get("frozen_at"),
        "evidence_cutoff_ts": (frozen_evidence or {}).get("evidence_cutoff_ts"),
        "dataset_sha256": (frozen_evidence or {}).get("dataset_sha256"),
        "families": current,
        "data_maturity": readiness,
        "edge_maturity": edge_status,
        "edge": edge,
        "current_observation_t0": observation_t0,
        "configured_max_age_sec": max_age_sec,
        "canonical_feature_parity": parity,
        "option_context_stance": stance,
        "context_lines_ru": lines,
        "confidence_modifier": round(modifier, 6),
        "confidence_modifier_cap": cap,
        "authority": {
            "role": "EXPLANATION_AND_CONFIDENCE_CONTEXT",
            "production_authority": False,
            "production_directional_authority": False,
            "may_trigger_exit_or_close": False,
            "explicit_promotion_required": True,
            "auto_promotion": False,
        },
    }
