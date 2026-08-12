"""Small fail-closed corrections for the additive G.1S V2 feature layer."""
from __future__ import annotations

from . import g1_short_horizon_feature_contract_v2 as _v2
from .g1_short_horizon_runtime import ShortHorizonRuntime


FEATURE_V2_INTEGRITY_VERSION = "g1s-feature-v2-integrity-v1"


def install_g1_short_horizon_feature_v2_integrity() -> None:
    if getattr(ShortHorizonRuntime, "_feature_v2_integrity_version", None) == FEATURE_V2_INTEGRITY_VERSION:
        return
    previous_cross = _v2._cross_asset

    def strict_cross(features, captured_ts, instrument):
        # Project code is XAU; the base V2 helper's gold branch was authored as
        # XAUUSD. Route it to the intended GOLD/GVZ pair without touching V1.
        lookup_instrument = "XAUUSD" if instrument == "XAU" else instrument
        result = previous_cross(features, captured_ts, lookup_instrument)
        # A rejected future timestamp is diagnostic, not an admitted source.
        # Keep it under a non-source timestamp key so the generic T0 validator
        # cannot mistake rejected evidence for a frozen feature source.
        if result.get("reason") == "cross_asset_after_t0" and "source_ts" in result:
            result["rejected_asof"] = result.pop("source_ts")
        return result

    _v2._cross_asset = strict_cross
    ShortHorizonRuntime._feature_v2_integrity_version = FEATURE_V2_INTEGRITY_VERSION
