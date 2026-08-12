from __future__ import annotations

from seiltanzer.g1_short_horizon_probability_selection import (
    PROBABILITY_SELECTION_VERSION,
    select_probability_representation,
)


def _item(*, raw_brier, raw_log, cal_brier, cal_log, blockers=None):
    return {
        "raw_brier": raw_brier,
        "raw_log_loss": raw_log,
        "calibrated_brier": cal_brier,
        "calibrated_log_loss": cal_log,
        "candidate_blockers": blockers or [],
        "baselines": {
            "constant_0_5": {"brier": 0.25, "log_loss": 0.693},
            "momentum": {"brier": 0.24, "log_loss": 0.68},
        },
    }


def test_already_good_raw_model_is_not_failed_when_platt_adds_no_value():
    result = select_probability_representation(
        _item(raw_brier=0.20, raw_log=0.60, cal_brier=0.205, cal_log=0.61)
    )
    assert result["contract_version"] == PROBABILITY_SELECTION_VERSION
    assert result["verdict"] == "YES"
    assert result["selected_representation"] == "RAW"
    assert result["raw_beats_causal_baselines"] is True
    assert result["calibration_value_added"] == "NO"


def test_calibrated_representation_can_rescue_raw_probability_failure():
    result = select_probability_representation(
        _item(raw_brier=0.26, raw_log=0.70, cal_brier=0.21, cal_log=0.62)
    )
    assert result["verdict"] == "YES"
    assert result["selected_representation"] == "CALIBRATED"
    assert result["calibrated_beats_causal_baselines"] is True
    assert result["calibration_value_added"] == "YES"


def test_neither_probability_representation_beating_baselines_is_no_after_maturity():
    result = select_probability_representation(
        _item(raw_brier=0.26, raw_log=0.70, cal_brier=0.255, cal_log=0.695)
    )
    assert result["verdict"] == "NO"
    assert result["selected_representation"] is None


def test_probability_representation_selection_fails_closed_before_serious_oos_gate():
    result = select_probability_representation(
        _item(
            raw_brier=0.20,
            raw_log=0.60,
            cal_brier=0.19,
            cal_log=0.58,
            blockers=["INSUFFICIENT_EFFECTIVE_N"],
        )
    )
    assert result["verdict"] == "INSUFFICIENT"
    assert result["selected_representation"] is None
    assert result["calibration_value_added"] == "INSUFFICIENT"
