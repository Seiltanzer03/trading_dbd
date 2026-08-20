from __future__ import annotations

import inspect
import json
import re
import threading

from seiltanzer import g1_short_horizon_calibration as calibration
from seiltanzer import g1_short_horizon_continuous_learning as continuous
from seiltanzer import g1_short_horizon_evidence_completion as completion
from seiltanzer import g1_short_horizon_evidence_materialization as materialization
from seiltanzer import g1_short_horizon_metrics_integrity as integrity
from seiltanzer import g1_short_horizon_metrics_refinement as metrics
from seiltanzer import production_resource_guard
from seiltanzer.g1_short_horizon_baseline_refinement import _momentum_probability


def test_projected_ret15_preserves_existing_baseline_semantics() -> None:
    legacy_momentum = {
        "frozen_features_json": json.dumps({
            "price_state": {"g1s_intraday": {"ret_15m": 0.01}},
        }),
    }
    assert _momentum_probability(legacy_momentum) == 0.55
    assert _momentum_probability({"momentum_ret_15m": 0.01}) == 0.55
    assert _momentum_probability({"momentum_ret_15m": None}) == 0.5

    legacy_return = {
        "frozen_features_json": json.dumps({
            "g1s_intraday": {"ret_15m": -0.02},
        }),
    }
    assert completion._ret15(legacy_return) == -0.02
    assert completion._ret15({"frozen_ret_15m": -0.02}) == -0.02
    assert continuous._ret15(legacy_return) == -0.02
    assert continuous._ret15({"frozen_ret_15m": -0.02}) == -0.02


def test_evidence_queries_project_scalar_not_full_frozen_payload() -> None:
    query_functions = (
        metrics._effectiveness,
        integrity._model_eval_rows,
        completion._safe_eval_rows,
        continuous._safe_prediction_rows,
        calibration._safe_raw_rows,
        calibration._safe_calibrated_rows,
    )
    for fn in query_functions:
        source = inspect.getsource(fn)
        assert "json_extract(g.frozen_features_json" in source
        assert re.search(
            r"(?m)^\s*g\.frozen_features_json\s*,", source
        ) is None
        assert "g.frozen_forecast_json" not in source


class _Cursor:
    def fetchall(self):
        return []


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return _Cursor()


class _Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = _Connection()


class _TrackedPayload(dict):
    def __init__(self, label: str, events: list[str]):
        super().__init__(report=label)
        self._label = label
        self._events = events

    def __del__(self):
        self._events.append(f"released:{self._label}")


def test_evidence_materializer_releases_payload_before_each_trim(monkeypatch) -> None:
    runtime = _Runtime()
    events: list[str] = []
    monkeypatch.setattr(materialization, "_ensure_table", lambda _runtime: None)
    monkeypatch.setattr(materialization, "_source_signature", lambda _runtime: "source")
    monkeypatch.setattr(materialization, "_writers", lambda _runtime: (
        ("probability_oos", lambda: _TrackedPayload("probability", events)),
        ("final_report", lambda: _TrackedPayload("final", events)),
    ))
    monkeypatch.setattr(
        production_resource_guard,
        "trim_memory_for_pressure",
        lambda: events.append("trim"),
    )

    result = materialization.materialize_evidence_reports(runtime, force=True)

    assert result["refreshed"] is True
    assert set(result["reports"]) == {"probability_oos", "final_report"}
    assert events == [
        "released:probability", "trim",
        "released:final", "trim",
    ]
