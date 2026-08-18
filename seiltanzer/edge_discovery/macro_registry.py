"""Future-only causal MACRO features for prospective Edge research.

These definitions deliberately have no historical availability: the terminal only
learns from official releases that were actually fetched and frozen into a T0
after this capability existed.  This prevents hindsight backfill while still
letting the normal prospective 15/30/60/120/240m machinery measure whether CPI,
NFP, ISM and FOMC state has repeatable OOS value.
"""
from __future__ import annotations

from .registry import FeatureDefinition, _f


MACRO_REGISTRY_VERSION = "g1s-macro-feature-registry-v1"


def _monthly(feature_id: str, family: str, notes: str = "") -> FeatureDefinition:
    return _f(
        feature_id,
        "MACRO",
        "macro_t0_context.build_macro_t0_context",
        frequency=f"official {family} release; frozen at subsequent T0 captures",
        asof="official first-seen available_at <= T0",
        quality="official-source verification + immutable first-seen release SHA",
        staleness="stale when first-seen release is older than 45 days",
        historical="UNAVAILABLE",
        live="AVAILABLE",
        eligible=True,
        dependency=f"macro_{family.lower()}",
        notes=(
            "future prospective evidence only; no historical backfill; no consensus/surprise "
            "unless a licensed consensus feed is added later; production authority false"
            + (f"; {notes}" if notes else "")
        ),
    )


def _fomc(feature_id: str, notes: str = "") -> FeatureDefinition:
    return _f(
        feature_id,
        "MACRO",
        "macro_data_factory.MacroDataFactory",
        frequency="official FOMC statement; semantic extraction SHA-cached after first fetch",
        asof="semantic extraction available_at <= T0",
        quality="official Federal Reserve source + strict bounded semantic schema",
        staleness="stale when statement semantic context is older than 60 days",
        historical="UNAVAILABLE",
        live="AVAILABLE",
        eligible=True,
        dependency="macro_fomc",
        notes=(
            "LLM is extractor only, never market forecaster; future prospective evidence only; "
            "no historical semantic backfill; production authority false"
            + (f"; {notes}" if notes else "")
        ),
    )


_CPI_IDS = (
    "macro.cpi_headline_mom_pct",
    "macro.cpi_core_mom_pct",
    "macro.cpi_headline_yoy_pct",
    "macro.cpi_core_yoy_pct",
    "macro.cpi_headline_mom_change_pp",
    "macro.cpi_core_mom_change_pp",
)

_NFP_IDS = (
    "macro.nfp_payroll_change_k",
    "macro.nfp_previous_payroll_change_k",
    "macro.nfp_unemployment_rate_pct",
    "macro.nfp_unemployment_change_pp",
    "macro.nfp_wage_mom_pct",
    "macro.nfp_wage_yoy_pct",
)

_ISM_MANUFACTURING_COMPONENTS = (
    "pmi", "new_orders", "production", "employment",
    "supplier_deliveries", "inventories", "prices",
)
_ISM_SERVICES_COMPONENTS = (
    "pmi", "business_activity", "new_orders", "employment",
    "supplier_deliveries", "inventories", "prices",
)

_FOMC_IDS = (
    "macro.fomc_policy_tone",
    "macro.fomc_policy_shift",
    "macro.fomc_inflation_concern",
    "macro.fomc_growth_concern",
    "macro.fomc_forward_guidance_shift",
    "macro.fomc_uncertainty",
)


MACRO_FEATURE_DEFINITIONS: tuple[FeatureDefinition, ...] = (
    *tuple(_monthly(feature_id, "CPI") for feature_id in _CPI_IDS),
    *tuple(_monthly(feature_id, "NFP") for feature_id in _NFP_IDS),
    *tuple(
        definition
        for component in _ISM_MANUFACTURING_COMPONENTS
        for definition in (
            _monthly(f"macro.ism_manufacturing_{component}", "ISM_MANUFACTURING"),
            _monthly(f"macro.ism_manufacturing_{component}_change_pp", "ISM_MANUFACTURING"),
        )
    ),
    *tuple(
        definition
        for component in _ISM_SERVICES_COMPONENTS
        for definition in (
            _monthly(f"macro.ism_services_{component}", "ISM_SERVICES"),
            _monthly(f"macro.ism_services_{component}_change_pp", "ISM_SERVICES"),
        )
    ),
    *tuple(_fomc(feature_id) for feature_id in _FOMC_IDS),
)


MACRO_FEATURE_IDS = frozenset(item.feature_id for item in MACRO_FEATURE_DEFINITIONS)
MACRO_DEFINITION_BY_ID = {item.feature_id: item for item in MACRO_FEATURE_DEFINITIONS}


def macro_feature_definitions() -> tuple[FeatureDefinition, ...]:
    return MACRO_FEATURE_DEFINITIONS
