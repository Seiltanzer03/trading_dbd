import pytest
from fastapi.testclient import TestClient
from seiltanzer.app import create_app
from seiltanzer.config import Settings

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    engine.market.refresh_price()
    price = engine.market.price["value"]
    trade = engine.journal.open_trade(3, "NAS100", "long", price,
                                      price*.99, price*1.025)
    engine.position.open_trade(trade)
    engine.on_trade_opened(trade)
    with TestClient(app) as c:
        yield c

def test_fallback_returns_structured_management_decision(client):
    response = client.post("/api/ai/verdict")
    assert response.status_code == 200
    body = response.json()
    decision = body["management_decision"]
    assert decision["policy"] in {"HOLD","CLOSE_10","CLOSE_25","CLOSE_50","EXIT"}
    assert decision["fraction_semantics"] == "fraction_of_current_remaining_position"
    assert body["mode"] == "deterministic_fallback"
    edge = body["edge_management"]
    assert edge["action_now"] == decision["policy"]
    assert edge["hard_risk_cvar_preserved"] is True
    assert edge["may_widen_stop"] is False
    assert edge["may_increase_position"] is False
    assert edge["automatic_execution_allowed"] is False


def test_extended_shadow_action_is_registered_and_manually_acknowledged(
    client, monkeypatch,
):
    def fake_verdict(snapshot):
        geometry = snapshot["trade_geometry"]
        current = float(geometry["current"])
        active_stop = float(geometry["active_risk_barrier"])
        target = active_stop + (current - active_stop) * 0.5
        return {
            "verdict": "test shadow action",
            "model": "test-provider",
            "llm_shadow_decision": {
                "status": "ok",
                "policy": "TIGHTEN_STOP",
                "confidence": 0.8,
                "production_authority": False,
                "automatic_execution_allowed": False,
                "working_action": {
                    "contract_version": "llm-shadow-manual-action-v1",
                    "status": "READY_FOR_MANUAL_CONFIRMATION",
                    "policy": "TIGHTEN_STOP",
                    "confidence": 0.8,
                    "instruction_ru": f"Подтянуть стоп к {target:g}",
                    "parameters": {"stop_price": target, "anchor": "TEST"},
                    "manual_confirmation_required": True,
                    "automatic_execution_allowed": False,
                    "may_widen_stop": False,
                    "may_increase_position": False,
                },
            },
        }

    monkeypatch.setattr("seiltanzer.app.request_verdict", fake_verdict)
    response = client.post("/api/ai/verdict")
    assert response.status_code == 200
    body = response.json()
    action = body["llm_shadow_decision"]["working_action"]
    assert action["action_id"].startswith("shadow-action-")
    assert action["execution_status"] == "pending_execution"
    assert action["production_authority"] is False

    acknowledged = client.post("/api/ai/shadow-action/ack", json={
        "action_id": action["action_id"],
        "trade_id": action["trade_id"],
        "executed": True,
    })
    assert acknowledged.status_code == 200
    result = acknowledged.json()
    assert result["execution_status"] == "executed"
    assert result["position_state"]["active_stop_type"] == "TIGHTENED"
    assert client.get("/api/position").json()["shadow_actions"][-1]["status"] == "executed"
