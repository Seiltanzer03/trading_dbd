from __future__ import annotations

import sqlite3
import threading

import pytest

from seiltanzer.g1_research_worker import _run_g1s_core, _run_maintenance_phase
from seiltanzer.g1_short_horizon_metrics_integrity import (
    _dependency_adjusted_for_model,
    _preentry_trade_metrics,
)


class _DependencyRuntime:
    @staticmethod
    def _dependency_key(row):
        horizon = int(row["horizon_minutes"])
        bucket = int(float(row["captured_ts"]) // (horizon * 60.0))
        return f"{row['instrument']}|{horizon}|{bucket}"


def test_dependency_adjusted_oos_gives_each_overlap_group_total_weight_one():
    runtime = _DependencyRuntime()
    rows = [
        {"instrument": "NAS100", "horizon_minutes": 60, "captured_ts": 100.0,
         "p_up": 0.9, "direction_label": "UP", "frozen_features_json": "{}"},
        {"instrument": "NAS100", "horizon_minutes": 60, "captured_ts": 200.0,
         "p_up": 0.8, "direction_label": "UP", "frozen_features_json": "{}"},
        {"instrument": "NAS100", "horizon_minutes": 60, "captured_ts": 4000.0,
         "p_up": 0.2, "direction_label": "DOWN", "frozen_features_json": "{}"},
    ]
    report = _dependency_adjusted_for_model(runtime, rows)
    assert report["raw_n"] == 3
    assert report["effective_n"] == 2
    assert report["weight_sum"] == pytest.approx(2.0)
    assert report["dependency_group_total_weight_one"] is True
    assert report["verdict"] == "INSUFFICIENT"
    expected = (0.5 * (0.9-1) ** 2 + 0.5 * (0.8-1) ** 2 + (0.2-0) ** 2) / 2.0
    assert report["model_brier"] == pytest.approx(expected)


class _SqlRuntime:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE g1s_trade_links(
                link_id TEXT PRIMARY KEY, trade_id INTEGER, observation_id TEXT,
                horizon_minutes INTEGER, forecast_age_sec REAL, created_ts REAL
            );
            CREATE TABLE trades(
                id INTEGER PRIMARY KEY, opened_at REAL, direction TEXT, result_r REAL
            );
            CREATE TABLE g1s_shadow_predictions(
                prediction_id TEXT PRIMARY KEY, observation_id TEXT, model_id TEXT,
                created_ts REAL, p_up REAL, production_used INTEGER
            );
            CREATE TABLE g1s_models(
                model_id TEXT PRIMARY KEY, created_ts REAL, training_cutoff_ts REAL
            );
            CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY, direction_label TEXT
            );
        """)


def test_metrics_trade_validation_excludes_prediction_written_after_entry(tmp_path):
    runtime = _SqlRuntime(tmp_path / "metrics.sqlite3")
    c = runtime._conn
    c.execute("INSERT INTO trades VALUES(?,?,?,?)", (1, 1000.0, "long", 1.0))
    c.execute("INSERT INTO g1s_trade_links VALUES(?,?,?,?,?,?)",
              ("l1", 1, "obs-1", 60, 120.0, 900.0))
    c.execute("INSERT INTO g1s_resolutions VALUES(?,?)", ("obs-1", "UP"))
    c.execute("INSERT INTO g1s_models VALUES(?,?,?)", ("m1", 800.0, 700.0))
    c.execute("INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?,?)",
              ("p1", "obs-1", "m1", 900.0, 0.8, 0))
    c.execute("INSERT INTO g1s_models VALUES(?,?,?)", ("m2", 800.0, 700.0))
    c.execute("INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?,?)",
              ("p2", "obs-1", "m2", 1100.0, 0.99, 0))
    c.commit()

    pre = _preentry_trade_metrics(runtime, "m1")
    late = _preentry_trade_metrics(runtime, "m2")
    assert pre["raw_n"] == 1
    assert pre["unique_trade_n"] == 1
    assert pre["brier_move_with_trade_direction"] == pytest.approx(0.04)
    assert pre["prediction_must_precede_trade_entry"] is True
    assert late["raw_n"] == 0
    assert late["brier_move_with_trade_direction"] is None


class _WorkerRuntime:
    def __init__(self):
        self.calls = []

    def materialize_new(self, limit):
        self.calls.append(("materialize", limit)); return 1

    def resolve_new(self, limit):
        self.calls.append(("resolve", limit)); return 2


def test_worker_core_excludes_barrier_and_path_metric_maintenance(monkeypatch):
    runtime = _WorkerRuntime()
    barrier_calls = []
    metric_calls = []
    monkeypatch.setattr(
        "seiltanzer.g1_short_horizon_refinement._materialize_barriers",
        lambda _runtime, limit: barrier_calls.append(limit) or 5,
    )
    monkeypatch.setattr(
        "seiltanzer.g1_short_horizon_metrics_refinement._materialize_path_metrics",
        lambda _runtime, limit: metric_calls.append(limit) or 6,
    )

    core = _run_g1s_core(runtime)
    assert core == {"materialized": 1, "resolved": 2, "batch_limit": 500}
    assert barrier_calls == []
    assert metric_calls == []

    barrier = _run_maintenance_phase(runtime, object(), "barriers")
    metric = _run_maintenance_phase(runtime, object(), "path_metrics")
    assert barrier["rows_created"] == 5
    assert metric["rows_created"] == 6
    assert barrier_calls == [500]
    assert metric_calls == [500]
    assert runtime.calls == [("materialize", 500), ("resolve", 500)]
