"""Integrity refinements for Phase G.1B.1 Q evidence admission.

The base G.1B.1 runtime records every attempt. This layer makes a successful
Q capture fail-closed on source freshness and target-price provenance, and
verifies the frozen source/target/proxy mapping before the attempt can be
counted as successful. G.1A itself remains unchanged.
"""
from __future__ import annotations

import json
import math
from typing import Any

from . import g1_q_evidence_runtime as _q

_REFINED_CONTRACT_VERSION = "g1-q-evidence-integrity-v1"
_PRE_BLOCKER_KEY = "_g1b1_refined_pre_blocker"
_ORIGINAL_CLASSIFY = _q._classify_pre_capture
_ORIGINAL_VALIDATE = _q._validate_created_q
_ORIGINAL_STATUS = _q.g1_q_status


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _refined_classify_pre_capture(
    *, instrument: str, captured_ts: float, market_price: float,
    features: dict, provenance: dict,
) -> tuple[str | None, dict]:
    blocker, detail = _ORIGINAL_CLASSIFY(
        instrument=instrument,
        captured_ts=captured_ts,
        market_price=market_price,
        features=features,
        provenance=provenance,
    )
    capability = _q._capability_for(instrument)
    price_meta = provenance.get("price") if isinstance(provenance, dict) else {}
    options_meta = provenance.get("options") if isinstance(provenance, dict) else {}
    metrics = ((features.get("option_derivatives") or {}).get("data")
               if isinstance(features, dict) else None)

    # A configured Q source is not runtime-valid unless freshness is provable.
    # Cached chains can still be stored by the lower layer for diagnostics, but
    # they are not counted as successful prospective Q captures here.
    if blocker is None and capability.get("configured") and isinstance(metrics, dict):
        source_age = _finite((options_meta or {}).get("age_sec"))
        if source_age is None:
            blocker = "Q_SOURCE_STALE"
        elif source_age > _q.Q_SOURCE_SNAPSHOT_MAX_AGE_SEC:
            blocker = "OPTION_CHAIN_STALE"

    # Q->P return geometry needs a real, current target spot at T0. A Yahoo
    # fallback/index proxy is still useful elsewhere in the terminal but cannot
    # establish a pristine Q evidence capture.
    if blocker is None:
        target_age = _finite((price_meta or {}).get("age_sec"))
        if target_age is None or target_age > 60.0:
            blocker = "TARGET_PRICE_STALE"
        elif (price_meta or {}).get("kind") != "direct":
            blocker = "TARGET_PRICE_NON_DIRECT"

    detail = dict(detail or {})
    detail["integrity_contract_version"] = _REFINED_CONTRACT_VERSION
    detail["refined_pre_blocker"] = blocker
    detail["target_price_kind"] = (price_meta or {}).get("kind")
    detail["target_price_age_sec"] = _finite((price_meta or {}).get("age_sec"))
    detail["option_age_sec"] = _finite((options_meta or {}).get("age_sec"))

    # This mutates only the private deep-copied diagnostic snapshot created by
    # capture_observation_g1b1; it never enters the immutable passive T0 row.
    if isinstance(features, dict):
        features[_PRE_BLOCKER_KEY] = blocker
    return blocker, detail


def _load_native_forecast(self, native_id: str | None) -> dict:
    if not native_id:
        return {}
    with self._lock:
        row = self._conn.execute(
            "SELECT forecast_json FROM passive_market_observations WHERE observation_id=?",
            (native_id,),
        ).fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _refined_validate_created_q(
    self,
    ids: list[str],
    instrument: str,
    captured_ts: float,
    market_price: float,
    features: dict,
) -> tuple[bool, str | None, str | None, dict]:
    success, native_id, blocker, detail = _ORIGINAL_VALIDATE(
        self, ids, instrument, captured_ts, market_price, features,
    )
    detail = dict(detail or {})
    detail["integrity_contract_version"] = _REFINED_CONTRACT_VERSION

    pre_blocker = features.get(_PRE_BLOCKER_KEY) if isinstance(features, dict) else None
    if success and pre_blocker:
        detail["success_rejected_by_pre_capture_integrity"] = True
        return False, native_id, str(pre_blocker), detail
    if not success:
        return success, native_id, blocker, detail

    forecast = _load_native_forecast(self, native_id)
    capability = _q._capability_for(instrument)
    expected_source = capability.get("q_source_instrument")
    actual_source = forecast.get("q_source_instrument") or forecast.get("proxy_symbol")
    actual_target = forecast.get("q_target_instrument")
    actual_transform = str(forecast.get("proxy_transform") or "").lower()
    expected_transform = str(capability.get("proxy_transform") or "").lower()

    detail.update({
        "expected_q_source_instrument": expected_source,
        "actual_q_source_instrument": actual_source,
        "expected_q_target_instrument": instrument,
        "actual_q_target_instrument": actual_target,
        "expected_proxy_transform": expected_transform,
        "actual_proxy_transform": actual_transform,
    })

    if expected_source is None or str(actual_source) != str(expected_source):
        return False, native_id, "SOURCE_CONTRACT_ERROR", detail
    if str(actual_target) != str(instrument):
        return False, native_id, "SOURCE_CONTRACT_ERROR", detail
    if actual_transform != expected_transform or actual_transform not in {"direct", "inverse"}:
        return False, native_id, "PROXY_TRANSFORM_UNKNOWN", detail

    source_spot = _finite(forecast.get("q_source_spot"))
    target_spot = _finite(forecast.get("q_target_spot"))
    detail["frozen_q_source_spot"] = source_spot
    detail["frozen_q_target_spot"] = target_spot
    if source_spot is None or source_spot <= 0.0:
        return False, native_id, "SOURCE_SPOT_UNAVAILABLE", detail
    if target_spot is None or target_spot <= 0.0:
        return False, native_id, "TARGET_PRICE_UNAVAILABLE", detail

    return True, native_id, None, detail


def _refined_status(self) -> dict:
    status = _ORIGINAL_STATUS(self)
    status["q_evidence_integrity_contract_version"] = _REFINED_CONTRACT_VERSION
    return status


def install_g1_q_evidence_refinement() -> None:
    if getattr(_q, "_g1_q_evidence_integrity", None) == _REFINED_CONTRACT_VERSION:
        return
    _q._BLOCKERS.add("TARGET_PRICE_NON_DIRECT")
    _q._classify_pre_capture = _refined_classify_pre_capture
    _q._validate_created_q = _refined_validate_created_q
    _q._ENGINE.g1_q_status = _refined_status
    _q._g1_q_evidence_integrity = _REFINED_CONTRACT_VERSION
