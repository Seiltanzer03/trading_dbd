from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.llm_edge_lifecycle import publish_materialized_lifecycle_cache
from seiltanzer.ml_research_broadcast import (
    build_ml_research_broadcast,
    install_ml_research_broadcast,
)


class Runtime:
    def __init__(self):
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        return {
            "status_snapshot_cached": True,
            "request_time_sqlite_access": False,
            "observations": 320,
            "resolved": 280,
            "pending": 40,
            "models": 3,
            "last_step": {
                "started_ts": 1_700_000_080.0,
                "finished_ts": 1_700_000_081.0,
                "duration_ms": 1000.0,
                "error": None,
            },
            "horizons": [{
                "horizon_minutes": 60,
                "state": "SHADOW_FIT_ALLOWED",
                "raw_resolved": 80,
                "effective_n": 52,
                "positive_n": 27,
                "negative_n": 25,
                "pending": 8,
                "model_n": 2,
                "oos_candidate_blockers": ["INSUFFICIENT_TEMPORAL_BLOCKS"],
            }],
        }


def _app(now=1_700_000_100.0):
    runtime = Runtime()
    publish_materialized_lifecycle_cache(runtime, """{
      "status":"OK","updated_ts":1700000090,
      "researcher":{"proposal_runs":4,"hypotheses":9,"discovery_signals":3,
        "frozen_prospective":1,"collecting":0,"prospective_pass":0,
        "prospective_fail":1,"rejected":6,"active_edge":0},
      "automation":{"last_automatic_run_id":"llm-edge-run-real",
        "last_automatic_run_ts":1700000080,"last_status":"OK",
        "new_resolved_t0_since_last_run":22,"required_new_resolved_t0":100},
      "candidates":[{"candidate_id":"candidate-real","hypothesis_id":"hyp-real",
        "name":"Real candidate","target":"DIRECTION","horizon":60,
        "state":"FAILED_LIVE","conditions":[{"feature_id":"vol.rv15_over_rv60",
          "kind":"train_relative","state":"ABOVE_MEDIAN"}],
        "prospective":{"eligible_opportunities":110,"matched_n":96,
          "unavailable_opportunities":2,"missed_prediction_windows":1,
          "next_checkpoint":null,"effect":0.004,"p":0.2,"q":0.15,"decision":"FAIL",
          "evidence_label":"LIVE_PROSPECTIVE_OOS",
          "historical_discovery_evidence_counted":false,
          "checkpoints":[{"checkpoint_n":96,"primary_improvement":0.004,
            "q_value":0.15,"q_value_max":0.0167,"decision":"FAIL"}]},
        "active_edge_eligible":false,"production_authority":false,
        "automatic_execution":false}]
    }""")
    app = FastAPI()
    app.state.engine = SimpleNamespace(short_horizon=runtime)
    app.state.g1_research_worker = {
        "running": True,
        "current_phase": "maintenance:fit_models",
        "maintenance_running": True,
        "maintenance_phase": "fit_models",
        "last_started_ts": now - 4,
        "last_finished_ts": now - 3,
        "last_duration_ms": 1000,
        "last_error": None,
    }
    return app, runtime


def test_broadcast_uses_only_materialized_status_and_explains_real_failure():
    app, runtime = _app()
    payload = build_ml_research_broadcast(app, now=1_700_000_100.0)

    assert runtime.status_calls == 1
    assert payload["freshness"]["stale"] is False
    assert payload["worker"]["activity_indicator_allowed"] is True
    assert payload["worker"]["current_phase"]["label_ru"] == "Проверка допуска и обучение shadow-моделей"
    assert payload["training"]["horizons"][0]["effective_n"] == 52
    assert payload["training"]["horizons"][0]["blockers"][0]["label_ru"] == "Недостаточно независимых временных блоков"
    hypothesis = payload["hypotheses"][0]
    assert hypothesis["stage"]["label_ru"] == "НЕ ПРОШЛО LIVE OOS"
    assert hypothesis["evidence"]["matched_n"] == 96
    assert hypothesis["rejection"]["code"] == "FDR_Q_ABOVE_LIMIT"
    assert hypothesis["rejection"]["reason_complete"] is True
    assert hypothesis["production_authority"] is False
    assert payload["semantics"]["request_time_research"] is False
    assert payload["semantics"]["request_time_sqlite_access"] is False
    assert payload["semantics"]["simulated_activity"] is False
    assert "disagreement_logger" in payload
    assert "ede_breakthrough" in payload
    assert payload["ede_breakthrough"]["active_pairs_count"] == 191
    assert payload["ede_breakthrough"]["families_count"] == 10


def test_missing_materialized_timestamp_is_honest_stale_na():
    app = FastAPI()
    runtime = Runtime()
    publish_materialized_lifecycle_cache(runtime, '{"status":"INITIALIZING","candidates":[]}')
    app.state.engine = SimpleNamespace(short_horizon=runtime)

    payload = build_ml_research_broadcast(app, now=1_700_000_100.0)

    assert payload["freshness"]["stale"] is True
    assert payload["freshness"]["age_sec"] is None
    assert payload["status"]["code"] == "INITIALIZING"
    assert payload["hypotheses"] == []
    assert payload["recent_runs"]


def test_broadcast_api_and_standalone_page_are_read_only_routes():
    app, _runtime = _app()
    install_ml_research_broadcast(app)
    paths = {route.path: set(route.methods or ()) for route in app.routes}
    assert paths["/api/research/ml-broadcast"] == {"GET"}
    assert paths["/ml-research"] == {"GET"}

    with TestClient(app) as client:
        response = client.get("/api/research/ml-broadcast")
        assert response.status_code == 200
        assert response.json()["read_only"] is True
        page = client.get("/ml-research")
        assert page.status_code == 200
        assert "ML RESEARCH LIVE" in page.text
