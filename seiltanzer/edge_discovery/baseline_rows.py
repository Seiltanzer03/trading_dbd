"""Causal row eligibility for the EDE primary persistence comparator."""
from __future__ import annotations

import math
from typing import Any


BASELINE_REQUIRED_FEATURES = ("ret_5m", "ret_15m")


def baseline_eligible_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep only rows scoreable by GLOBAL_RET5_PERSISTENCE and sanity baseline.

    Missing T0 returns are missing evidence, never zero. Filtering occurs before
    temporal folds are built, so candidate and baseline always see the exact same
    causally scoreable observation universe.
    """
    eligible: list[dict[str, Any]] = []
    missing_by_feature = {feature: 0 for feature in BASELINE_REQUIRED_FEATURES}
    invalid_direction = 0
    for row in rows:
        features = row.get("features") or {}
        valid = True
        for feature in BASELINE_REQUIRED_FEATURES:
            value = features.get(feature)
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = float("nan")
            if not math.isfinite(number):
                missing_by_feature[feature] += 1
                valid = False
        if row.get("direction_label") not in {"UP", "DOWN"}:
            invalid_direction += 1
            valid = False
        if valid:
            eligible.append(row)
    return eligible, {
        "contract_version": "g1s-ede-baseline-row-gate-v1",
        "primary_baseline": "GLOBAL_RET5_PERSISTENCE",
        "required_t0_features": list(BASELINE_REQUIRED_FEATURES),
        "input_rows": len(rows),
        "eligible_rows": len(eligible),
        "excluded_rows": len(rows)-len(eligible),
        "missing_by_feature": missing_by_feature,
        "invalid_direction_rows": invalid_direction,
        "missing_is_zero": False,
        "filter_before_temporal_folds": True,
    }
