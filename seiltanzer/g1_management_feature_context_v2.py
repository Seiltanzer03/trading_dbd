"""Future-only broad T0 context for G.1-M / G.1-M.1 research.

The authoritative decision snapshot already contains the state used by the AI at
T0.  This layer does not recompute that state and does not change the action.  It
materializes a compact, immutable research context so later management-edge
analysis can ask which *already-known-at-T0* families were useful.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

from . import storage_runtime as _storage
from .g1_management_runtime import ManagementEdgeRuntime


MANAGEMENT_CONTEXT_V2 = "g1m-management-context-v2"
MANAGEMENT_CONTEXT_ACTIVATION = "g1m-management-context-v2-activation-v1"
MANAGEMENT_FAMILY_MANIFEST = "g1m-feature-family-manifest-v1"

MANAGEMENT_FEATURE_FAMILIES = {
    "MANAGEMENT_BASE": ("position_geometry", "policy_state"),
    "OPTION_BARRIER": ("position_geometry", "option_barrier"),
    "OPTION_DERIVATIVES": ("position_geometry", "option_derivatives"),
    "GEX": ("position_geometry", "gex"),
    "CROSS_MACRO": ("position_geometry", "market_context"),
    "INTERACTIONS": ("position_geometry", "interactions"),
    "FULL_MANAGEMENT_CONTEXT": (
        "position_geometry", "option_barrier", "option_derivatives", "gex",
        "market_context", "interactions", "policy_state",
    ),
}

CRITICAL_TABLES = (
    "g1m_feature_context_v2_activation",
    "g1m_t0_feature_context_v2",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else default
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _at(value: Any, *path: str, default=None):
    current = value
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _metric(state: dict, name: str) -> dict:
    row = (state.get("metrics") or {}).get(name)
    if not isinstance(row, dict):
        return {"available": False, "reason": "not_frozen_in_decision_snapshot"}
    keys = (
        "value", "slope", "acceleration", "noise", "sample_count",
        "normalization_noise", "numerical_effect_floor",
        "normalization_horizon_minutes", "time_span_minutes", "confidence",
        "source_quality", "available", "units", "value_units", "slope_units",
        "acceleration_units", "reason",
    )
    return {key: row.get(key) for key in keys if key in row}


def _position_geometry(snapshot: dict, observation_row: Any, context_row: Any,
                       decision_row: Any) -> dict:
    position = snapshot.get("position_state") or {}
    observation = snapshot.get("observation") or {}
    manager = snapshot.get("policy_manager") or {}
    inputs = manager.get("inputs") or {}
    current_r = (_finite(_at(observation, "position", "r"))
                 or _finite(inputs.get("r0")))
    take_r = _finite(inputs.get("T"))
    entry = _finite(decision_row["entry"]) if decision_row is not None else _finite(position.get("entry"))
    original_stop = (_finite(decision_row["original_stop"])
                     if decision_row is not None else _finite(position.get("original_stop")))
    active_stop = _finite(position.get("active_stop_price"))
    take_price = _finite(decision_row["take_price"]) if decision_row is not None else None
    current_price = _finite(_at(observation, "exact_levels", "current"))
    return {
        "available": current_r is not None,
        "current_r": current_r,
        "remaining_position_fraction": _finite(position.get("remaining_position_fraction")),
        "realized_r_weighted": _finite(position.get("realized_r_weighted")),
        "entry": entry,
        "original_stop": original_stop,
        "active_stop": active_stop,
        "take_price": take_price,
        "current_price": current_price,
        "distance_to_original_stop_r": (current_r + 1.0 if current_r is not None else None),
        "distance_to_take_r": (take_r - current_r
                               if current_r is not None and take_r is not None else None),
        "take_r": take_r,
        "instrument": context_row["instrument"] if context_row is not None else None,
        "direction": context_row["direction"] if context_row is not None else None,
        "setup": context_row["setup"] if context_row is not None else None,
        "trade_id": int(observation_row["trade_id"]),
        "captured_ts": float(observation_row["captured_ts"]),
        "geometry_note": "R distances use original-risk normalization; active stop remains an exact price",
    }


def _option_state(snapshot: dict) -> dict:
    manager = snapshot.get("policy_manager") or {}
    state = manager.get("option_derivative_state") or snapshot.get("option_derivative_state") or {}
    return state if isinstance(state, dict) else {}


def _option_barrier(state: dict) -> dict:
    names = (
        "p_take", "p_stop", "p_no_touch", "barrier_ev", "bop",
        "q10", "q50", "q90", "width", "tail_log_ratio",
        "h_take", "h_stop", "hazard_log_ratio",
    )
    metrics = {name: _metric(state, name) for name in names}
    return {
        "available": any(row.get("available") for row in metrics.values()),
        "metrics": metrics,
        "first_touch_hazard": state.get("first_touch_hazard") or {},
        "family": "option_distribution",
        "independent_vote": False,
        "authority": "frozen_t0_context",
    }


def _option_derivatives(state: dict) -> dict:
    metrics = {}
    for name, row in (state.get("metrics") or {}).items():
        if not isinstance(row, dict):
            continue
        if any(row.get(key) is not None for key in ("slope", "acceleration")):
            metrics[str(name)] = _metric(state, str(name))
    return {
        "available": bool(metrics),
        "metrics": metrics,
        "named_derivatives": state.get("named_derivatives") or {},
        "option_state_score": state.get("option_state_score"),
        "option_state_confidence": state.get("option_state_confidence"),
        "option_state_attribution": state.get("option_state_attribution") or {},
        "redundancy_contract": state.get("option_state_redundancy_contract") or {},
        "family": "option_distribution",
        "independent_vote": False,
        "authority": "frozen_t0_shadow_context",
    }


def _gex(state: dict) -> dict:
    geometry = state.get("gex_geometry") or {}
    metrics = {
        name: _metric(state, name)
        for name in (
            "gex_field", "gex_force", "gex_stiffness",
            "distance_to_zero_gamma", "distance_to_call_wall", "distance_to_put_wall",
        )
    }
    return {
        "available": bool(geometry) or any(row.get("available") for row in metrics.values()),
        "geometry": geometry,
        "metrics": metrics,
        "family": "option_distribution",
        "independent_vote": False,
        "dealer_inventory_claim": False,
        "authority": "frozen_t0_context",
    }


def _market_context(snapshot: dict) -> dict:
    observation = snapshot.get("observation") or {}
    manager = snapshot.get("policy_manager") or {}
    evidence = manager.get("evidence") or {}

    def first_dict(*candidates):
        for value in candidates:
            if isinstance(value, dict) and value:
                return value
        return {}

    cross = first_dict(
        observation.get("cross_asset"), evidence.get("cross_asset"),
        manager.get("cross_asset"))
    macro = first_dict(
        observation.get("macro_regime"), observation.get("regime"),
        evidence.get("macro_regime"), manager.get("regime"))
    wavelet = first_dict(
        observation.get("wavelet"), evidence.get("wavelet"), manager.get("wavelet"))
    return {
        "available": bool(cross or macro or wavelet),
        "cross_asset": cross,
        "macro": macro,
        "wavelet": wavelet,
        "family_authority": "context_only",
        "independent_vote": False,
        "missing_means_not_frozen_not_zero": True,
    }


def _interactions(snapshot: dict, state: dict) -> dict:
    manager = snapshot.get("policy_manager") or {}
    direct = state.get("interactions") or manager.get("option_interactions") or {}
    ensemble = manager.get("derived_scenario_ensemble") or {}
    drivers = ensemble.get("drivers") or {}
    if direct:
        source = "frozen_option_derivative_state"
        value = direct
    elif drivers:
        # The v15 compact snapshot may intentionally omit the full interaction
        # workspace. Preserve only the already-frozen ensemble drivers; do not
        # reconstruct interactions after T0.
        source = "frozen_derived_scenario_drivers"
        value = drivers
    else:
        source = "not_frozen_in_decision_snapshot"
        value = {}
    return {
        "available": bool(value),
        "source": source,
        "value": value,
        "recomputed_after_t0": False,
        "family": "option_distribution",
        "independent_vote": False,
        "authority": "frozen_t0_context",
    }


def _policy_state(snapshot: dict, observation_row: Any) -> dict:
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    ensemble = manager.get("derived_scenario_ensemble") or {}
    shadow = manager.get("shadow_policy_contract") or {}
    scenarios = []
    for row in ensemble.get("scenarios") or []:
        if not isinstance(row, dict):
            continue
        scenarios.append({
            key: row.get(key) for key in (
                "name", "weight", "material", "driver_confidence", "source_quality",
            ) if key in row
        })
    return {
        "production_policy": str(observation_row["production_policy"]),
        "shadow_candidate_policy": (
            shadow.get("new_candidate_policy") or ensemble.get("candidate_policy")),
        "shadow_old_policy": ensemble.get("old_policy"),
        "shadow_promotion_allowed": bool(ensemble.get("promotion_allowed", False)),
        "scenario_version": ensemble.get("version"),
        "scenario_drivers": ensemble.get("drivers") or {},
        "scenarios": scenarios,
        "state_change_attribution": manager.get("state_change_attribution") or {},
        "entry_avg_prev_now": (
            (manager.get("state_change_attribution") or {}).get("snapshots") or {}),
        "management_decision_id": decision.get("decision_id"),
        "production_action_changed_by_context_v2": False,
        "production_authority": False,
    }


def build_management_context_v2(snapshot: dict, observation_row: Any,
                                context_row: Any, decision_row: Any) -> dict:
    state = _option_state(snapshot)
    families = {
        name: {
            "members": list(members),
            "training_enabled": False,
            "auto_fit": False,
            "ablation_required": True,
            "independent_vote": False,
        }
        for name, members in MANAGEMENT_FEATURE_FAMILIES.items()
    }
    return {
        "contract_version": MANAGEMENT_CONTEXT_V2,
        "family_manifest_version": MANAGEMENT_FAMILY_MANIFEST,
        "captured_ts": float(observation_row["captured_ts"]),
        "observation_id": str(observation_row["observation_id"]),
        "review_id": str(observation_row["review_id"]),
        "position_geometry": _position_geometry(
            snapshot, observation_row, context_row, decision_row),
        "option_barrier": _option_barrier(state),
        "option_derivatives": _option_derivatives(state),
        "gex": _gex(state),
        "market_context": _market_context(snapshot),
        "interactions": _interactions(snapshot, state),
        "policy_state": _policy_state(snapshot, observation_row),
        "feature_families": families,
        "semantics": {
            "source_is_immutable_decision_snapshot": True,
            "recomputed_after_t0": False,
            "future_captures_only": True,
            "no_historical_retrofit": True,
            "collect_wide_train_controlled": True,
            "ablation_required_before_training_authority": True,
            "production_action_unchanged": True,
            "production_authority": False,
        },
        "missing_is_not_zero": True,
    }


def install_g1_management_feature_context_v2() -> None:
    if getattr(ManagementEdgeRuntime, "_management_context_v2", None) == MANAGEMENT_CONTEXT_V2:
        return
    previous_ensure = ManagementEdgeRuntime._ensure_tables
    previous_capture = ManagementEdgeRuntime._capture_observation
    previous_status = ManagementEdgeRuntime.status

    def ensure_tables(self):
        previous_ensure(self)
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_feature_context_v2_activation(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    activation_ts REAL NOT NULL,
                    contract_version TEXT NOT NULL)""")
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_feature_context_v2_activation(id,activation_ts,contract_version) "
                "VALUES(1,?,?)", (time.time(), MANAGEMENT_CONTEXT_ACTIVATION))
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_t0_feature_context_v2(
                    observation_id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL UNIQUE,
                    captured_ts REAL NOT NULL,
                    source_snapshot_sha256 TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    context_sha256 TEXT NOT NULL,
                    created_ts REAL NOT NULL)""")
            for table in CRITICAL_TABLES:
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1M context v2 row'); END""")
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1M context v2 row'); END""")

    def capture_observation(self, source):
        inserted = previous_capture(self, source)
        if not inserted:
            return False
        review_id = str(source["review_id"])
        captured_ts = float(source["captured_ts"])
        with self._lock:
            activation = self._conn.execute(
                "SELECT activation_ts FROM g1m_feature_context_v2_activation WHERE id=1").fetchone()
            obs = self._conn.execute(
                "SELECT * FROM g1m_management_observations WHERE review_id=?", (review_id,)).fetchone()
            context = (self._conn.execute(
                "SELECT * FROM g1m_observation_context WHERE observation_id=?",
                (obs["observation_id"],)).fetchone() if obs is not None else None)
            decision = self._conn.execute(
                "SELECT * FROM management_decisions WHERE review_id=? "
                "ORDER BY created_ts DESC,rowid DESC LIMIT 1", (review_id,)).fetchone()
        if obs is None or activation is None:
            return True
        # Never retrofit an older decision just because a worker first sees it
        # after this code is deployed.
        if captured_ts < float(activation["activation_ts"]) - 1e-9:
            return True
        raw_snapshot = str(source["snapshot_json"])
        snapshot = _loads(raw_snapshot, {})
        if not isinstance(snapshot, dict):
            return True
        payload = build_management_context_v2(snapshot, obs, context, decision)
        raw = _json(payload)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_t0_feature_context_v2("
                "observation_id,review_id,captured_ts,source_snapshot_sha256,"
                "context_json,context_sha256,created_ts) VALUES(?,?,?,?,?,?,?)",
                (obs["observation_id"], review_id, captured_ts,
                 str(source["snapshot_sha256"]), raw, digest, time.time()))
        return True

    def status(self):
        body = previous_status(self)
        with self._lock:
            activation = self._conn.execute(
                "SELECT activation_ts FROM g1m_feature_context_v2_activation WHERE id=1").fetchone()
            count = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1m_t0_feature_context_v2").fetchone()[0])
        body["management_context_v2"] = {
            "contract_version": MANAGEMENT_CONTEXT_V2,
            "activation_ts": float(activation["activation_ts"]) if activation else None,
            "future_captures_only": True,
            "observations": count,
            "feature_families": list(MANAGEMENT_FEATURE_FAMILIES),
            "training_enabled": False,
            "ablation_required_before_training": True,
            "production_authority": False,
        }
        return body

    ManagementEdgeRuntime._ensure_tables = ensure_tables
    ManagementEdgeRuntime._capture_observation = capture_observation
    ManagementEdgeRuntime.status = status
    ManagementEdgeRuntime._management_context_v2 = MANAGEMENT_CONTEXT_V2
    _storage.CRITICAL_TABLES = tuple(dict.fromkeys((*_storage.CRITICAL_TABLES, *CRITICAL_TABLES)))
