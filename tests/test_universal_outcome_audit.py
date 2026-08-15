from __future__ import annotations

from scripts.run_universal_outcome_audit import _summarize


def test_universal_outcome_audit_keeps_clean_and_ambiguous_labels_separate():
    rows = [
        {
            "horizon_minutes": 30,
            "outcome_available": True,
            "universal_outcome_reason": None,
            "universal_outcome": {
                "available": True,
                "path_complete": True,
                "barriers": {
                    "up_1s_down_1s": {"label": "UP_FIRST", "clean_label": True},
                },
            },
        },
        {
            "horizon_minutes": 30,
            "outcome_available": True,
            "universal_outcome_reason": None,
            "universal_outcome": {
                "available": True,
                "path_complete": True,
                "barriers": {
                    "up_1s_down_1s": {
                        "label": "AMBIGUOUS_SAME_BAR", "clean_label": False,
                    },
                },
            },
        },
    ]
    report = _summarize(rows)
    horizon = report["by_horizon"]["30"]
    assert horizon["universal_outcome_available"] == 2
    assert horizon["all_barrier_labels"]["up_1s_down_1s:AMBIGUOUS_SAME_BAR"] == 1
    assert horizon["all_barrier_labels"]["up_1s_down_1s:UP_FIRST"] == 1
    assert "up_1s_down_1s:AMBIGUOUS_SAME_BAR" not in horizon["clean_barrier_labels"]
    assert horizon["clean_barrier_labels"]["up_1s_down_1s:UP_FIRST"] == 1
    assert report["strategy_agnostic"] is True
    assert report["production_authority"] is False
