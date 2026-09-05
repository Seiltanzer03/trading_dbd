from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.crypto_global_routes import (
    CryptoGlobalCache,
    build_crypto_market_summary_payload,
    build_crypto_options_matrix_payload,
    crypto_warming_payload,
    install_crypto_global_routes,
)


def test_crypto_options_matrix_endpoint():
    app = FastAPI()
    cache = CryptoGlobalCache(loader=crypto_warming_payload)
    install_crypto_global_routes(app, cache=cache)
    client = TestClient(app)

    res = client.get("/api/crypto/options-matrix?currency=BTC")
    assert res.status_code == 200
    data = res.json()
    assert data["currency"] == "BTC"
    assert "spot" in data
    assert "matrix" in data
    assert "expiries_list" in data


def test_crypto_market_summary_endpoint():
    app = FastAPI()
    cache = CryptoGlobalCache(loader=crypto_warming_payload)
    install_crypto_global_routes(app, cache=cache)
    client = TestClient(app)

    res = client.get("/api/crypto/market-summary")
    assert res.status_code == 200
    data = res.json()
    assert "assets" in data
    assert "BTC" in data["assets"]
    assert "ETH" in data["assets"]
    assert "SOL" in data["assets"]


def test_crypto_term_structure_endpoint():
    app = FastAPI()
    cache = CryptoGlobalCache(loader=crypto_warming_payload)
    install_crypto_global_routes(app, cache=cache)
    client = TestClient(app)

    res = client.get("/api/crypto/term-structure?currency=ETH")
    assert res.status_code == 200
    data = res.json()
    assert data["currency"] == "ETH"
    assert "points" in data
    assert isinstance(data["points"], list)
