"""P0 operational integrity for passive G.1S evidence collection.

This layer is intentionally additive and installed after the H2/V3 wrappers.
Optional research feature failures must never cancel the immutable core T0 price
observation.  Legacy V2 Wavelet fields are *not* fabricated from the current
Wavelet-v3 contract: the native result is preserved separately while the old
feature slots remain explicitly unavailable.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any, Callable

from . import g1_broad_market_evidence_v3 as _v3
from . import g1_short_horizon_feature_contract_v2 as _v2
from . import passive_learning as _pl
from .passive_learning import PassiveLearningEngine


OPERATIONAL_INTEGRITY_VERSION = "g1-operational-integrity-p0-v1"
HEALTH_STATE_KEY = "g1_operational_integrity_v1"


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"


def _default_health() -> dict[str, Any]:
    return {
        "version": OPERATIONAL_INTEGRITY_VERSION,
        "last_step_ts": None,
        "last_successful_eligible_capture_ts": None,
        "last_error_ts": None,
        "last_error": None,
        "errors_by_feature_family": {},
        "consecutive_failed_capture_cycles": 0,
    }


def _load_health(engine: PassiveLearningEngine) -> dict[str, Any]:
    try:
        with engine._lock:
            row = engine._conn.execute(
                "SELECT value_json FROM passive_collector_state WHERE key=?",
                (HEALTH_STATE_KEY,),
            ).fetchone()
        if row:
            value = json.loads(row[0])
            if isinstance(value, dict):
                return {**_default_health(), **value}
    except Exception:  # pragma: no cover - status must survive damaged telemetry
        pass
    return _default_health()


def _save_health(engine: PassiveLearningEngine, health: dict[str, Any]) -> None:
    try:
        raw = json.dumps(health, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
        with engine._lock, engine._conn:
            engine._conn.execute(
                "INSERT INTO passive_collector_state(key,value_json,updated_ts) "
                "VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_ts=excluded.updated_ts",
                (HEALTH_STATE_KEY, raw, time.time()),
            )
    except Exception:
        # Telemetry must never become a new reason to lose the market observation.
        return


def _record_feature_error(engine: PassiveLearningEngine, family: str,
                          exc: BaseException) -> None:
    health = _load_health(engine)
    now = time.time()
    text = _error_text(exc)
    families = dict(health.get("errors_by_feature_family") or {})
    row = dict(families.get(family) or {})
    row.update({
        "count": int(row.get("count") or 0) + 1,
        "last_error_ts": now,
        "last_error": text,
    })
    families[family] = row
    health.update({
        "last_error_ts": now,
        "last_error": f"{family}: {text}",
        "errors_by_feature_family": families,
    })
    _save_health(engine, health)


def _family_error_block(family: str, exc: BaseException, *,
                        contract_version: str) -> dict[str, Any]:
    return {
        "available": False,
        "family": family,
        "reason": "optional_feature_error",
        "error": _error_text(exc),
        "contract_version": contract_version,
        "source_status": "error_isolated",
        "production_authority": False,
    }


def _call_optional(engine: PassiveLearningEngine, family: str,
                   contract_version: str, fn: Callable[[], Any],
                   fallback: Callable[[BaseException], Any] | None = None) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - isolation is the contract
        _record_feature_error(engine, family, exc)
        if fallback is not None:
            return fallback(exc)
        return _family_error_block(family, exc, contract_version=contract_version)


def _legacy_wavelet_compat(rows: list[dict[str, Any]], captured_ts: float) -> dict[str, Any]:
    """Use Wavelet-v3 natively without inventing removed V2 feature semantics."""
    buckets: dict[int, dict[str, float]] = {}
    for row in rows:
        ts = _v2._finite(row.get("bar_end_ts"))
        price = _v2._finite(row.get("close"))
        if ts is None or price is None or price <= 0 or ts > captured_ts + 1e-6:
            continue
        key = int(ts // 300)
        if key not in buckets or ts > buckets[key]["ts"]:
            buckets[key] = {"ts": float(ts), "price": float(price)}
    points = [buckets[key] for key in sorted(buckets)][-288:]
    if len(points) < 36:
        return {
            "available": False,
            "legacy_semantics_available": False,
            "native_available": False,
            "reason": "insufficient_pre_t0_5m_points_for_wavelet_v3",
            "source_point_count": len(points),
            "low_pct": None, "high_pct": None, "resonance": None,
            "energy_low": None, "energy_high": None, "regime": None,
            "contract_version": "g1s-v2-wavelet-compat-v1",
        }
    result = _v3.compute_wavelet_analysis(
        points, sampling_minutes=5.0,
        source_meta={"source": "passive_5m_points_pre_t0",
                     "compatibility_role": "native_wavelet_v3_only"},
    )
    summary = result.get("summary") if isinstance(result, dict) else None
    native_available = bool(isinstance(result, dict) and result.get("available"))
    return {
        # Deliberately false: V2 model slots low_pct/high_pct/resonance belonged to
        # an older, non-equivalent contract and must not receive made-up mappings.
        "available": False,
        "legacy_semantics_available": False,
        "native_available": native_available,
        "reason": (
            "legacy_v2_wavelet_fields_not_semantically_equivalent_to_wavelet_v3"
            if native_available else "wavelet_v3_unavailable"
        ),
        "source_point_count": len(points),
        "low_pct": None, "high_pct": None, "resonance": None,
        "energy_low": None, "energy_high": None, "regime": None,
        "native_wavelet_v3": {
            "version": result.get("version") if isinstance(result, dict) else None,
            "available": native_available,
            "summary": summary if isinstance(summary, dict) else {},
        },
        "contract_version": "g1s-v2-wavelet-compat-v1",
    }


def _safe_build_v2(engine: PassiveLearningEngine, instrument: str,
                   captured_ts: float, market_price: float,
                   features: dict[str, Any]) -> dict[str, Any]:
    try:
        with engine._lock:
            rows = [dict(row) for row in engine._conn.execute(
                "SELECT bar_start_ts,bar_end_ts,close,high,low,source,quality,kind "
                "FROM passive_market_bars WHERE instrument=? AND bar_end_ts<=? "
                "AND bar_end_ts>=? ORDER BY bar_end_ts",
                (instrument, captured_ts + 1e-6, captured_ts - 6 * 3600.0),
            ).fetchall()]
    except Exception as exc:  # noqa: BLE001
        _record_feature_error(engine, "v2_market_bars", exc)
        rows = []

    intraday = _call_optional(
        engine, "v2_intraday", _v2.FEATURE_CONTRACT_V2,
        lambda: _v2._window_stats(rows, captured_ts),
    )
    wavelet = _call_optional(
        engine, "v2_wavelet", _v2.FEATURE_CONTRACT_V2,
        lambda: _legacy_wavelet_compat(rows, captured_ts),
    )
    option_snapshot = _call_optional(
        engine, "v2_option_snapshot", _v2.FEATURE_CONTRACT_V2,
        lambda: _v2._latest_option_snapshot(engine, features, captured_ts),
    )
    vol = features.get("volatility") or {}
    annual_vol = (_v2._finite(vol.get("reference_volatility_annual"))
                  or _v2._finite(vol.get("reference_annual")))
    option = _call_optional(
        engine, "v2_option_scalars", _v2.FEATURE_CONTRACT_V2,
        lambda: _v2._option_scalars(option_snapshot, annual_vol, market_price),
    )
    cross = _call_optional(
        engine, "v2_cross_asset", _v2.FEATURE_CONTRACT_V2,
        lambda: _v2._cross_asset(features, captured_ts, instrument),
    )
    return {
        "contract_version": _v2.FEATURE_CONTRACT_V2,
        "operational_integrity_version": OPERATIONAL_INTEGRITY_VERSION,
        "captured_ts": float(captured_ts),
        "instrument": instrument,
        "intraday": intraday,
        "wavelet": wavelet,
        "option_context": option,
        "cross_asset": cross,
        "semantics": {
            "continuous_market_outcomes_are_physical_observed_returns": True,
            "option_inputs_are_risk_neutral_context_only": True,
            "option_inputs_are_not_physical_probability": True,
            "dealer_inventory_assumption": False,
            "no_future_feature_backfill": True,
            "optional_feature_failure_does_not_cancel_core_t0": True,
            "legacy_wavelet_fields_are_not_fabricated": True,
        },
        "missing_is_not_zero": True,
    }


def _safe_build_v3(engine: PassiveLearningEngine, instrument: str,
                   captured_ts: float, market_price: float,
                   features: dict[str, Any],
                   provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = _call_optional(
        engine, "v3_market_bars", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._bars(engine, instrument, captured_ts),
        fallback=lambda exc: [],
    )
    points = _call_optional(
        engine, "v3_5m_points", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._five_minute_points(rows, captured_ts),
        fallback=lambda exc: [],
    )
    price = _call_optional(
        engine, "v3_price_volatility", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._price_block(rows, captured_ts),
    )
    vol = features.get("volatility") or {}
    annual_rv = _v3._finite(vol.get("reference_volatility_annual"))
    if annual_rv is None:
        annual_rv = _v3._finite(vol.get("reference_annual"))
    option_quality = _v3._finite(((provenance or {}).get("options") or {}).get("quality"))

    option_result = _call_optional(
        engine, "v3_option_distribution", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._option_blocks(engine, features, captured_ts, annual_rv, option_quality),
        fallback=lambda exc: tuple(
            _family_error_block("option_distribution", exc,
                                contract_version=_v3.MARKET_EVIDENCE_V3)
            for _ in range(3)
        ),
    )
    option_static, option_dynamics, gex = option_result

    cross_result = _call_optional(
        engine, "v3_cross_asset", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._cross_block(features, captured_ts, instrument),
        fallback=lambda exc: (
            _family_error_block("correlation", exc,
                                contract_version=_v3.MARKET_EVIDENCE_V3), None),
    )
    cross, raw_cross = cross_result
    macro = _call_optional(
        engine, "v3_macro", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._macro_block(points, raw_cross, captured_ts, instrument),
    )
    wavelet = _call_optional(
        engine, "v3_wavelet", _v3.MARKET_EVIDENCE_V3,
        lambda: _v3._wavelet_block(points, captured_ts),
    )
    families = {
        name: {
            "members": list(members), "training_enabled": False,
            "auto_fit": False, "ablation_required": True,
            "independent_vote": False,
        }
        for name, members in _v3.MARKET_FEATURE_FAMILIES.items()
    }
    return {
        "contract_version": _v3.MARKET_EVIDENCE_V3,
        "family_manifest_version": _v3.FAMILY_MANIFEST_VERSION,
        "operational_integrity_version": OPERATIONAL_INTEGRITY_VERSION,
        "captured_ts": float(captured_ts), "instrument": instrument,
        "market_price": float(market_price),
        "price_volatility": price,
        "option_static": option_static,
        "option_dynamics": option_dynamics,
        "gex": gex,
        "cross_asset": cross,
        "macro": macro,
        "wavelet": wavelet,
        "feature_families": families,
        "semantics": {
            "collect_wide_train_controlled": True,
            "future_captures_only": True,
            "no_historical_retrofit": True,
            "no_future_feature_backfill": True,
            "family_ablation_before_training_authority": True,
            "option_inputs_are_risk_neutral_context_only": True,
            "gex_is_not_observed_dealer_inventory": True,
            "cross_asset_has_no_causal_direction_claim": True,
            "macro_and_wavelet_are_context_not_independent_votes": True,
            "optional_feature_failure_does_not_cancel_core_t0": True,
            "production_authority": False,
        },
        "missing_is_not_zero": True,
    }


def install_g1_operational_integrity() -> None:
    if getattr(PassiveLearningEngine, "_g1_operational_integrity_version", None) == OPERATIONAL_INTEGRITY_VERSION:
        return

    # The already-installed V2/V3 capture closures resolve these module globals
    # at call time, so replacing the builders hardens the chain without changing
    # immutable storage or production decision authority.
    _v2._build_v2 = _safe_build_v2
    _v3.build_market_evidence_v3 = _safe_build_v3

    previous_step = PassiveLearningEngine.step
    previous_status = PassiveLearningEngine.status

    def step(self: PassiveLearningEngine, now: float | None = None):
        step_ts = float(now or time.time())
        result = previous_step(self, now=step_ts)
        health = _load_health(self)
        health["last_step_ts"] = step_ts
        error = result.get("error") if isinstance(result, dict) else None
        created = list((result or {}).get("created") or []) if isinstance(result, dict) else []
        if error:
            health["last_error_ts"] = step_ts
            health["last_error"] = str(error)
            health["consecutive_failed_capture_cycles"] = (
                int(health.get("consecutive_failed_capture_cycles") or 0) + 1)
        elif created:
            # A successful core INSERT is the recovery event. Optional family
            # errors stay in history but do not make the collector look stalled.
            health["consecutive_failed_capture_cycles"] = 0
            try:
                placeholders = ",".join("?" for _ in created)
                with self._lock:
                    row = self._conn.execute(
                        f"SELECT MAX(captured_ts) FROM passive_market_observations "
                        f"WHERE observation_id IN ({placeholders}) AND evidence_eligible=1",
                        tuple(created),
                    ).fetchone()
                if row and row[0] is not None:
                    health["last_successful_eligible_capture_ts"] = float(row[0])
            except Exception as exc:  # telemetry-only
                _record_feature_error(self, "collector_health", exc)
        # A market_closed/no-trigger cycle intentionally does not erase either
        # the persisted last meaningful error or a preceding failed-cycle count.
        _save_health(self, health)
        return result

    def status(self: PassiveLearningEngine):
        body = previous_status(self)
        now = time.time()
        health = _load_health(self)
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT MAX(CASE WHEN evidence_eligible=1 THEN captured_ts END),"
                    "SUM(CASE WHEN evidence_eligible=1 AND captured_ts>=? THEN 1 ELSE 0 END),"
                    "SUM(CASE WHEN evidence_eligible=1 AND captured_ts>=? THEN 1 ELSE 0 END) "
                    "FROM passive_market_observations",
                    (now - 3600.0, now - 86400.0),
                ).fetchone()
            latest_eligible = float(row[0]) if row and row[0] is not None else None
            eligible_1h = int(row[1] or 0) if row else 0
            eligible_24h = int(row[2] or 0) if row else 0
        except Exception:
            latest_eligible = health.get("last_successful_eligible_capture_ts")
            eligible_1h = eligible_24h = 0
        if latest_eligible is not None:
            health["last_successful_eligible_capture_ts"] = latest_eligible
        age = (max(0.0, now - float(latest_eligible))
               if latest_eligible is not None else None)
        cadence = float((body.get("budget") or {}).get(
            "base_observation_cadence_sec", _pl.OBSERVATION_CADENCE_SEC))
        market_open = False
        if not getattr(self.settings, "demo", False):
            try:
                market_open = any(
                    _pl._session_state(code, now).get("is_open", False)
                    for code in tuple(_pl.INSTRUMENTS)
                )
            except Exception:
                market_open = False
        stalled = bool(
            market_open
            and (latest_eligible is None or (age is not None and age > 2.0 * cadence))
        )
        if stalled:
            operational_status = "STALLED"
        elif int(health.get("consecutive_failed_capture_cycles") or 0) > 0:
            operational_status = "DEGRADED"
        else:
            operational_status = "RUNNING"
        body["collector_health"] = {
            **health,
            "operational_status": operational_status,
            "market_open_any_supported_instrument": market_open,
            "expected_capture_cadence_sec": cadence,
            "stall_threshold_sec": 2.0 * cadence,
            "last_successful_eligible_capture_ts": latest_eligible,
            "eligible_capture_age_sec": None if age is None else round(age, 1),
            "eligible_captures_1h": eligible_1h,
            "eligible_captures_24h": eligible_24h,
            "meaningful_error_persists_across_market_closed_cycle": True,
            "optional_feature_failure_cancels_core_capture": False,
        }
        body["operational_collector_status"] = operational_status
        return body

    PassiveLearningEngine.step = step
    PassiveLearningEngine.status = status
    PassiveLearningEngine._g1_operational_integrity_version = OPERATIONAL_INTEGRITY_VERSION
