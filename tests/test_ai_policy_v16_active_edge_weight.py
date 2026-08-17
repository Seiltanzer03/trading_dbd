from __future__ import annotations

from seiltanzer import ai_policy_v16 as policy


def _context(groups, *, strict_n=0, strict_ratio=None):
    return {
        "available": True,
        "matched_groups": groups,
        "matched_strict_reference_signal_n": strict_n,
        "strict_net_position_vote_ratio": strict_ratio,
        "matched_structured_signal_n": sum(int(g.get("matched_n") or 0) for g in groups),
    }


def _group(family, horizon, support, oppose, *, strict_support=0, strict_oppose=0):
    return {
        "target_family": family,
        "signal_horizon_minutes": horizon,
        "matched_n": support + oppose,
        "supporting_n": support,
        "opposing_n": oppose,
        "strict_matched_n": strict_support + strict_oppose,
        "strict_supporting_n": strict_support,
        "strict_opposing_n": strict_oppose,
    }


def _result(base_policy, expected, eligible=None):
    eligible = eligible or list(expected)
    return {
        "recommendation": {
            "policy": base_policy,
            "automatic_execution_allowed": True,
            "working_action_code": base_policy,
        },
        "policies": {
            name: {"expected_final_r": value, "cvar10_r": -0.2}
            for name, value in expected.items()
        },
        "selection_rule": {"eligible": eligible},
        "evidence": {},
        "gate": {
            "automatic_execution_allowed": True,
            "execution_policy": base_policy,
        },
    }


def test_high_risk_only_never_exceeds_fifteen_percent():
    signal = policy._active_edge_signal(_context([
        _group("RETURN", 60, 4, 0),
        _group("PATH_FIRST_TOUCH", 30, 3, 0),
        _group("PATH_EXCURSION", 120, 5, 0),
    ]))
    assert signal["direction"] == "SUPPORTS_POSITION"
    assert signal["weight_cap"] == policy.EDGE_HIGH_RISK_ONLY_MAX
    assert signal["applied_weight"] == policy.EDGE_HIGH_RISK_ONLY_MAX
    assert signal["applied_weight"] <= 0.15


def test_broad_strict_alignment_can_reach_thirty_percent():
    groups = [
        _group("RETURN", 60, 4, 0, strict_support=1),
        _group("PATH_FIRST_TOUCH", 30, 3, 0, strict_support=1),
        _group("PATH_EXCURSION", 120, 5, 0, strict_support=1),
    ]
    signal = policy._active_edge_signal(
        _context(groups, strict_n=3, strict_ratio=1.0))
    assert signal["weight_cap"] == policy.EDGE_STRICT_BROAD_MAX
    assert signal["applied_weight"] == 0.30
    assert signal["aligned_strict_group_n"] == 3


def test_support_can_only_move_toward_less_intervention():
    result = _result("CLOSE_50", {
        "HOLD": 0.1190,
        "CLOSE_10": 0.1192,
        "CLOSE_25": 0.1195,
        "CLOSE_50": 0.1200,
        "EXIT": 0.0,
    })
    blend = policy._blend_policy_scores(result, {
        "direction": "SUPPORTS_POSITION",
        "applied_weight": 0.30,
    })
    assert blend["changed"] is True
    assert policy._POLICY_FRACTIONS[blend["adjusted_policy"]] <= 0.50


def test_oppose_can_only_move_toward_more_intervention():
    result = _result("HOLD", {
        "HOLD": 0.1200,
        "CLOSE_10": 0.1198,
        "CLOSE_25": 0.1195,
        "CLOSE_50": 0.1190,
        "EXIT": 0.0,
    })
    blend = policy._blend_policy_scores(result, {
        "direction": "OPPOSES_POSITION",
        "applied_weight": 0.30,
    })
    assert blend["changed"] is True
    assert policy._POLICY_FRACTIONS[blend["adjusted_policy"]] > 0.0


def test_edge_never_selects_outside_hard_cvar_eligible_set():
    result = _result("HOLD", {
        "HOLD": 0.10,
        "CLOSE_25": 0.099,
        "CLOSE_50": 0.20,
        "EXIT": 0.30,
    }, eligible=["HOLD", "CLOSE_25"])
    blend = policy._blend_policy_scores(result, {
        "direction": "OPPOSES_POSITION",
        "applied_weight": 0.30,
    })
    assert blend["adjusted_policy"] in {"HOLD", "CLOSE_25"}
    assert "CLOSE_50" not in blend["policy_scores"]
    assert "EXIT" not in blend["policy_scores"]


def test_edge_adjustment_never_adds_automatic_execution_authority():
    result = _result("HOLD", {
        "HOLD": 0.1200,
        "CLOSE_10": 0.1198,
        "CLOSE_25": 0.1195,
        "CLOSE_50": 0.1190,
        "EXIT": 0.0,
    })
    context = _context([
        _group("RETURN", 60, 0, 4, strict_oppose=1),
        _group("PATH_FIRST_TOUCH", 30, 0, 3, strict_oppose=1),
        _group("PATH_EXCURSION", 120, 0, 5, strict_oppose=1),
    ], strict_n=3, strict_ratio=-1.0)
    adjusted = policy._apply_active_edge_policy(result, context)
    block = adjusted["active_edge_policy_weight"]
    assert block["blend"]["changed"] is True
    assert block["blend"]["applied_weight"] == 0.30
    assert adjusted["recommendation"]["automatic_execution_allowed"] is False
    assert adjusted["gate"]["automatic_execution_allowed"] is False
    assert adjusted["gate"]["execution_policy"] is None
    assert block["hard_risk_override_allowed"] is False
