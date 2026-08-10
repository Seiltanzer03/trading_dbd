from __future__ import annotations

from seiltanzer import ai_policy, ai_verdict
from seiltanzer.analytics_runtime import (
    _cross_asset_payload,
    _macro_regime_payload,
    _wavelet_payload,
)
from seiltanzer.engine import Engine
from seiltanzer.ai_verdict_base import _observation
from seiltanzer.metric_contracts import CONTRACTS, metric_contract, validate_contracts


def test_metric_contract_registry_is_complete_and_valid():
    assert len(CONTRACTS) >= 30
    assert validate_contracts() == []
    assert len({row.key for row in CONTRACTS}) == len(CONTRACTS)


def test_option_transforms_are_one_non_independent_family():
    option_rows = [row for row in CONTRACTS if row.family == "option_distribution"]
    assert len(option_rows) >= 12
    assert all(row.independent_vote is False for row in option_rows)
    required = {
        "option.p_take_first_touch",
        "option.p_stop_first_touch",
        "option.p_no_touch",
        "option.barrier_ev_r",
        "option.derivative_state",
        "option.first_touch_hazard",
        "gex.field_force_stiffness",
    }
    assert required <= {row.key for row in option_rows}


def test_policy_outputs_are_joint_distribution_descriptors_not_votes():
    policy_rows = [row for row in CONTRACTS if row.family == "policy_outcome"]
    assert {row.key for row in policy_rows} == {
        "policy.expected_net_r",
        "policy.median_net_r",
        "policy.cvar10_net_r",
        "policy.p_loss",
    }
    assert not any(row.independent_vote for row in policy_rows)


def test_known_audit_defects_cannot_be_silently_relabelled_keep():
    assert metric_contract("option.first_touch_median").disposition == "KEEP"
    assert "legacy median_years remains mixed" in metric_contract(
        "option.first_touch_median").failure_modes
    assert metric_contract("gex.profile").disposition == "VISUAL CONTEXT ONLY"
    assert metric_contract("wavelet.energy").disposition == "VISUAL CONTEXT ONLY"


def test_public_runtime_facades_resolve_to_current_policy_and_verdict():
    assert ai_policy._impl.__name__.endswith("ai_policy_v16")
    assert ai_verdict._impl.__name__.endswith("ai_verdict_v18")
    assert ai_policy.analyze_policies.__module__.endswith("ai_policy_v16")
    assert ai_verdict.render_policy_report.__module__.endswith("ai_verdict_v18")


def test_real_analytics_adapters_replace_synthetic_engine_prototypes():
    # Importing the package installs market-backed adapters.  This guard stops a
    # future refactor from accidentally exposing Engine's old prototype methods.
    assert Engine.macro_regime_payload is _macro_regime_payload
    assert Engine.wavelet_payload is _wavelet_payload
    assert Engine.cross_asset_payload is _cross_asset_payload


def test_ai_observation_does_not_duplicate_canonical_metric_families():
    tick = {
        "prob": {"r": 0.2, "T": 2.5},
        "ladder": {"max_r": 0.4},
        "feeds": {"price": {"value": 101.0}},
    }
    evidence = {
        "option_barrier": {"p_take": 0.2},
        "iv_surface": {"large": list(range(100))},
        "correlation": {"all_pairs": list(range(100))},
    }
    observation = _observation(
        tick, {"evidence": evidence},
        {"entry": 100.0, "stop": 90.0, "take": 125.0},
    )
    assert observation["canonical_metric_paths"]["market_evidence"] == (
        "policy_manager.evidence"
    )
    for duplicate in (
        "option_probability", "probability_cone", "strike_landscape",
        "iv_surface", "correlation", "feed_quality",
    ):
        assert duplicate not in observation
