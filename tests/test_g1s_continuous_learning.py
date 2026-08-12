from __future__ import annotations

import sqlite3
import threading

import numpy as np
import pytest

from seiltanzer.g1_short_horizon_continuous_learning import (
    RETURN_FIT_REQUIRED,
    _causal_return_baselines,
    _continuous_candidate,
    _ensure_tables,
    _fit_ridge,
)


class _Runtime:
    def __init__(self, path=None):
        self._lock = threading.RLock()
        if path is not None:
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    @staticmethod
    def _dependency_key(row):
        horizon = int(row["horizon_minutes"])
        bucket = int(float(row["captured_ts"]) // (horizon*60.0))
        return f"{row['instrument']}|{horizon}|{bucket}"

    @staticmethod
    def _feature_vector(row, feature_set):
        return [float(row["x"])], {"x": float(row["x"])}


def _row(index, x, y, captured, resolved, *, label=None, regime="normal"):
    return {
        "observation_id": f"o-{index}",
        "instrument": "NAS100",
        "horizon_minutes": 15,
        "captured_ts": float(captured),
        "target_ts": float(captured)+900.0,
        "resolved_ts": float(resolved),
        "terminal_log_return": float(y),
        "direction_label": label or ("UP" if y > 0 else "DOWN"),
        "market_regime": regime,
        "frozen_features_json": '{"g1s_intraday":{"ret_15m":0.001}}',
        "x": float(x),
    }


def test_continuous_fit_gate_is_explicit_and_research_scale():
    assert RETURN_FIT_REQUIRED == {
        "raw_resolved": 120,
        "effective_n": 60,
        "trading_days": 3,
    }


def test_dependency_weighted_ridge_learns_continuous_target_direction():
    runtime = _Runtime()
    rows = []
    for index in range(80):
        x = -0.02 + 0.0005*index
        rows.append(_row(index, x, 1.5*x, 10_000.0+index*1000.0,
                         11_000.0+index*1000.0))
    beta, mean, std = _fit_ridge(runtime, rows, "TEST")
    assert len(beta) == 2
    for x in (-0.01, 0.01):
        predicted = float(beta[0] + ((x-mean[0])/std[0])*beta[1])
        assert np.sign(predicted) == np.sign(x)


def test_continuous_baseline_never_uses_unresolved_future_return():
    rows = [
        _row(1, 0.1, 0.01, 100.0, 500.0),
        _row(2, 0.2, -0.02, 200.0, 600.0),
        _row(3, 0.3, 0.03, 700.0, 1600.0),
    ]
    baselines = _causal_return_baselines(rows)
    # Historical mean is deliberately neutral until 20 causally visible outcomes.
    assert baselines["causal_historical_mean"] == [0.0, 0.0, 0.0]
    assert len(baselines["fixed_ret15_persistence"]) == 3


def test_continuous_serious_oos_gate_requires_1000_400_and_regime_diversity():
    rows = []
    for index in range(999):
        rows.append(_row(
            index, 0.001, 0.001 if index % 2 == 0 else -0.001,
            1_780_000_000.0+index*86400.0,
            1_780_000_900.0+index*86400.0,
            regime="high" if index % 3 == 0 else "normal",
        ))
    observed, blockers = _continuous_candidate(rows, effective_n=500)
    assert observed["raw_resolved"] == 999
    assert "INSUFFICIENT_RAW_RESOLVED" in blockers


def test_continuous_artifact_tables_are_immutable(tmp_path):
    runtime = _Runtime(tmp_path / "continuous.sqlite3")
    _ensure_tables(runtime)
    runtime._conn.execute(
        "INSERT INTO g1s_return_models VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("m", "DEPENDENCY_WEIGHTED_RIDGE", 15, "TEST", 1.0, 120, 60.0, 3,
         "{}", "{}", "a"*64, "research_only", 2.0),
    )
    runtime._conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        runtime._conn.execute("UPDATE g1s_return_models SET authority='production' WHERE model_id='m'")
