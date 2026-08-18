"""Expose future-only frozen macro facts to the prospective EDE matrix.

The market collector already stores ``macro_context_v1`` inside immutable
``frozen_features_json``.  The base adapter predates that family and therefore
ignores it.  This refinement adds only values that were physically present at T0
and whose official ``available_at`` is <= T0.  It never fetches a release, calls an
LLM, reconstructs history, or changes production decision authority.
"""
from __future__ import annotations

import json
import math
from typing import Any

from .feature_view import feature_value
from .macro_registry import MACRO_DEFINITION_BY_ID, MACRO_FEATURE_IDS


MACRO_PROSPECTIVE_REFINEMENT_VERSION = "g1s-macro-prospective-refinement-v1"
_MONTHLY_STALE_SEC = 45.0 * 24.0 * 3600.0
_FOMC_STALE_SEC = 60.0 * 24.0 * 3600.0
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _release_family(feature_id: str) -> str | None:
    if feature_id.startswith("macro.cpi_"):
        return "CPI"
    if feature_id.startswith("macro.nfp_"):
        return "NFP"
    if feature_id.startswith("macro.ism_manufacturing_"):
        return "ISM_MANUFACTURING"
    if feature_id.startswith("macro.ism_services_"):
        return "ISM_SERVICES"
    return None


def _numeric_macro_candidates(
    frozen: dict[str, Any], t0: float,
) -> list[tuple[str, float, float, float, str]]:
    root = frozen.get("macro_context_v1")
    root = root if isinstance(root, dict) else {}
    numeric = root.get("numeric_macro")
    numeric = numeric if isinstance(numeric, dict) else {}
    vector = numeric.get("candidate_vector")
    vector = vector if isinstance(vector, dict) else {}
    releases = numeric.get("releases")
    releases = releases if isinstance(releases, dict) else {}
    output: list[tuple[str, float, float, float, str]] = []
    for feature_id, raw in vector.items():
        feature_id = str(feature_id)
        if feature_id not in MACRO_FEATURE_IDS:
            continue
        family = _release_family(feature_id)
        release = releases.get(family) if family else None
        release = release if isinstance(release, dict) else {}
        if release.get("status") != "VALID" or not release.get("official_source_verified"):
            continue
        asof = _finite(release.get("available_at"))
        value = _finite(raw)
        if asof is None or value is None or asof > t0 + 1e-6:
            continue
        output.append((feature_id, value, asof, _MONTHLY_STALE_SEC, str(family)))
    return output


def _fomc_candidates(
    frozen: dict[str, Any], t0: float,
) -> list[tuple[str, float, float, float, str]]:
    root = frozen.get("macro_context_v1")
    root = root if isinstance(root, dict) else {}
    fomc = root.get("fomc")
    fomc = fomc if isinstance(fomc, dict) else {}
    if not fomc.get("available") or not fomc.get("official_source_verified"):
        return []
    asof = _finite(fomc.get("available_at"))
    if asof is None or asof > t0 + 1e-6:
        return []
    semantic = fomc.get("semantic")
    semantic = semantic if isinstance(semantic, dict) else {}
    mapping = {
        "policy_tone": "macro.fomc_policy_tone",
        "policy_shift": "macro.fomc_policy_shift",
        "inflation_concern": "macro.fomc_inflation_concern",
        "growth_concern": "macro.fomc_growth_concern",
        "forward_guidance_shift": "macro.fomc_forward_guidance_shift",
        "uncertainty": "macro.fomc_uncertainty",
    }
    output: list[tuple[str, float, float, float, str]] = []
    for source_key, feature_id in mapping.items():
        value = _finite(semantic.get(source_key))
        if value is not None:
            output.append((feature_id, value, asof, _FOMC_STALE_SEC, "FOMC_STATEMENT"))
    return output


def install_macro_prospective_refinement(prospective_module) -> None:
    """Patch the adapter once; package import order guarantees deterministic use."""
    global _INSTALLED
    if _INSTALLED:
        return
    adapter = prospective_module.ProspectiveFeatureAdapter
    previous = adapter._feature_values

    def _feature_values(self, row: dict[str, Any], *, strict: bool):
        values, rejected, provenance = previous(self, row, strict=strict)
        t0 = float(row["captured_ts"])
        frozen = _loads(row.get("frozen_features_json"))
        candidates = _numeric_macro_candidates(frozen, t0) + _fomc_candidates(frozen, t0)
        for feature_id, value, asof, stale_after, family in candidates:
            definition = MACRO_DEFINITION_BY_ID[feature_id]
            try:
                record = feature_value(
                    instrument=str(row["instrument"]),
                    t0=t0,
                    horizon=int(row["horizon_minutes"]),
                    feature_id=feature_id,
                    value=value,
                    asof=asof,
                    quality=1.0,
                    stale_after_seconds=stale_after,
                    historical_available=False,
                    live_available=True,
                    training_eligible=definition.training_eligibility,
                    dependency_group=definition.dependency_family,
                )
                values[feature_id] = record
                provenance[feature_id] = {
                    "provenance": "FROZEN_T0_OFFICIAL_MACRO",
                    "macro_family": family,
                    "available_at": asof,
                    "future_points_used": False,
                    "historical_backfill": False,
                    "production_authority": False,
                    "refinement_version": MACRO_PROSPECTIVE_REFINEMENT_VERSION,
                }
            except ValueError as exc:
                rejected.append(feature_id)
                if strict:
                    raise ValueError(f"{feature_id} rejected: {exc}") from exc
        return values, rejected, provenance

    adapter._feature_values = _feature_values
    _INSTALLED = True
