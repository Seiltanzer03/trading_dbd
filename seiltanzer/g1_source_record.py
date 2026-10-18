"""Canonical resolved-source identity shared by live and stdlib storage tools.

The byte contract is unchanged from g1_dataset_runtime; no source authority
or eligibility is inferred from a matching hash.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def source_record_payload(row: dict) -> dict:
    """Canonical post-resolution research record used for mutation detection."""
    return {
        "observation_id": row.get("observation_id"),
        "anchor_group_id": row.get("anchor_group_id"),
        "captured_ts": finite(row.get("captured_ts")),
        "target_ts": finite(row.get("target_ts")),
        "resolved_ts": finite(row.get("resolved_ts")),
        "instrument": row.get("instrument"),
        "horizon_minutes": row.get("horizon_minutes"),
        "trigger_reason": row.get("trigger_reason"),
        "market_price": finite(row.get("market_price")),
        "price_source": row.get("price_source"),
        "price_age_sec": finite(row.get("price_age_sec")),
        "price_quality": finite(row.get("price_quality")),
        "price_kind": row.get("price_kind"),
        "option_source": row.get("option_source"),
        "option_age_sec": finite(row.get("option_age_sec")),
        "option_quality": finite(row.get("option_quality")),
        "option_kind": row.get("option_kind"),
        "market_regime": row.get("market_regime"),
        "session": row.get("session"),
        "feature_contract_version": row.get("feature_contract_version"),
        "forecast_model_version": row.get("forecast_model_version"),
        "calibrator_version": row.get("calibrator_version"),
        "scenario_version": row.get("scenario_version"),
        "features": _loads(row.get("features_json"), {}),
        "forecast": _loads(row.get("forecast_json"), {}),
        "evidence_eligible": int(row.get("evidence_eligible") or 0),
        "resolution_status": row.get("resolution_status"),
        "outcome": _loads(row.get("outcome_json"), None),
        "calendar_elapsed": finite(row.get("calendar_elapsed")),
        "trading_elapsed": finite(row.get("trading_elapsed")),
        "market_open_fraction": finite(row.get("market_open_fraction")),
        "retrospective_replay": int(row.get("retrospective_replay") or 0),
        "observation_origin": row.get("observation_origin"),
    }


def source_record_sha256(row: dict) -> str:
    return _sha256(source_record_payload(row))
