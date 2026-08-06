import numpy as np

from seiltanzer.config import INSTRUMENTS
from seiltanzer.core.options import RNDensity, _trapezoid
from seiltanzer.data import adaptive_chain as adaptive
from seiltanzer.data.feeds import MarketData


def _candidate(expiry, low, high, ordinal):
    return adaptive.ChainCandidate(
        expiry=expiry,
        metrics={"density": {"strikes": [low, 1.0, high]}},
        support_low_ratio=low,
        support_high_ratio=high,
        ordinal=ordinal,
    )


def test_shortest_expiry_covering_broad_moneyness_is_selected():
    candidates = [
        _candidate("near", 0.90, 1.10, 0),
        _candidate("middle", 0.77, 1.23, 1),
        _candidate("far", 0.70, 1.30, 2),
    ]
    assert adaptive.select_candidate(candidates).expiry == "middle"


def test_widest_balanced_expiry_is_used_when_none_fully_covers():
    candidates = [
        _candidate("near", 0.92, 1.15, 0),
        _candidate("down_only", 0.70, 1.08, 1),
        _candidate("balanced", 0.84, 1.18, 2),
    ]
    assert adaptive.select_candidate(candidates).expiry == "balanced"


def test_missing_bl_tails_are_extrapolated_for_real_proxy_mapping():
    strikes = np.linspace(90.0, 110.0, 41)
    q = np.exp(-0.5 * ((strikes - 100.0) / 4.0) ** 2)
    area = _trapezoid(q, strikes)
    density = RNDensity(strikes=strikes, density=q / area, t_years=14 / 365)
    setattr(density, "_allow_proxy_tail_extrapolation", True)

    result = adaptive.market_r_distribution(
        density,
        scale=1.0,
        entry=100.0,
        stop=75.0,
        take=130.0,
        direction="long",
        T=1.2,
    )

    assert result["hit_ratio"] is not None
    assert 0.0 <= result["hit_ratio"] <= 1.0
    assert result["tail_anchor_supported"] is True
    assert result["barriers_supported"] is True
    assert result["tail_extrapolated"] is True
    assert result["tail_method"] == "moment_matched_lognormal"
    assert result["observed_barriers_supported"] is False
    assert abs(sum(result["probs"]) - 1.0) < 1e-9


def test_unmarked_density_retains_strict_original_contract():
    strikes = np.linspace(90.0, 110.0, 41)
    q = np.exp(-0.5 * ((strikes - 100.0) / 4.0) ** 2)
    density = RNDensity(
        strikes=strikes,
        density=q / _trapezoid(q, strikes),
        t_years=14 / 365,
    )
    result = adaptive.market_r_distribution(
        density, 1.0, 100.0, 75.0, 130.0, "long", 1.2
    )
    assert result["hit_ratio"] is None
    assert result["barriers_supported"] is False
    assert result["tail_extrapolated"] is False


def test_empirical_bl_support_is_left_untouched_when_sufficient():
    strikes = np.linspace(70.0, 135.0, 131)
    q = np.exp(-0.5 * ((strikes - 100.0) / 12.0) ** 2)
    area = _trapezoid(q, strikes)
    density = RNDensity(strikes=strikes, density=q / area, t_years=30 / 365)

    result = adaptive.market_r_distribution(
        density,
        scale=1.0,
        entry=100.0,
        stop=85.0,
        take=120.0,
        direction="long",
        T=4 / 3,
    )

    assert result["hit_ratio"] is not None
    assert result["tail_extrapolated"] is False
    assert result["tail_method"] == "observed_bl_support"


def test_real_proxy_mapping_is_marked_but_demo_same_scale_is_not():
    strikes = np.linspace(80.0, 120.0, 41)
    q = np.exp(-0.5 * ((strikes - 100.0) / 8.0) ** 2)
    density = RNDensity(
        strikes=strikes,
        density=q / _trapezoid(q, strikes),
        t_years=14 / 365,
    )
    real = adaptive.map_proxy_density(density, proxy_spot=100.0,
                                      instrument_spot=30000.0)
    demo = adaptive.map_proxy_density(density, proxy_spot=100.0,
                                      instrument_spot=100.0)
    assert getattr(real, "_allow_proxy_tail_extrapolation", False) is True
    assert getattr(demo, "_allow_proxy_tail_extrapolation", False) is False


def test_proxy_pipeline_is_installed_for_expected_instruments():
    assert INSTRUMENTS["NAS100"].options_proxy == "QQQ"
    assert INSTRUMENTS["UK100"].options_proxy == "EWU"
    assert INSTRUMENTS["GER40"].options_proxy == "EWG"
    assert INSTRUMENTS["USDCAD"].options_proxy == "FXC"

    real_jpy = object.__new__(MarketData)
    real_jpy.instrument_code = "JPY100"
    real_jpy.demo = False
    demo_jpy = object.__new__(MarketData)
    demo_jpy.instrument_code = "JPY100"
    demo_jpy.demo = True
    assert real_jpy.instrument.options_proxy == "EWJ"
    assert real_jpy.instrument.proxy_experimental is True
    assert demo_jpy.instrument.options_proxy is None
    assert MarketData.refresh_chain.__module__ == "seiltanzer.data.adaptive_chain"
