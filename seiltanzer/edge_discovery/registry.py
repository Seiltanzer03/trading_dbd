"""Machine-readable inventory of reusable causal T0 features.

This is an inventory and routing layer, not another implementation of the
metrics.  ``source`` points at the existing authoritative calculator/capture
contract.  Historical availability describes the immutable/point-in-time source
set admitted by research; prospective availability describes future T0 captures
already supported by the terminal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


EDE_CONTRACT_VERSION = "g1s-edge-discovery-engine-v1.3-macro-registry"
RegistryAvailability = Literal["AVAILABLE", "LIMITED", "UNAVAILABLE"]
ResearchScope = Literal["G1S", "G1M_ONLY", "QUALITY_ONLY"]

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
    "seiltanzer/macro_t0_context.py",
    "seiltanzer/macro_bls_historical_bootstrap.py",
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
    research_scope: ResearchScope = "G1S"
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _f(feature_id: str, family: str, source: str, *, datatype: str = "float",
       frequency: str = "5m completed bar", asof: str = "bar_end_ts <= T0",
       quality: str = "source quality and availability stored separately",
       staleness: str = "stale when source age exceeds feature contract",
       historical: RegistryAvailability = "AVAILABLE",
       live: RegistryAvailability = "AVAILABLE", eligible: bool = True,
       dependency: str | None = None, scope: ResearchScope = "G1S",
       notes: str = "") -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id, family=family, source=source, datatype=datatype,
        t0_availability="causal value only; absent value is UNAVAILABLE, never zero",
        sampling_frequency=frequency, asof_timestamp=asof, quality=quality,
        staleness=staleness, historical_availability=historical,
        live_availability=live, training_eligibility=eligible,
        dependency_family=dependency or family.lower(), research_scope=scope,
        notes=notes,
    )


# Registry identity is static. Runtime refinements may materialize values, but
# must not change which canonical IDs exist based on import order.
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
       dependency="option_distribution", live="LIMITED", eligible=False,
       scope="G1M_ONLY",
       notes="captured in immutable G1M trade-management context, not yet in per-instrument G1S EDE rows"),
    _f("option.rnd_geometry", "OPTIONS", "metric_contracts.CONTRACTS",
       frequency="accepted option scenario snapshot", historical="UNAVAILABLE",
       dependency="option_distribution", live="UNAVAILABLE", eligible=False,
       scope="G1M_ONLY",
       notes="no immutable per-instrument G1S T0 materialization contract found"),
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
    _f("option_dynamics.zero_gamma_velocity", "OPTION_DYNAMICS", "g1_broad_market_evidence_v3._option_blocks",
       frequency="accepted sequential option snapshots", historical="UNAVAILABLE",
       dependency="option_distribution"),
    *tuple(
        _f(
            f"option_dynamics.{metric}_{transform}", "OPTION_DYNAMICS",
            "edge_discovery.feature_view.causal_dynamics",
            frequency="accepted sequential immutable T0 observations",
            historical="UNAVAILABLE", dependency="option_distribution",
            notes="causal transform materialized by the prospective feature adapter",
        )
        for metric in ("iv", "skew", "gex", "vanna", "charm", "zero_gamma")
        for transform in (
            "acceleration", "rolling_rank", "rolling_zscore", "direction_consistency")
    ),
    _f("cross.confirmation", "CROSS_ASSET", "edge_discovery.historical.aligned_cross_asset_context",
       datatype="category",
       notes="causally aligned observed peer return category SAME/OPPOSITE, not reconstructed correlation"),
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
    _f("regime.wavelet_phase", "REGIME", "g1_broad_market_evidence_v3._wavelet_block",
       datatype="float", historical="LIMITED", live="AVAILABLE",
       notes="canonical ID currently materializes numeric wavelet phase_stability; not a categorical phase label"),

    # Official macro release features are canonical IDs, not a runtime extension.
    # CPI/NFP historical capability is backed only by original archived BLS
    # release copies. ISM/FOMC remain future/live only until separate historical
    # vintage contracts are implemented.
    _f("macro.cpi_headline_mom_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official CPI release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:CPI",
       notes="official release vintage; repeated T0 rows share one release dependence unit"),
    _f("macro.cpi_core_mom_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official CPI release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:CPI"),
    _f("macro.cpi_headline_yoy_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official CPI release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:CPI"),
    _f("macro.cpi_core_yoy_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official CPI release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:CPI"),
    _f("macro.cpi_headline_mom_change_pp", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official CPI release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:CPI"),
    _f("macro.cpi_core_mom_change_pp", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official CPI release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:CPI"),
    _f("macro.nfp_payroll_change_k", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official Employment Situation release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:NFP"),
    _f("macro.nfp_previous_payroll_change_k", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official Employment Situation release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:NFP"),
    _f("macro.nfp_unemployment_rate_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official Employment Situation release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:NFP"),
    _f("macro.nfp_unemployment_change_pp", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official Employment Situation release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:NFP"),
    _f("macro.nfp_wage_mom_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official Employment Situation release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:NFP"),
    _f("macro.nfp_wage_yoy_pct", "MACRO_NUMERIC", "macro_t0_context+macro_bls_historical_bootstrap",
       frequency="official Employment Situation release", asof="official release available_at/published_at <= T0",
       historical="AVAILABLE", dependency="macro_release:NFP"),
    _f("macro.ism_manufacturing_pmi", "MACRO_NUMERIC", "macro_t0_context.numeric_macro",
       frequency="official ISM Manufacturing release", asof="official release available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:ISM_MANUFACTURING"),
    _f("macro.ism_manufacturing_pmi_change_pp", "MACRO_NUMERIC", "macro_t0_context.numeric_macro",
       frequency="official ISM Manufacturing release", asof="official release available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:ISM_MANUFACTURING"),
    _f("macro.ism_services_pmi", "MACRO_NUMERIC", "macro_t0_context.numeric_macro",
       frequency="official ISM Services release", asof="official release available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:ISM_SERVICES"),
    _f("macro.ism_services_pmi_change_pp", "MACRO_NUMERIC", "macro_t0_context.numeric_macro",
       frequency="official ISM Services release", asof="official release available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:ISM_SERVICES"),
    _f("macro.fomc_policy_tone", "MACRO_FOMC", "macro_t0_context.fomc",
       frequency="official FOMC statement", asof="official statement available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:FOMC_STATEMENT"),
    _f("macro.fomc_policy_shift", "MACRO_FOMC", "macro_t0_context.fomc",
       frequency="official FOMC statement", asof="official statement available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:FOMC_STATEMENT"),
    _f("macro.fomc_inflation_concern", "MACRO_FOMC", "macro_t0_context.fomc",
       frequency="official FOMC statement", asof="official statement available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:FOMC_STATEMENT"),
    _f("macro.fomc_growth_concern", "MACRO_FOMC", "macro_t0_context.fomc",
       frequency="official FOMC statement", asof="official statement available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:FOMC_STATEMENT"),
    _f("macro.fomc_forward_guidance_shift", "MACRO_FOMC", "macro_t0_context.fomc",
       frequency="official FOMC statement", asof="official statement available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:FOMC_STATEMENT"),
    _f("macro.fomc_uncertainty", "MACRO_FOMC", "macro_t0_context.fomc",
       frequency="official FOMC statement", asof="official statement available_at <= T0",
       historical="UNAVAILABLE", dependency="macro_release:FOMC_STATEMENT"),

    _f("quality.availability", "DATA_QUALITY", "edge_discovery.feature_view.FeatureValue",
       datatype="category", eligible=False, dependency="data_quality",
       scope="QUALITY_ONLY", notes="never interpreted as a market predictor"),
    _f("quality.staleness", "DATA_QUALITY", "edge_discovery.feature_view.FeatureValue",
       datatype="boolean", eligible=False, dependency="data_quality",
       scope="QUALITY_ONLY", notes="provider outage/staleness never interpreted as market state"),
)


# Pre-result classification of the original v1.1 zero-coverage rows. This is a
# measurement contract, not a conclusion inferred after seeing an edge result.
ZERO_COVERAGE_DIAGNOSIS: dict[str, dict[str, Any]] = {
    **{
        feature_id: {
            "root_cause_class": "A+B",
            "root_cause": "missing EDE mapping; causally computable from retained completed bars",
            "causal_backfill": True,
            "fix": "causal recomputation from bars admitted by bar_end_ts<=T0 and created_ts<=capture record",
            "expected_coverage_after_fix": "all T0 rows whose retained pre-T0 60m bar window is complete",
        }
        for feature_id in (
            "price.trend_efficiency_60", "price.range_60",
            "price.drawdown_60", "price.drawup_60", "regime.trend",
            "regime.volatility",
        )
    },
    **{
        feature_id: {
            "root_cause_class": "A+B",
            "root_cause": "collector stored skew under risk-reversal key rr but EDE did not map it",
            "causal_backfill": True,
            "fix": "read rr from immutable frozen option_distribution and capture it prospectively",
            "expected_coverage_after_fix": "same causal option-snapshot cohort as IV where rr is present",
        }
        for feature_id in (
            "option.skew", "option_dynamics.skew_velocity",
            "option_dynamics.skew_acceleration", "option_dynamics.skew_rolling_rank",
            "option_dynamics.skew_rolling_zscore",
            "option_dynamics.skew_direction_consistency",
        )
    },
    **{
        feature_id: {
            "root_cause_class": "A+B",
            "root_cause": "exact timestamp equality rejected sequential real peer captures",
            "causal_backfill": True,
            "fix": "nearest causal leave-one-out peer join with explicit maximum staleness",
            "expected_coverage_after_fix": "T0 rows with at least one eligible external peer at or before T0",
        }
        for feature_id in (
            "cross.confirmation", "cross.family_breadth", "cross.market_breadth",
            "cross.correlation", "cross.correlation_change",
        )
    },
    "option.barrier_probability": {
        "root_cause_class": "D", "root_cause": "trade-specific G1M state",
        "causal_backfill": False, "fix": "classify G1M_ONLY; do not mix cohorts",
        "expected_coverage_after_fix": "not applicable to G1S",
    },
    "option.rnd_geometry": {
        "root_cause_class": "D", "root_cause": "trade-specific G1M state",
        "causal_backfill": False, "fix": "classify G1M_ONLY; do not mix cohorts",
        "expected_coverage_after_fix": "not applicable to G1S",
    },
    "quality.availability": {
        "root_cause_class": "E", "root_cause": "technical quality dimension, not a market predictor",
        "causal_backfill": False, "fix": "classify QUALITY_ONLY and report as metadata",
        "expected_coverage_after_fix": "not applicable as predictor",
    },
    "quality.staleness": {
        "root_cause_class": "E", "root_cause": "technical quality dimension, not a market predictor",
        "causal_backfill": False, "fix": "classify QUALITY_ONLY and report as metadata",
        "expected_coverage_after_fix": "not applicable as predictor",
    },
}


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
        "research_scope_counts": {
            scope: sum(feature.research_scope == scope for feature in FEATURES)
            for scope in ("G1S", "G1M_ONLY", "QUALITY_ONLY")
        },
        "zero_coverage_diagnosis": ZERO_COVERAGE_DIAGNOSIS,
        "historically_unavailable": unavailable,
        "missing_is_not_zero": True,
        "provider_outage_is_not_market_signal": True,
        "production_authority": False,
        "auto_promotion": False,
    }
