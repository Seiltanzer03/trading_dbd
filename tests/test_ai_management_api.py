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
