"""Deterministic identity for the actual inputs consumed by EDE research.

The v1 production audit originally hashed only observation/timestamp identity.
That was insufficient: outcome resolution, causal feature backfill, or EDE
eligibility could change while those timestamps stayed fixed, causing a new
research result to collide with an immutable historical evaluation.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable


DATASET_FINGERPRINT_CONTRACT_VERSION = "g1s-ede-dataset-fingerprint-v2"

_RESEARCH_ROW_FIELDS = (
    "observation_id",
    "instrument",
    "captured_ts",
    "target_ts",
    "resolved_ts",
    "horizon_minutes",
    "direction_label",
    "terminal_log_return",
    "mfe_log_return",
    "mae_log_return",
    "features",
    "ede_features",
    "rejected_feature_ids",
    "prospective_adapter_version",
    "retrospective_options_reconstruction",
)


def _finite_json(value: Any) -> Any:
    """Normalize JSON-compatible research inputs and fail closed on NaN/inf."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value in EDE dataset fingerprint")
        return value
    if isinstance(value, dict):
        return {str(key): _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported EDE fingerprint value: {type(value).__name__}")


def _row_identity(row: dict[str, Any]) -> tuple[float, str, int, str]:
    return (
        float(row["captured_ts"]),
        str(row["instrument"]),
        int(row["horizon_minutes"]),
        str(row["observation_id"]),
    )


def research_dataset_fingerprint(
    rows: Iterable[dict[str, Any]], *, eligible_feature_ids: Iterable[str],
) -> str:
    """Hash all deterministic inputs that can affect selective EDE evaluation.

    This intentionally excludes wall-clock/materialization metadata. A rerun on
    the same immutable research inputs must deduplicate, while any change to an
    outcome, baseline feature, EDE feature, adapter version, rejected feature
    set, or eligible feature universe must produce a distinct dataset identity.
    """
    projected = []
    for row in sorted((dict(item) for item in rows), key=_row_identity):
        projected.append({
            field: _finite_json(row.get(field))
            for field in _RESEARCH_ROW_FIELDS
        })
    payload = {
        "contract_version": DATASET_FINGERPRINT_CONTRACT_VERSION,
        "eligible_feature_ids": sorted({str(item) for item in eligible_feature_ids}),
        "rows": projected,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
