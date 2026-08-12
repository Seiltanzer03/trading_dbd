from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from seiltanzer.g1_short_horizon_continuous_learning import _ensure_tables
from seiltanzer.g1_short_horizon_continuous_v2 import _write_v2_return_predictions
from seiltanzer.g1_short_horizon_feature_contract_v2 import FEATURE_CONTRACT_V2


class _Runtime:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.errors = []
        self._conn.execute("""
            CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY, instrument TEXT,
                frozen_features_json TEXT, price_quality REAL, option_quality REAL
            )""")
        _ensure_tables(self)

    @staticmethod
    def _feature_vector(row, feature_set):
        assert feature_set == "MARKET_V2"
        return [2.0], {"x": 2.0}

    def _error(self, *args, **kwargs):
        self.errors.append((args, kwargs))


def _insert_model(runtime, *, created=90.0, cutoff=80.0):
    params = {
        "intercept_and_coefficients": [0.1, 0.5],
        "feature_mean": [1.0], "feature_std": [2.0], "feature_names": ["x"],
        "ridge_l2": 1.0, "feature_contract_version": FEATURE_CONTRACT_V2,
    }
    runtime._conn.execute(
        "INSERT INTO g1s_return_models VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("v2m", "DEPENDENCY_WEIGHTED_RIDGE_V2", 15, "MARKET_V2", cutoff,
         120, 60.0, 3, json.dumps(params), "{}", "a"*64, "research_only", created),
    )
    runtime._conn.commit()


def _insert_observation(runtime):
    frozen = {"g1s_evidence_v2": {"contract_version": FEATURE_CONTRACT_V2}}
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?)",
        ("o1", "NAS100", json.dumps(frozen), 1.0, 1.0),
    )
    runtime._conn.commit()


def test_v2_return_prediction_requires_model_and_cutoff_before_t0(tmp_path):
    runtime = _Runtime(tmp_path/"v2.sqlite3")
    _insert_observation(runtime)
    _insert_model(runtime, created=90.0, cutoff=80.0)
    assert _write_v2_return_predictions(runtime, "o1", 100.0, 15) == 1
    row = runtime._conn.execute("SELECT * FROM g1s_return_predictions").fetchone()
    assert row["model_id"] == "v2m"
    assert row["production_used"] == 0
    assert row["predicted_log_return"] == pytest.approx(0.35)


def test_v2_return_prediction_rejects_model_created_after_t0(tmp_path):
    runtime = _Runtime(tmp_path/"late.sqlite3")
    _insert_observation(runtime)
    _insert_model(runtime, created=101.0, cutoff=80.0)
    assert _write_v2_return_predictions(runtime, "o1", 100.0, 15) == 0


def test_v2_return_prediction_rejects_legacy_observation(tmp_path):
    runtime = _Runtime(tmp_path/"legacy.sqlite3")
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?)",
        ("o1", "NAS100", "{}", 1.0, 1.0),
    )
    runtime._conn.commit()
    _insert_model(runtime, created=90.0, cutoff=80.0)
    assert _write_v2_return_predictions(runtime, "o1", 100.0, 15) == 0
