from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.config import Settings
from seiltanzer.engine import Engine
from seiltanzer.g1_intelligence_page import INTELLIGENCE_HTML
from seiltanzer.g1_intelligence_routes import install_g1_intelligence_routes
from seiltanzer.g1_intelligence_runtime import IntelligenceRuntime
from seiltanzer.storage_runtime import StorageManager


def test_intelligence_zero_data_is_honest_and_research_only(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    engine = Engine(settings)
    try:
        runtime = IntelligenceRuntime(engine)
        status = runtime.status()
        assert status["g1_stage"] == "G.1E"
        assert status["maturity_state"] == "COLLECTING"
        assert status["experience"]["q_resolved"] == 0
        assert status["models"]["frozen_model_n"] == 0
        assert status["evidence"]["ready_for_g1d"] is False
        assert status["authority"]["research_only"] is True
        assert status["authority"]["production_authority"] is False
        assert status["authority"]["shadow_p_used_for_trading"] is False
    finally:
        engine.close()


def test_intelligence_model_readiness_explains_missing_evidence(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    engine = Engine(settings)
    try:
        status = IntelligenceRuntime(engine).status()
        platt = status["models"]["platt"]
        beta = status["models"]["beta"]
        isotonic = status["models"]["isotonic"]
        assert platt["ready"] is False
        assert beta["ready"] is False
        assert isotonic["ready"] is False
        assert platt["deficits"]["raw_n"] >= 60
        assert platt["semantic_pooling"] is False
        assert platt["semantic_scope_n"] >= 1
        assert any("наблюден" in text.lower() for text in platt["explanations"])
    finally:
        engine.close()


def test_intelligence_snapshot_history_is_immutable(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    engine = Engine(settings)
    try:
        runtime = IntelligenceRuntime(engine)
        assert runtime.snapshot_if_due() is True
        assert runtime.snapshot_if_due() is False
        history = runtime.history()
        assert len(history["items"]) == 1
        row = history["items"][0]
        assert row["contract_version"] == "g1-intelligence-snapshot-v1"
        try:
            with engine.passive._lock, engine.passive._conn:
                engine.passive._conn.execute(
                    "UPDATE g1e_intelligence_snapshots SET captured_ts=captured_ts+1"
                )
        except Exception as exc:
            assert "immutable G1E intelligence snapshot" in str(exc)
        else:
            raise AssertionError("intelligence snapshot must be immutable")
    finally:
        engine.close()


def test_intelligence_page_has_human_cockpit_and_research_boundary():
    assert "SEILTANZER · INTELLIGENCE LAB" in INTELLIGENCE_HTML
    assert "PRODUCTION AUTHORITY OFF" in INTELLIGENCE_HTML
    assert "WAITING FOR OUTCOME" in INTELLIGENCE_HTML
    assert "Research only" in INTELLIGENCE_HTML
    assert "setInterval(load,60000)" in INTELLIGENCE_HTML


def test_intelligence_routes_return_aggregated_backend_state(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    engine = Engine(settings)
    app = FastAPI()
    app.state.engine = engine
    app.state.settings = settings
    app.state.storage = StorageManager(settings)
    app.state.intelligence = IntelligenceRuntime(engine, storage=app.state.storage)
    install_g1_intelligence_routes(app)
    try:
        with TestClient(app) as client:
            page = client.get("/intelligence")
            assert page.status_code == 200
            assert "INTELLIGENCE LAB" in page.text
            assert "RAW Q → OBSERVED FREQUENCY" in page.text
            assert "reliability" in page.text.lower()
            assert page.headers["x-seiltanzer-intelligence-page"] == "g1e-reliability-presentation-v1"
            status = client.get("/api/research/g1/intelligence/status")
            assert status.status_code == 200
            body = status.json()
            assert body["authority"]["production_authority"] is False
            assert body["authority"]["shadow_p_used_for_trading"] is False
            quality = client.get("/api/research/g1/intelligence/forecast-quality")
            assert quality.status_code == 200
            q_identity = quality.json()["status"]["terminal_q_identity"]
            reliability = q_identity["direction_event"]["q_identity"]["reliability"]
            assert reliability["contract_version"] == "g1-reliability-10bin-v1"
            assert client.get("/api/research/g1/intelligence/pipeline").status_code == 200
            assert client.get("/api/research/g1/intelligence/calibration").status_code == 200
            assert client.get("/api/research/g1/intelligence/pending").status_code == 200
            assert client.get("/api/research/g1/intelligence/resolved").status_code == 200
            assert client.get("/api/research/g1/intelligence/history").status_code == 200
    finally:
        engine.close()
