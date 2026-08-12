from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.g1_management_routes import install_g1_management_routes


class _ManagementRuntime:
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


class _LocalRuntime:
    def eligibility_diagnostics(self):
        return {
            "contract_version": "g1m-local-eligibility-diagnostics-v1",
            "state": "ELIGIBLE_WINDOWS_PENDING_OUTCOME",
            "counts": {"ELIGIBLE_PENDING": 4, "ELIGIBLE_RESOLVED": 0},
            "semantics_changed": False,
            "authority": {"production_authority": False},
        }


def test_local_eligibility_route_is_read_only_and_returns_diagnostics():
    app = FastAPI()
    app.state.engine = SimpleNamespace(
        management=_ManagementRuntime(),
        management_local=_LocalRuntime(),
    )
    install_g1_management_routes(app)
    client = TestClient(app)

    path = "/api/research/g1/management/local-eligibility"
    response = client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "g1m-local-eligibility-diagnostics-v1"
    assert body["semantics_changed"] is False
    assert body["authority"]["production_authority"] is False

    route = next(route for route in app.routes if getattr(route, "path", None) == path)
    assert set(route.methods or ()) <= {"GET", "HEAD"}
