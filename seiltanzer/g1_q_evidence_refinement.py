"""Integrity refinements for Phase G.1B.1 Q evidence admission.

The base G.1B.1 runtime records every attempt. This layer makes a successful
Q capture fail-closed on source freshness and target-price provenance, verifies
the frozen source/target/proxy mapping, and prevents an invalid Q attempt from
creating an option-native dataset row. G.1A itself remains unchanged.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

from . import g1_q_evidence_runtime as _q

_REFINED_CONTRACT_VERSION = "g1-q-evidence-integrity-v1"
_PRE_BLOCKER_KEY = "_g1b1_refined_pre_blocker"
_FORCED_BLOCKER_KEY = "_g1b1_forced_blocker"
_FORCED_DETAIL_KEY = "_g1b1_forced_detail"
_ORIGINAL_CLASSIFY = _q._classify_pre_capture
_ORIGINAL_VALIDATE = _q._validate_created_q
_ORIGINAL_STATUS = _q.g1_q_status
_ORIGINAL_CAPTURE_METHOD = _q._ENGINE.capture_observation


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
    forced = provenance.get(_FORCED_BLOCKER_KEY) if isinstance(provenance, dict) else None
    if forced:
        detail = deepcopy(provenance.get(_FORCED_DETAIL_KEY) or {})
        detail["integrity_contract_version"] = _REFINED_CONTRACT_VERSION
        detail["refined_pre_blocker"] = str(forced)
        if isinstance(features, dict):
            features[_PRE_BLOCKER_KEY] = str(forced)
        return str(forced), detail

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
    if blocker is None and capability.get("configured") and isinstance(metrics, dict):
        source_age = _finite((options_meta or {}).get("age_sec"))
        if source_age is None:
            blocker = "Q_SOURCE_STALE"
        elif source_age > _q.Q_SOURCE_SNAPSHOT_MAX_AGE_SEC:
            blocker = "OPTION_CHAIN_STALE"

    # Q->P return geometry needs a real, current target spot at T0.
    if blocker is None:
        target_age = _finite((price_meta or {}).get("age_sec"))
        if target_age is None or target_age > 60.0:
            blocker = "TARGET_PRICE_STALE"
        elif (price_meta or {}).get("kind") != "direct":
            blocker = "TARGET_PRICE_NON_DIRECT"

    # Probe the exact F.3.2a terminal-Q adapter before the lower capture layer can
    # create an option-native row. This catches malformed/non-normalizable CDFs
    # and mapping drift prospectively instead of leaving a failed Q row behind.
    if blocker is None and isinstance(metrics, dict):
        probe = _q.adapt_option_q_forecast_f32a(
            metrics, 1, None, instrument, instrument_spot=market_price,
            horizon_kind="option_native_expiry",
        )
        method = str(probe.get("horizon_alignment_method") or "")
        if (
            probe.get("probability_measure") != "risk_neutral_Q_terminal"
            or probe.get("q_terminal_distribution_available") is not True
        ):
            blocker = "CDF_INVALID" if "cdf" in method else "CDF_BUILD_FAILED"
        elif not _q.valid_terminal_cdf(probe.get("terminal_q_cdf")):
            blocker = "CDF_INVALID"
        elif str(probe.get("q_source_instrument") or probe.get("proxy_symbol")) != str(
            capability.get("q_source_instrument")
        ):
            blocker = "SOURCE_CONTRACT_ERROR"
        elif str(probe.get("q_target_instrument")) != str(instrument):
            blocker = "SOURCE_CONTRACT_ERROR"
        elif str(probe.get("proxy_transform") or "").lower() != str(
            capability.get("proxy_transform") or ""
        ).lower():
            blocker = "PROXY_TRANSFORM_UNKNOWN"

    detail = dict(detail or {})
    detail["integrity_contract_version"] = _REFINED_CONTRACT_VERSION
    detail["refined_pre_blocker"] = blocker
    detail["target_price_kind"] = (price_meta or {}).get("kind")
    detail["target_price_age_sec"] = _finite((price_meta or {}).get("age_sec"))
    detail["option_age_sec"] = _finite((options_meta or {}).get("age_sec"))

    # This marker is used only on the private diagnostic copy owned by the base
    # G.1B.1 wrapper. It must never be written into passive features_json.
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
    native_ids = [str(item) for item in ids if str(item).endswith("-native-expiry")]
    pre_blocker = features.get(_PRE_BLOCKER_KEY) if isinstance(features, dict) else None
    if pre_blocker and not native_ids:
        return False, None, str(pre_blocker), {
            "integrity_contract_version": _REFINED_CONTRACT_VERSION,
            "native_row_suppressed": True,
        }

    success, native_id, blocker, detail = _ORIGINAL_VALIDATE(
        self, ids, instrument, captured_ts, market_price, features,
    )
    detail = dict(detail or {})
    detail["integrity_contract_version"] = _REFINED_CONTRACT_VERSION

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


def _capture_observation_refined(
    self,
    *,
    instrument: str,
    captured_ts: float,
    market_price: float,
    features: dict,
    forecast: dict,
    provenance: dict,
    trigger_reason: str = "cadence",
    evidence_eligible: bool = True,
    observation_origin: str | None = None,
) -> list[str]:
    probe_features = deepcopy(features) if isinstance(features, dict) else {}
    probe_provenance = deepcopy(provenance) if isinstance(provenance, dict) else {}
    blocker, detail = _refined_classify_pre_capture(
        instrument=instrument,
        captured_ts=float(captured_ts),
        market_price=float(market_price),
        features=probe_features,
        provenance=probe_provenance,
    )
    if blocker is None:
        return _ORIGINAL_CAPTURE_METHOD(
            self, instrument=instrument, captured_ts=captured_ts,
            market_price=market_price, features=features, forecast=forecast,
            provenance=provenance, trigger_reason=trigger_reason,
            evidence_eligible=evidence_eligible, observation_origin=observation_origin,
        )

    # Keep ordinary fixed-horizon collection alive, but remove unusable option
    # geometry from the copy passed to the lower layer. This makes the failed Q
    # attempt observable while preventing creation of a native-expiry Q row.
    safe_features = deepcopy(features) if isinstance(features, dict) else {}
    safe_features["option_derivatives"] = {"available": False, "data": None}
    safe_features["option_distribution"] = {
        "available": False,
        "reason": "q_capture_blocked",
    }
    safe_provenance = deepcopy(provenance) if isinstance(provenance, dict) else {}
    safe_provenance[_FORCED_BLOCKER_KEY] = blocker
    safe_provenance[_FORCED_DETAIL_KEY] = detail
    return _ORIGINAL_CAPTURE_METHOD(
        self, instrument=instrument, captured_ts=captured_ts,
        market_price=market_price, features=safe_features, forecast=forecast,
        provenance=safe_provenance, trigger_reason=trigger_reason,
        evidence_eligible=evidence_eligible, observation_origin=observation_origin,
    )


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
    _q._ENGINE.capture_observation = _capture_observation_refined
    _q._ENGINE.g1_q_status = _refined_status
    _q._g1_q_evidence_integrity = _REFINED_CONTRACT_VERSION
