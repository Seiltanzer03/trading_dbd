import json
import sqlite3
import threading

import pytest
from fastapi import FastAPI, Response

from seiltanzer.llm_edge_researcher import (
    CONTRACT_VERSION,
    edge_researcher_status,
    propose_edge_hypotheses,
)
from seiltanzer.llm_edge_researcher_routes import install_llm_edge_researcher_routes


class Runtime:
    def __init__(self, *, frozen=None, observation_id="obs-1", instrument="NAS100",
                 captured_ts=1_800_000_000.0, horizon=60):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                frozen_features_json TEXT NOT NULL,
                training_eligible INTEGER NOT NULL DEFAULT 1,
                created_ts REAL NOT NULL
            )""")
        self._conn.execute("""
            CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY,
                resolved_ts REAL NOT NULL,
                terminal_log_return REAL,
                direction_label TEXT,
                mfe_log_return REAL,
                mae_log_return REAL,
                path_quality_status TEXT
            )""")
        self._conn.execute(
            "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?)",
            (observation_id, instrument, captured_ts,
             captured_ts + horizon * 60.0, horizon, json.dumps(frozen or {}),
             1, captured_ts))
        self._conn.commit()


def _valid_provider(counter=None):
    def provider(summary, model, max_hypotheses):
        if counter is not None:
            counter["n"] += 1
        assert summary["contains_future_outcomes"] is False
        assert summary["contains_position_manager_state"] is False
        assert summary["production_authority"] is False
        feature = next(item for item in summary["features"]
                       if item["feature_id"] == "regime.asset")
        return {"hypotheses": [{
            "name": "Asset-state control hypothesis",
            "target_id": "DIRECTION",
            "conditions": [{
                "feature_id": "regime.asset",
                "kind": "categorical",
                "state": feature["value"],
            }],
            "rationale": "Test whether this causal state improves the train-only baseline.",
        }][:max_hypotheses]}
    return provider


def test_edge_researcher_is_shadow_only_and_cache_precedes_provider():
    runtime = Runtime()
    counter = {"n": 0}
    first = propose_edge_hypotheses(runtime, "obs-1", provider=_valid_provider(counter))
    assert first["contract_version"] == CONTRACT_VERSION
    assert first["status"] == "OK"
    assert first["provider_called"] is True
    assert first["cache_hit"] is False
    assert counter["n"] == 1
    hypothesis = first["hypotheses"][0]
    assert hypothesis["status"] == "PROPOSED_SHADOW"
    assert hypothesis["evaluation_state"] == "PENDING_DETERMINISTIC_EVALUATION"
    assert hypothesis["research_only"] is True
    assert hypothesis["production_authority"] is False
    assert hypothesis["eligible_for_policy"] is False
    assert hypothesis["auto_promotion"] is False
    assert hypothesis["may_change_position_manager"] is False
    assert hypothesis["may_change_cvar_stop_or_size"] is False

    second = propose_edge_hypotheses(runtime, "obs-1", provider=_valid_provider(counter))
    assert second["status"] == "OK"
    assert second["cache_hit"] is True
    assert second["provider_called"] is False
    assert counter["n"] == 1
    assert second["hypotheses"][0]["hypothesis_id"] == hypothesis["hypothesis_id"]


def test_provider_cannot_inject_policy_authority_or_unknown_fields():
    runtime = Runtime()

    def provider(summary, model, max_hypotheses):
        return {"hypotheses": [{
            "name": "Bad authority injection",
            "target_id": "DIRECTION",
            "conditions": [{
                "feature_id": "regime.asset",
                "kind": "categorical",
                "state": "NAS100",
            }],
            "rationale": "Should be rejected.",
            "eligible_for_policy": True,
        }]}

    report = propose_edge_hypotheses(runtime, "obs-1", provider=provider)
    assert report["status"] == "NO_VALID_HYPOTHESES"
    assert report["hypotheses"] == []
    assert report["production_authority"] is False
    assert report["eligible_for_policy"] is False
    assert report["rejections"] == ["0:UNKNOWN_HYPOTHESIS_FIELDS"]


def test_unknown_or_future_asof_feature_is_rejected_not_backfilled():
    t0 = 1_800_000_000.0
    runtime = Runtime(frozen={
        "g1s_evidence_v3": {
            "option_static": {
                "iv": 0.22,
                "quality": {"source_ts": t0 + 60.0, "source_quality": 1.0},
            }
        }
    }, captured_ts=t0)

    def provider(summary, model, max_hypotheses):
        assert all(item["feature_id"] != "option.iv" for item in summary["features"])
        return {"hypotheses": [{
            "name": "Future IV must fail",
            "target_id": "DIRECTION",
            "conditions": [{
                "feature_id": "option.iv",
                "kind": "train_relative",
                "state": "ABOVE_MEDIAN",
            }],
            "rationale": "This feature was not available causally at T0.",
        }]}

    report = propose_edge_hypotheses(runtime, "obs-1", provider=provider)
    assert report["status"] == "NO_VALID_HYPOTHESES"
    assert report["rejections"] == ["0.0:FEATURE_NOT_AVAILABLE_AT_T0"]


def test_numeric_thresholds_cannot_be_supplied_by_llm():
    t0 = 1_800_000_000.0
    runtime = Runtime(frozen={
        "g1s_evidence_v3": {
            "price_volatility": {
                "realized_vol_15m": 0.01,
                "realized_vol_60m": 0.02,
                "quality": {"source_ts": t0, "source_quality": 1.0},
            }
        }
    }, captured_ts=t0)

    def provider(summary, model, max_hypotheses):
        return {"hypotheses": [{
            "name": "Raw threshold forbidden",
            "target_id": "DIRECTION",
            "conditions": [{
                "feature_id": "vol.rv15_over_rv60",
                "kind": "threshold",
                "state": "0.5",
            }],
            "rationale": "LLM must not choose a numeric threshold.",
        }]}

    report = propose_edge_hypotheses(runtime, "obs-1", provider=provider)
    assert report["status"] == "NO_VALID_HYPOTHESES"
    assert report["rejections"] == ["0.0:INVALID_NUMERIC_CONDITION"]


def test_research_tables_are_immutable():
    runtime = Runtime()
    report = propose_edge_hypotheses(runtime, "obs-1", provider=_valid_provider())
    hypothesis_id = report["hypotheses"][0]["hypothesis_id"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable llm edge research row"):
        with runtime._conn:
            runtime._conn.execute(
                "UPDATE llm_edge_hypotheses SET status='VALIDATED' WHERE hypothesis_id=?",
                (hypothesis_id,))


def test_status_and_routes_are_research_only():
    runtime = Runtime()
    status = edge_researcher_status(runtime)
    # PR C status reads materialized worker state only. Before startup route
    # materialization exists, it fails closed instead of scanning history.
    assert status["status"] == "INITIALIZING"
    assert status["run_n"] == 0
    assert status["hypothesis_n"] == 0
    assert status["numeric_thresholds_fit_by_llm"] is False
    assert status["future_outcomes_visible_to_llm"] is False
    assert status["writes_active_edge_registry"] is False
    assert status["request_time_history_scan"] is False
    assert status["production_authority"] is False

    app = FastAPI()
    app.state.engine = type("Engine", (), {"short_horizon": runtime})()
    install_llm_edge_researcher_routes(app)
    paths = {route.path: set(route.methods or ()) for route in app.routes}
    assert paths["/api/research/g1s/edge-researcher/status"] == {"GET"}
    assert paths["/api/research/g1s/edge-researcher/lifecycle"] == {"GET"}
    assert paths["/api/research/g1s/edge-researcher/propose"] == {"POST"}
    lifecycle_route = next(
        route for route in app.routes
        if route.path == "/api/research/g1s/edge-researcher/lifecycle"
    )
    lifecycle_response = lifecycle_route.endpoint()
    assert isinstance(lifecycle_response, Response)
    lifecycle_payload = json.loads(lifecycle_response.body)
    assert lifecycle_payload["production_authority"] is False

    # Startup route installation upgrades the prebuilt state before the first
    # GET; no research worker/history reconstruction is required for the contract.
    after = edge_researcher_status(runtime)
    assert after["pr_c_contract_version"] == "llm-edge-researcher-v1.3-pr-c"
    assert after["request_time_history_scan"] is False
    assert (after["automation"] or {}).get("manual_post_only") is False
