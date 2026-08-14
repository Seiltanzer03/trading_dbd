from __future__ import annotations

from seiltanzer.edge_discovery.stratified import (
    MIN_STRATUM_EFFECTIVE,
    MIN_STRATUM_RAW,
    _asset_stability,
    _contexts,
    _score,
)


def _records(n: int = 20, *, good: bool = True):
    output = []
    for index in range(n):
        up = index % 2 == 0
        row = {
            "observation_id": f"obs-{index}",
            "instrument": "NAS100" if index < n // 2 else "SP500",
            "captured_ts": float(index * 900),
            "target_ts": float((index + 1) * 900),
            "horizon_minutes": 15,
            "direction_label": "UP" if up else "DOWN",
            "terminal_log_return": 0.01 if up else -0.01,
            "ede_features": {"regime.session_utc": "US"},
        }
        if good:
            candidate_probability = 0.8 if up else 0.2
        else:
            candidate_probability = 0.2 if up else 0.8
        output.append({
            "row": row,
            "candidate_probability": candidate_probability,
            "baseline_probability": 0.5,
        })
    return output


def test_stratum_score_marks_joint_positive_only_after_descriptive_gate():
    score = _score(_records(), 20)
    assert score["raw_n"] == MIN_STRATUM_RAW
    assert score["effective_n"] >= MIN_STRATUM_EFFECTIVE
    assert score["descriptive_ready"] is True
    assert score["joint_positive"] is True
    assert score["joint_negative"] is False
    assert score["delta_brier"] > 0
    assert score["delta_logloss"] > 0
    assert score["selection_use"] is False
    assert score["edge_maturity_use"] is False


def test_cross_instrument_stability_is_descriptive_not_edge_gate():
    rows = [
        {"descriptive_ready": True, "joint_positive": True, "joint_negative": False},
        {"descriptive_ready": True, "joint_positive": True, "joint_negative": False},
        {"descriptive_ready": True, "joint_positive": False, "joint_negative": True},
    ]
    result = _asset_stability(rows)
    assert result["status"] == "BROAD_POSITIVE"
    assert result["positive_share"] == 2 / 3
    assert result["selection_use"] is False
    assert result["edge_maturity_use"] is False


def test_where_it_hurts_excludes_neutral_strata():
    candidate = {
        "candidate_id": "c1", "hypothesis_id": "h1", "horizon_minutes": 30,
        "template": [{"feature_id": "option.iv"}],
        "edge_maturity": "INSUFFICIENT_DATA",
        "stratified_diagnostics": {"dimensions": {"instrument": [
            {
                "dimension": "instrument", "value": "NAS100",
                "descriptive_ready": True, "joint_positive": False,
                "joint_negative": False, "delta_brier": 0.001,
                "delta_logloss": -0.001,
            },
            {
                "dimension": "instrument", "value": "SP500",
                "descriptive_ready": True, "joint_positive": False,
                "joint_negative": True, "delta_brier": -0.01,
                "delta_logloss": -0.02,
            },
        ]}},
    }
    report = {"horizons": [{"candidates": [candidate]}]}
    hurts = _contexts(report, False)
    assert [row["value"] for row in hurts] == ["SP500"]
