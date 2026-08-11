from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.g1_management_routes import install_g1_management_routes


class _Runtime:
    def status(self):
        return {"g1_stage": "G.1-M", "authority": {"production_authority": False}}

    def observations(self, **kwargs):
        return {"items": [], "filters": kwargs}

    def policies(self):
        return {"items": []}

    def cohorts(self):
        return {"items": []}

    def edge(self):
        return {"edge_claim_allowed": False}

    def research_cuts(self, **kwargs):
        return {"items": [], "filters": kwargs}

    def decision(self, observation_id):
        return None


def test_management_routes_are_read_only_and_bounded():
    app = FastAPI()
    app.state.engine = SimpleNamespace(management=_Runtime())
    install_g1_management_routes(app)
    client = TestClient(app)

    paths = [
        "/api/research/g1/management/status",
        "/api/research/g1/management/observations",
        "/api/research/g1/management/pending",
        "/api/research/g1/management/resolved",
        "/api/research/g1/management/policies",
        "/api/research/g1/management/cohorts",
        "/api/research/g1/management/edge",
        "/api/research/g1/management/cuts",
        "/management-edge",
    ]
    for path in paths:
        assert client.get(path).status_code == 200, path

    assert client.get("/api/research/g1/management/decision/missing").status_code == 404
    # Research surface contains no public mutation route.
    management_paths = [
        route for route in app.routes
        if str(getattr(route, "path", "")).startswith("/api/research/g1/management")
    ]
    assert management_paths
    assert all(set(route.methods or ()) <= {"GET", "HEAD"} for route in management_paths)


def test_management_page_has_explicit_research_authority_boundary():
    app = FastAPI()
    app.state.engine = SimpleNamespace(management=_Runtime())
    install_g1_management_routes(app)
    html = TestClient(app).get("/management-edge").text
    assert "MANAGEMENT EDGE" in html
    assert "RESEARCH ONLY" in html
    assert "production authority OFF" in html
    assert "PRODUCTION POLICY vs HOLD" in html
