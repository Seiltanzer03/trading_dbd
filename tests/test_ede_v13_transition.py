from __future__ import annotations

import json
import sqlite3
import threading

from seiltanzer.edge_discovery.transition_search import (
    MAX_TRANSITION_CONDITIONS,
    MAX_TRANSITION_TEMPLATES,
    TRANSITION_FEATURES,
    augment_rows_from_frozen_v3,
    transition_templates,
)


class Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute(
            "CREATE TABLE g1s_observations ("
            "observation_id TEXT PRIMARY KEY,captured_ts REAL,horizon_minutes INTEGER,"
            "frozen_features_json TEXT)"
        )


def _frozen(source_ts: float) -> str:
    quality = {
        "available": True, "stale": False, "source_ts": source_ts,
        "source_quality": 0.9,
    }
    return json.dumps({
        "g1s_evidence_v3": {
            "macro": {
                "available": True, "boundary_distance": 0.21,
                "transition_velocity": 1.2, "transition_acceleration": 0.7,
                "x": 0.8, "y": -0.2, "z": 0.4, "quality": quality,
            },
            "wavelet": {
                "available": True, "phase_stability": 0.82,
                "spectral_concentration": 0.41, "persistence": 0.72,
                "ridge_velocity_log_per_hour": -0.13,
                "ridge_power_slope_log_per_hour": -0.08,
                "dominant_period_hours": 4.0, "cycle_shift": "STABLE",
                "energy_transfer": {"rate_pp_per_30m": 5.5},
                "quality": quality,
            },
        }
    })


def _row(observation_id: str, captured_ts: float = 100.0) -> dict:
    # Canonical regime.wavelet_phase is already materialized by the base adapter
    # and represents numeric phase_stability after PR #100.
    return {
        "observation_id": observation_id, "instrument": "NAS100",
        "captured_ts": captured_ts, "target_ts": captured_ts + 900,
        "horizon_minutes": 15, "outcome_available": True,
        "ede_features": {"regime.wavelet_phase": 0.82}, "feature_values": {},
    }


def test_transition_features_are_read_from_existing_frozen_t0_only():
    runtime = Runtime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES (?,?,?,?)",
        ("ok", 100.0, 15, _frozen(99.0)),
    )
    rows = [_row("ok")]
    coverage = augment_rows_from_frozen_v3(runtime, rows)
    ede = rows[0]["ede_features"]
    assert ede["regime.macro_transition_velocity"] == 1.2
    assert ede["regime.wavelet_spectral_concentration"] == 0.41
    assert ede["regime.wavelet_cycle_shift"] == "STABLE"
    assert ede["regime.wavelet_phase"] == 0.82
    assert "regime.wavelet_phase_stability" not in TRANSITION_FEATURES
    assert rows[0]["feature_values"]["regime.wavelet_spectral_concentration"]["future_points_used"] is False
    assert coverage["synthetic_history_used"] is False
    assert coverage["retrospective_reconstruction_used"] is False
    assert coverage["canonical_wavelet_phase_reused"] == "regime.wavelet_phase"


def test_future_source_timestamp_is_rejected_not_backfilled():
    runtime = Runtime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES (?,?,?,?)",
        ("future", 100.0, 15, _frozen(101.0)),
    )
    rows = [_row("future")]
    augment_rows_from_frozen_v3(runtime, rows)
    assert "regime.macro_transition_velocity" not in rows[0]["ede_features"]
    assert "regime.wavelet_spectral_concentration" not in rows[0]["ede_features"]
    # Existing canonical T0 value is not deleted or rewritten by this sub-audit.
    assert rows[0]["ede_features"]["regime.wavelet_phase"] == 0.82


def test_transition_search_space_is_predeclared_and_bounded():
    available = set(TRANSITION_FEATURES) | {
        "regime.wavelet_phase", "option_dynamics.gex_velocity",
        "option_dynamics.iv_velocity", "option.iv_rv_ratio",
        "cross.confirmation", "regime.trend",
    }
    templates = transition_templates(available)
    assert 0 < len(templates) <= MAX_TRANSITION_TEMPLATES
    assert max(template.complexity for template in templates) <= MAX_TRANSITION_CONDITIONS
    ids = {condition.feature_id for template in templates for condition in template.conditions}
    assert "regime.macro_boundary_distance" in ids
    assert "regime.macro_transition_velocity" in ids
    assert "regime.wavelet_phase" in ids
    assert "regime.wavelet_ridge_velocity" in ids


def test_no_duplicate_wavelet_phase_feature_is_created():
    assert "regime.wavelet_phase_stability" not in TRANSITION_FEATURES
    assert TRANSITION_FEATURES["regime.wavelet_cycle_shift"]["datatype"] == "category"
