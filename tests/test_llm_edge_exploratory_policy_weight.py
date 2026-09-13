from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from seiltanzer.active_edge_policy_weight import adjust_metrics_for_edge
from seiltanzer.ai_policy_base import POLICY_FRACTIONS
from seiltanzer.llm_edge_exploratory_policy_weight import (
    MAX_EXPLORATORY_WEIGHT,
    combine_weight_profiles,
    exploratory_weight_profile,
)


class Integration:
    @staticmethod
    def _current_values(engine, snapshot):
        return engine.values

    @staticmethod
    def _conditions_match(values, candidate):
        return all(
            values.get(row.get("feature_id"), {}).get("value") == row.get("state")
            for row in candidate.get("conditions") or []
        )

    @staticmethod
    def _bias(candidate):
        shift = candidate.get("prediction_shift") or {}
        interpretation = shift.get("interpretation")
        if interpretation in {"MORE_UP", "MORE_UPSIDE_RETURN", "MORE_UPSIDE_EXCURSION"}:
            return "BULLISH"
        if interpretation in {"MORE_DOWN", "MORE_DOWNSIDE_RETURN", "LESS_UPSIDE_EXCURSION"}:
            return "BEARISH"
        strongest = shift.get("strongest_class")
        if strongest == "UP_FIRST":
            return "BULLISH"
        if strongest == "DOWN_FIRST":
            return "BEARISH"
        return "NON_DIRECTIONAL"

    @staticmethod
    def _relation(direction, bias):
        supports = (
            direction.lower() in {"long", "buy"} and bias == "BULLISH"
        ) or (
            direction.lower() in {"short", "sell"} and bias == "BEARISH"
        )
        return "SUPPORTS_POSITION" if supports else (
            "OPPOSES_POSITION" if bias in {"BULLISH", "BEARISH"} else "NON_DIRECTIONAL"
        )

    @staticmethod
    def _target_family(target):
        return "DIRECTION" if target == "DIRECTION" else "RETURN"


def _hypothesis(
    hypothesis_id: str, *, status: str = "EARLY_ADVANTAGE",
    confidence: str = "LIMITED", shift=0.2, state: str = "EXPANDING",
):
    return {
        "hypothesis_id": hypothesis_id,
        "target": "RETURN_SIGMA",
        "horizon_minutes": 30,
        "exploratory_verdict": {
            "status": status,
            "confidence": confidence,
            "target_id": "RETURN_SIGMA",
            "horizon_minutes": 30,
            "condition_count": 3,
            "deployment_rule": [{
                "feature_id": "regime.volatility",
                "kind": "categorical",
                "state": state,
            }],
            "prediction_shift": shift,
            "selected_test_n": 48,
            "evaluated_fold_count": 4,
            "primary_improvement": 0.02,
        },
    }


def _engine(rows):
    runtime = SimpleNamespace(_llm_edge_lifecycle_payload_json=json.dumps({
        "status": "OK", "research_hypotheses": rows,
    }))
    return SimpleNamespace(
        short_horizon=runtime,
        settings=SimpleNamespace(ede_context_max_age_sec=900.0),
        values={"regime.volatility": {
            "value": "EXPANDING", "available": True,
            "live_applicability": "LIVE_APPLICABLE", "stale": False,
            "asof": 1_999_999_950.0,
        }},
    )


def _snapshot(direction="long"):
    return {"captured_ts": 2_000_000_000.0, "strategy": {
        "instrument": "EURUSD", "direction": direction,
    }}


def test_current_match_gets_fifteen_percent_across_all_existing_actions():
    profile = exploratory_weight_profile(
        _engine([_hypothesis("up")]), _snapshot(), Integration,
    )
    assert profile["available"] is True
    assert profile["weight_fraction"] == MAX_EXPLORATORY_WEIGHT == 0.15
    assert profile["preferred_close_fraction"] == 0.0
    assert profile["eligible_policies"] == [
        "HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT",
    ]
    assert profile["hard_risk_override"] is False
    assert profile["automatic_execution_source"] is False


def test_very_low_and_nonmatching_hypotheses_have_zero_weight():
    profile = exploratory_weight_profile(
        _engine([
            _hypothesis("weak", confidence="VERY_LOW"),
            _hypothesis("other-regime", state="CONTRACTING"),
        ]),
        _snapshot(),
        Integration,
    )
    assert profile["available"] is False
    assert profile["weight_fraction"] == 0.0
    assert profile["reason"] == "NO_MATCHED_LIMITED_DIRECTIONAL_ADVANTAGES"


def test_stale_current_feature_cannot_receive_weight():
    engine = _engine([_hypothesis("up")])
    engine.values["regime.volatility"]["asof"] = 1_999_000_000.0
    profile = exploratory_weight_profile(engine, _snapshot(), Integration)
    assert profile["available"] is False
    assert profile["weight_fraction"] == 0.0


def test_negative_and_mixed_matches_reduce_but_do_not_invert_advantage():
    profile = exploratory_weight_profile(
        _engine([
            _hypothesis("advantage"),
            _hypothesis("negative", status="EARLY_DISADVANTAGE", shift=-0.2),
            _hypothesis("mixed", status="EARLY_MIXED", shift=-0.2),
        ]),
        _snapshot(),
        Integration,
    )
    assert profile["available"] is True
    assert profile["weight_fraction"] == 0.05
    assert profile["direction_score"] == round(1 / 3, 6)
    assert profile["matched_uncertainty_n"] == 2


def test_combined_profile_keeps_total_cap_and_preserves_early_component():
    active = {
        "available": True, "weight_fraction": 0.40,
        "direction_score": 1.0, "preferred_close_fraction": 0.0,
    }
    early = {
        "available": True, "weight_fraction": 0.15,
        "direction_score": -1.0, "contract_version": "early-v1",
    }
    combined = combine_weight_profiles(active, early, absolute_cap=0.40)
    assert combined["weight_fraction"] == 0.40
    assert combined["exploratory_component_weight"] == 0.15
    assert combined["direction_score"] == round(0.25 / 0.55, 6)
    assert combined["preferred_close_fraction"] > 0.0


def test_early_weight_changes_soft_ranking_but_not_hard_risk_fields():
    metrics = {
        "HOLD": {"expected_final_r": 0.10, "cvar10_r": -0.20},
        "CLOSE_10": {"expected_final_r": 0.101, "cvar10_r": -0.18},
        "CLOSE_25": {"expected_final_r": 0.102, "cvar10_r": -0.15},
        "CLOSE_50": {"expected_final_r": 0.103, "cvar10_r": -0.10},
        "EXIT": {"expected_final_r": 0.104, "cvar10_r": 0.00},
    }
    original = deepcopy(metrics)
    profile = exploratory_weight_profile(
        _engine([_hypothesis("against", shift=-0.2)]), _snapshot(), Integration,
    )
    adjusted, audit = adjust_metrics_for_edge(
        metrics, profile, 0.0, cvar_floor=-0.50,
        policy_fractions=POLICY_FRACTIONS,
    )
    assert audit["applied"] is True
    assert adjusted["EXIT"]["expected_final_r"] > adjusted["HOLD"]["expected_final_r"]
    for name in metrics:
        assert adjusted[name]["cvar10_r"] == original[name]["cvar10_r"]
        assert metrics[name] == original[name]
