from __future__ import annotations

import pytest

from seiltanzer.ai_report_semantics_guard import repair_snapshot_geometry


def test_missing_current_r_does_not_publish_fake_barrier_or_take_distances():
    snapshot = {
        "trade_geometry": {
            "current": None,
            "entry": 4393.0,
            "original_stop": 4380.0,
            "active_risk_barrier": 4393.0,
            "active_risk_barrier_type": "BREAK_EVEN",
            "final_take": 4410.0,
            "current_r": None,
            # Regression observed immediately after production restart.
            "r_to_active_stop": 1.0,
            "r_to_final_take": 0.0,
        }
    }
    repair_snapshot_geometry(snapshot)
    geometry = snapshot["trade_geometry"]
    assert geometry["current_r"] is None
    assert geometry["r_to_active_stop"] is None
    assert geometry["r_to_final_take"] is None


def test_current_price_can_reconstruct_all_r_geometry_from_original_risk():
    snapshot = {
        "trade_geometry": {
            "current": 4405.55,
            "entry": 4393.0,
            "original_stop": 4380.0,
            "active_risk_barrier": 4393.0,
            "final_take": 4410.0,
            "current_r": None,
            "r_to_active_stop": 99.0,
            "r_to_final_take": 99.0,
        }
    }
    repair_snapshot_geometry(snapshot)
    geometry = snapshot["trade_geometry"]
    assert geometry["current_r"] == pytest.approx((4405.55 - 4393.0) / 13.0, abs=1e-6)
    assert geometry["r_to_active_stop"] == pytest.approx((4405.55 - 4393.0) / 13.0, abs=1e-4)
    assert geometry["r_to_final_take"] == pytest.approx((4410.0 - 4405.55) / 13.0, abs=1e-4)
