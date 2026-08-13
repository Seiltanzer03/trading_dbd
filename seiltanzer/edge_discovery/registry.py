"""Machine-readable inventory of reusable causal T0 features.

This is an inventory and routing layer, not another implementation of the
metrics.  ``source`` points at the existing authoritative calculator/capture
contract.  Historical availability describes the immutable P1B 5m source set
used by the first EDE audit; prospective availability describes future T0
captures already supported by the terminal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


EDE_CONTRACT_VERSION = "g1s-edge-discovery-engine-v1"
RegistryAvailability = Literal["AVAILABLE", "LIMITED", "UNAVAILABLE"]

INVENTORY_SOURCES = (
    "seiltanzer/metric_contracts.py",
    "seiltanzer/g1_short_horizon_feature_contract_v2.py",
    "seiltanzer/g1_broad_market_evidence_v3.py",
    "seiltanzer/g1_management_feature_context_v2.py",
    "seiltanzer/option_shadow_state.py",
    "seiltanzer/core/cross_asset.py",
    "seiltanzer/core/macro_regime.py",
    "seiltanzer/core/wavelet.py",
    "seiltanzer/core/gex_field.py",
    "seiltanzer/web/js/regime_phase.js",
    "seiltanzer/web/js/wavelet.js",
    "seiltanzer/web/js/gex.js",
)


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    family: str
    source: str
    datatype: str
    t0_availability: str
    sampling_frequency: str
    asof_timestamp: str
    quality: str
    staleness: str
    historical_availability: RegistryAvailability
    live_availability: RegistryAvailability
    training_eligibility: bool
    dependency_family: str
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _f(feature_id: str, family: str, source: str, *, datatype: str = "float",
       frequency: str = "5m completed bar", asof: str = "bar_end_ts <= T0",
       quality: str = "source quality and availability stored separately",
       staleness: str = "stale when source age exceeds feature contract",
       historical: RegistryAvailability = "AVAILABLE",
       live: RegistryAvailability = "AVAILABLE", eligible: bool = True,
       dependency: str | None = None, notes: str = "") -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id, family=family, source=source, datatype=datatype,
        t0_availability="causal value only; absent value is UNAVAILABLE, never zero",
        sampling_frequency=frequency, asof_timestamp=asof, quality=quality,
        staleness=staleness, historical_availability=historical,
        live_availability=live, training_eligibility=eligible,
        dependency_family=dependency or family.lower(), notes=notes,
    )


# The first audit consumes only the AVAILABLE P1B subset.  LIMITED features may
# exist in future-only T0 observations, but cannot pass a historical sample gate
# until their actual immutable coverage is demonstrated.
FEATURES: tuple[FeatureDefinition, ...] = (
    _f("price.ret_5m", "PRICE", "g1_short_horizon_historical_wf._build_horizon_rows"),
    _f("price.ret_15m", "PRICE", "g1_short_horizon_historical_wf._build_horizon_rows"),
    _f("price.ret_60m", "PRICE", "g1_short_horizon_historical_wf._build_horizon_rows"),
    _f("price.momentum", "PRICE", "g1_broad_market_evidence_v3._price_block"),
    _f("price.acceleration", "PRICE", "edge_discovery.feature_view.causal_dynamics",
       notes="generic causal transform of an accepted price series"),
    _f("price.trend_efficiency_60", "PRICE", "g1_short_horizon_p2e_segmented_persistence._source_context"),
    _f("price.range_60", "PRICE", "g1_short_horizon_p2e_segmented_persistence._source_context"),
    _f("price.drawdown_60", "PRICE", "g1_broad_market_evidence_v3._price_block",
       historical="LIMITED"),
    _f("price.drawup_60", "PRICE", "g1_broad_market_evidence_v3._price_block",
       historical="LIMITED"),
    _f("vol.rv_15m", "VOLATILITY", "g1_short_horizon_historical_wf._build_horizon_rows"),
    _f("vol.rv_60m", "VOLATILITY", "g1_short_horizon_historical_wf._build_horizon_rows"),
    _f("vol.rv15_over_rv60", "VOLATILITY", "g1_short_horizon_p2e_segmented_persistence._source_context"),
    _f("vol.expansion_state", "VOLATILITY", "g1_broad_market_evidence_v3._price_block"),
    _f("vol.vol_of_vol", "VOLATILITY", "g1_broad_market_evidence_v3._price_block",
       historical="LIMITED"),
    _f("option.iv", "OPTIONS", "g1_short_horizon_feature_contract_v2._option_scalars",
       frequency="option-chain snapshot", asof="chain snapshot ts <= T0",
       historical="UNAVAILABLE", live="AVAILABLE", dependency="option_distribution"),
    _f("option.iv_rv_ratio", "OPTIONS", "g1_short_horizon_feature_contract_v2._option_scalars",
       frequency="min(option chain, realized vol)", asof="both legs <= T0",
       historical="UNAVAILABLE", dependency="option_distribution"),
    _f("option.skew", "OPTIONS", "g1_short_horizon_feature_contract_v2._option_scalars",
       frequency="option-chain snapshot", asof="chain snapshot ts <= T0",
       historical="UNAVAILABLE", dependency="option_distribution"),
    _f("option.term_slope", "OPTIONS", "g1_short_horizon_feature_contract_v2._option_scalars",
       frequency="option-chain snapshot", asof="chain snapshot ts <= T0",
       historical="UNAVAILABLE", dependency="option_distribution"),
    _f("option.gex_net_balance", "OPTIONS", "g1_short_horizon_feature_contract_v2._option_scalars",
       frequency="option-chain snapshot", historical="UNAVAILABLE",
       dependency="option_distribution", notes="heuristic context; dealer sign unobserved"),
    _f("option.zero_gamma_distance", "OPTIONS", "g1_broad_market_evidence_v3._gex_snapshot",
       frequency="option-chain snapshot", historical="UNAVAILABLE",
       dependency="option_distribution", notes="heuristic context; dealer sign unobserved"),
    _f("option.delta", "OPTIONS", "g1_short_horizon_feature_contract_v2._bs_greek_context",
       frequency="option-chain snapshot", historical="UNAVAILABLE", dependency="option_distribution"),
    _f("option.vanna", "OPTIONS", "g1_short_horizon_feature_contract_v2._bs_greek_context",
       frequency="option-chain snapshot", historical="UNAVAILABLE", dependency="option_distribution"),
    _f("option.charm", "OPTIONS", "g1_short_horizon_feature_contract_v2._bs_greek_context",
       frequency="option-chain snapshot", historical="UNAVAILABLE", dependency="option_distribution"),
    _f("option.barrier_probability", "OPTIONS", "metric_contracts.CONTRACTS",
       frequency="accepted option scenario snapshot", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("option.rnd_geometry", "OPTIONS", "metric_contracts.CONTRACTS",
       frequency="accepted option scenario snapshot", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("option_dynamics.iv_velocity", "OPTION_DYNAMICS", "g1_broad_market_evidence_v3._option_blocks",
       frequency="accepted sequential option snapshots", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("option_dynamics.skew_velocity", "OPTION_DYNAMICS", "g1_broad_market_evidence_v3._option_blocks",
       frequency="accepted sequential option snapshots", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("option_dynamics.gex_velocity", "OPTION_DYNAMICS", "g1_broad_market_evidence_v3._option_blocks",
       frequency="accepted sequential option snapshots", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("option_dynamics.vanna_velocity", "OPTION_DYNAMICS", "g1_broad_market_evidence_v3._option_blocks",
       frequency="accepted sequential option snapshots", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("option_dynamics.charm_velocity", "OPTION_DYNAMICS", "g1_broad_market_evidence_v3._option_blocks",
       frequency="accepted sequential option snapshots", historical="UNAVAILABLE",
       dependency="option_distribution"),
    _f("cross.confirmation", "CROSS_ASSET", "edge_discovery.historical.aligned_cross_asset_context",
       notes="causally aligned observed peer return, not reconstructed correlation"),
    _f("cross.family_breadth", "CROSS_ASSET", "edge_discovery.historical.aligned_cross_asset_context"),
    _f("cross.market_breadth", "CROSS_ASSET", "edge_discovery.historical.aligned_cross_asset_context"),
    _f("cross.correlation", "CROSS_ASSET", "core.cross_asset.compute_correlation_graph",
       historical="UNAVAILABLE", live="AVAILABLE"),
    _f("cross.correlation_change", "CROSS_ASSET", "core.cross_asset.compute_correlation_graph",
       historical="UNAVAILABLE", live="AVAILABLE"),
    _f("regime.asset", "REGIME", "config.INSTRUMENTS", datatype="category"),
    _f("regime.asset_family", "REGIME", "g1_short_horizon_p2e_segmented_persistence.ASSET_FAMILY_BY_INSTRUMENT",
       datatype="category"),
    _f("regime.session_utc", "REGIME", "g1_short_horizon_p2e_segmented_persistence.session_utc",
       datatype="category"),
    _f("regime.trend", "REGIME", "g1_broad_market_evidence_v3._price_block",
       datatype="category"),
    _f("regime.volatility", "REGIME", "g1_broad_market_evidence_v3._price_block",
       datatype="category"),
    _f("regime.macro", "REGIME", "core.macro_regime.compute_macro_regime",
       datatype="category", historical="UNAVAILABLE", live="AVAILABLE"),
    _f("regime.wavelet_phase", "REGIME", "core.wavelet.compute_wavelet_analysis",
       datatype="category", historical="LIMITED", live="AVAILABLE"),
    _f("quality.availability", "DATA_QUALITY", "edge_discovery.feature_view.FeatureValue",
       datatype="category", eligible=False, dependency="data_quality",
       notes="never interpreted as a market predictor"),
    _f("quality.staleness", "DATA_QUALITY", "edge_discovery.feature_view.FeatureValue",
       datatype="boolean", eligible=False, dependency="data_quality",
       notes="provider outage/staleness never interpreted as market state"),
)


def feature_registry() -> dict[str, Any]:
    families = sorted({feature.family for feature in FEATURES})
    unavailable = [feature.feature_id for feature in FEATURES
                   if feature.historical_availability == "UNAVAILABLE"]
    return {
        "contract_version": EDE_CONTRACT_VERSION,
        "registry_kind": "logical_feature_store_inventory",
        "inventory_sources": list(INVENTORY_SOURCES),
        "feature_count": len(FEATURES),
        "family_count": len(families),
        "families": families,
        "features": [feature.as_dict() for feature in FEATURES],
        "historically_unavailable": unavailable,
        "missing_is_not_zero": True,
        "provider_outage_is_not_market_signal": True,
        "production_authority": False,
        "auto_promotion": False,
    }
