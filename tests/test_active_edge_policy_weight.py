from __future__ import annotations

from copy import deepcopy

from seiltanzer.active_edge_policy_weight import (
    MAX_EDGE_WEIGHT,
    HIGH_RISK_ONLY_CAP,
    adjust_metrics_for_edge,
    edge_weight_profile,
)
from seiltanzer.ai_policy_base import POLICY_FRACTIONS


def _context(*, strict: bool, ratios: tuple[float, ...] = (1.0, 1.0)) -> dict:
    supporting = len(ratios)
    strict_n = supporting if strict else 0
    return {
        "supporting_position_n": supporting,
        "opposing_position_n": 0,
        "strict_supporting_position_n": strict_n,
        "strict_opposing_position_n": 0,
        "matched_groups": [
            {
                "target_family": "RETURN" if index == 0 else "PATH_FIRST_TOUCH",
                "signal_horizon_minutes": 60 if index == 0 else 120,
                "net_vote": 1 if ratio > 0 else -1,
                "net_vote_ratio": ratio,
            }
            for index, ratio in enumerate(ratios)
        ],
    }


def test_high_risk_only_is_capped_at_thirty_percent():
    profile = edge_weight_profile(_context(strict=False))
    assert profile["available"] is True
    assert profile["weight_fraction"] == HIGH_RISK_ONLY_CAP
    assert profile["max_weight_fraction"] == HIGH_RISK_ONLY_CAP
    assert profile["preferred_close_fraction"] == 0.0


def test_strict_unanimous_edge_can_reach_forty_percent():
    profile = edge_weight_profile(_context(strict=True))
    assert profile["available"] is True
    assert profile["weight_fraction"] == MAX_EDGE_WEIGHT
    assert profile["max_weight_fraction"] == MAX_EDGE_WEIGHT


def test_disagreement_reduces_effective_weight_to_zero():
    profile = edge_weight_profile(_context(strict=True, ratios=(1.0, -1.0)))
    assert profile["available"] is False
    assert profile["weight_fraction"] == 0.0


def test_soft_weight_can_change_expected_r_ranking_without_touching_cvar():
    metrics = {
        "HOLD": {"name": "HOLD", "expected_final_r": 0.10, "cvar10_r": -0.20},
        "CLOSE_10": {"name": "CLOSE_10", "expected_final_r": 0.105, "cvar10_r": -0.18},
        "CLOSE_25": {"name": "CLOSE_25", "expected_final_r": 0.11, "cvar10_r": -0.15},
        "CLOSE_50": {"name": "CLOSE_50", "expected_final_r": 0.115, "cvar10_r": -0.10},
        "EXIT": {"name": "EXIT", "expected_final_r": 0.12, "cvar10_r": 0.00},
    }
    original = deepcopy(metrics)
    profile = edge_weight_profile(_context(strict=True))
    adjusted, audit = adjust_metrics_for_edge(
        metrics, profile, 0.0, cvar_floor=-0.50,
        policy_fractions=POLICY_FRACTIONS,
    )
    assert audit["applied"] is True
    assert audit["hard_risk_modified"] is False
    assert adjusted["HOLD"]["expected_final_r"] > adjusted["EXIT"]["expected_final_r"]
    for name in metrics:
        assert adjusted[name]["cvar10_r"] == original[name]["cvar10_r"]
        assert metrics[name] == original[name]


def test_hard_risk_ineligible_policy_never_receives_soft_adjustment():
    metrics = {
        "HOLD": {"name": "HOLD", "expected_final_r": 0.10, "cvar10_r": -0.20},
        "EXIT": {"name": "EXIT", "expected_final_r": 0.50, "cvar10_r": -1.20},
    }
    profile = {
        "available": True,
        "weight_fraction": 0.40,
        "max_weight_fraction": 0.40,
        "direction_score": -1.0,
        "preferred_close_fraction": 1.0,
        "independent_bucket_n": 2,
        "strict_directional_share": 1.0,
    }
    adjusted, audit = adjust_metrics_for_edge(
        metrics, profile, 0.0, cvar_floor=-0.50,
        policy_fractions=POLICY_FRACTIONS,
    )
    assert audit["applied"] is False
    assert audit["reason"] == "FEWER_THAN_TWO_HARD_RISK_ELIGIBLE_POLICIES"
    assert adjusted is metrics
