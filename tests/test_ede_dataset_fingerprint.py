import math

import pytest

from seiltanzer.edge_discovery.dataset_fingerprint import (
    DATASET_FINGERPRINT_CONTRACT_VERSION,
    research_dataset_fingerprint,
)


def _row():
    return {
        "observation_id": "obs-1",
        "instrument": "NAS100",
        "captured_ts": 1000.0,
        "target_ts": 1900.0,
        "resolved_ts": 1901.0,
        "horizon_minutes": 15,
        "direction_label": "UP",
        "terminal_log_return": 0.01,
        "mfe_log_return": 0.02,
        "mae_log_return": -0.003,
        "features": {"ret_5m": 0.002, "ret_15m": 0.004},
        "ede_features": {"option.iv": 0.23, "vol.rv15_over_rv60": 1.1},
        "rejected_feature_ids": [],
        "prospective_adapter_version": "adapter-v-test",
        "retrospective_options_reconstruction": False,
        # Deliberately transient and therefore excluded from the fingerprint.
        "outcome_available_asof": 99999.0,
    }


def _fingerprint(row, eligible=("option.iv", "vol.rv15_over_rv60")):
    return research_dataset_fingerprint([row], eligible_feature_ids=eligible)


def test_fingerprint_v2_changes_when_research_content_changes():
    base = _row()
    original = _fingerprint(base)

    outcome_changed = _row()
    outcome_changed["terminal_log_return"] = 0.011
    assert _fingerprint(outcome_changed) != original

    feature_changed = _row()
    feature_changed["ede_features"]["option.iv"] = 0.24
    assert _fingerprint(feature_changed) != original

    baseline_changed = _row()
    baseline_changed["features"]["ret_5m"] = 0.003
    assert _fingerprint(baseline_changed) != original

    assert _fingerprint(base, eligible=("option.iv",)) != original


def test_fingerprint_v2_is_deterministic_for_same_research_inputs():
    left = _row()
    right = _row()
    right["outcome_available_asof"] = 123456789.0
    right["ede_features"] = {
        "vol.rv15_over_rv60": 1.1,
        "option.iv": 0.23,
    }
    assert _fingerprint(left) == _fingerprint(right)
    assert DATASET_FINGERPRINT_CONTRACT_VERSION.endswith("-v2")


def test_fingerprint_v2_fails_closed_on_non_finite_research_values():
    row = _row()
    row["ede_features"]["option.iv"] = math.nan
    with pytest.raises(ValueError, match="non-finite"):
        _fingerprint(row)
