from __future__ import annotations

import sqlite3
import threading

import pytest

from seiltanzer.g1_short_horizon_calibration import (
    CALIBRATION_FIT_REQUIRED,
    _apply_calibrators,
    _ensure_tables,
    _fit_platt,
)


class _Runtime:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE g1s_shadow_predictions(
                prediction_id TEXT PRIMARY KEY, observation_id TEXT, model_id TEXT,
                created_ts REAL, p_up REAL, production_used INTEGER
            );
            CREATE TABLE g1s_models(
                model_id TEXT PRIMARY KEY, horizon_minutes INTEGER, feature_set TEXT,
                model_family TEXT, created_ts REAL, training_cutoff_ts REAL
            );
        """)
        _ensure_tables(self)

    @staticmethod
    def _dependency_key(row):
        horizon = int(row["horizon_minutes"])
        bucket = int(float(row["captured_ts"]) // (horizon*60.0))
        return f"{row['instrument']}|{horizon}|{bucket}"


def test_platt_fit_requirements_are_deliberately_stricter_than_initial_model_fit():
    assert CALIBRATION_FIT_REQUIRED == {
        "raw_resolved": 240,
        "effective_n": 120,
        "positive_n": 60,
        "negative_n": 60,
        "temporal_blocks": 5,
    }


def test_platt_solver_maps_better_raw_probabilities_monotonically(tmp_path):
    runtime = _Runtime(tmp_path / "platt.sqlite3")
    rows = []
    for index in range(160):
        up = index % 2 == 0
        rows.append({
            "instrument": "NAS100", "horizon_minutes": 15,
            "captured_ts": 10_000.0+index*1000.0,
            "p_up": 0.75 if up else 0.25,
            "direction_label": "UP" if up else "DOWN",
        })
    intercept, slope = _fit_platt(runtime, rows)
    assert abs(intercept) < 1.0
    assert slope > 0.0


def test_calibrator_created_after_t0_is_never_applied(tmp_path):
    runtime = _Runtime(tmp_path / "causal.sqlite3")
    c = runtime._conn
    c.execute("INSERT INTO g1s_models VALUES(?,?,?,?,?,?)",
              ("m1", 15, "PRICE", "REGULARIZED_LOGISTIC", 50.0, 40.0))
    c.execute("INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?,?)",
              ("p1", "o1", "m1", 101.0, 0.7, 0))
    c.execute(
        "INSERT INTO g1s_probability_calibrators VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("late", 15, "PRICE", "REGULARIZED_LOGISTIC", 90.0, 240, 120.0, 120, 120, 5,
         '{"platt_intercept":0.0,"platt_slope":1.0}', "{}", "a"*64,
         "research_only", 300.0),
    )
    c.commit()
    assert _apply_calibrators(runtime, "o1", 100.0, 15) == 0
    assert c.execute("SELECT COUNT(*) FROM g1s_calibrated_predictions").fetchone()[0] == 0


def test_pre_t0_calibrator_creates_immutable_future_only_prediction(tmp_path):
    runtime = _Runtime(tmp_path / "future.sqlite3")
    c = runtime._conn
    c.execute("INSERT INTO g1s_models VALUES(?,?,?,?,?,?)",
              ("m1", 15, "PRICE", "REGULARIZED_LOGISTIC", 50.0, 40.0))
    c.execute("INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?,?)",
              ("p1", "o1", "m1", 101.0, 0.7, 0))
    c.execute(
        "INSERT INTO g1s_probability_calibrators VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("cal", 15, "PRICE", "REGULARIZED_LOGISTIC", 80.0, 240, 120.0, 120, 120, 5,
         '{"platt_intercept":0.0,"platt_slope":1.0}', "{}", "b"*64,
         "research_only", 90.0),
    )
    c.commit()
    assert _apply_calibrators(runtime, "o1", 100.0, 15) == 1
    row = c.execute("SELECT * FROM g1s_calibrated_predictions").fetchone()
    assert row["raw_p_up"] == pytest.approx(0.7)
    assert row["calibrated_p_up"] == pytest.approx(0.7)
    assert row["production_used"] == 0
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE g1s_calibrated_predictions SET production_used=1")
