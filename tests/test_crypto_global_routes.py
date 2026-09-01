import math
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.crypto_global_routes import (
    CONTRACT_VERSION,
    CRYPTO_ASSETS,
    CryptoGlobalCache,
    build_crypto_global_payload,
    crypto_warming_payload,
    install_crypto_global_routes,
)


def _coingecko_fixture():
    rows = []
    for index, asset in enumerate(CRYPTO_ASSETS):
        row = {
            "id": asset["id"],
            "current_price": 60_000 / (index + 1),
            "market_cap": 1_000_000_000_000 / (index + 1),
            "total_volume": 20_000_000_000 / (index + 1),
            "price_change_percentage_1h_in_currency": 0.1 * index,
            "price_change_percentage_24h_in_currency": -2.0 + index * 0.75,
            "price_change_percentage_7d_in_currency": -4.0 + index,
            "last_updated": "2026-09-01T10:00:00Z",
        }
        rows.append(row)
    # A missing observed field must stay N/A, never become zero.
    rows[-1]["total_volume"] = None
    return ({
        "active_cryptocurrencies": 17_500,
        "total_market_cap": {"usd": 3_700_000_000_000},
        "total_volume": {"usd": 180_000_000_000},
        "market_cap_change_percentage_24h_usd": 1.25,
        "market_cap_percentage": {"btc": 54.2, "eth": 12.7},
    }, rows, {
        "provider": "CoinGecko public API",
        "status": "observed",
        "observed_at": 1_788_256_800.0,
        "error": None,
    })


def _history_fixture():
    base_ts = 1_788_000_000.0
    series = {}
    for index, asset in enumerate(CRYPTO_ASSETS):
        # Distinct deterministic observed-like fixtures with aligned timestamps.
        series[asset["symbol"]] = [
            (base_ts + hour * 3600, (100 + index * 7) * math.exp((hour * (index + 1)) / 100_000))
            for hour in range(80)
        ]
    return series, {
        "provider": "Yahoo Finance via yfinance",
        "status": "observed",
        "observed_at": base_ts + 79 * 3600,
        "interval": "1h",
        "window": "7d",
        "available_assets": len(series),
        "error": None,
    }


def test_crypto_payload_uses_observed_sources_and_preserves_missing_values():
    payload = build_crypto_global_payload(
        coingecko_fetcher=_coingecko_fixture,
        history_fetcher=_history_fixture,
        now=1_788_300_000.0,
    )
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["status"] == "observed"
    assert payload["available"] is True
    assert payload["global"]["btc_dominance_pct"] == 54.2
    assert len(payload["assets"]) == len(CRYPTO_ASSETS)
    assert payload["assets"][-1]["volume_24h_usd"] is None
    assert payload["assets"][-1]["available"] is True
    assert payload["correlation"]["minimum_pair_observations"] == 36
    assert payload["correlation"]["matrix"][0][0] == 1.0
    assert payload["leadership_path"]
    assert payload["summary"]["authority"] == "descriptive_only"
    assert payload["summary"]["observations_ru"][-1].endswith("не торговый сигнал.")


def test_crypto_payload_is_honest_partial_when_one_provider_fails():
    def unavailable_history():
        return ({asset["symbol"]: [] for asset in CRYPTO_ASSETS}, {
            "provider": "Yahoo Finance via yfinance",
            "status": "no_data",
            "observed_at": None,
            "error": "network unavailable",
        })

    payload = build_crypto_global_payload(
        coingecko_fetcher=_coingecko_fixture,
        history_fetcher=unavailable_history,
        now=1_788_300_000.0,
    )
    assert payload["status"] == "partial"
    assert payload["available"] is True
    assert payload["correlation"]["matrix"][0][1] is None
    assert payload["assets"][0]["realized_vol_24h_annual_pct"] is None
    assert payload["sources"]["yahoo"]["status"] == "no_data"


def test_crypto_routes_publish_page_and_nonblocking_cache_payload():
    payload = crypto_warming_payload()
    payload["status"] = "partial"
    cache = CryptoGlobalCache(loader=lambda: payload)
    cache.refresh_now()
    app = FastAPI()
    install_crypto_global_routes(app, cache=cache)
    client = TestClient(app)

    api = client.get("/api/crypto/global")
    assert api.status_code == 200
    assert api.json()["contract_version"] == CONTRACT_VERSION
    assert api.json()["transport"]["cache_state"] == "FRESH"
    page = client.get("/crypto")
    assert page.status_code == 200
    assert "GLOBAL CRYPTO" in page.text
    assert "/static/js/crypto_global.js" in page.text


def test_crypto_cache_preserves_observed_snapshot_as_explicit_stale():
    payload = crypto_warming_payload()
    payload.update({"status": "partial", "available": True, "assets": [{"symbol": "BTC"}]})
    cache = CryptoGlobalCache(loader=lambda: payload)
    cache.refresh_now()
    with cache._lock:
        cache._loaded_at = time.time() - 400
        cache._building = True

    result = cache.get()

    assert result["status"] == "stale"
    assert result["available"] is True
    assert result["assets"] == [{"symbol": "BTC"}]
    assert result["freshness"]["stale"] is True
    assert result["transport"]["cache_state"] == "STALE_REFRESHING"


def test_crypto_frontend_has_no_random_or_synthetic_motion():
    source = (Path(__file__).resolve().parents[1]
              / "seiltanzer/web/js/crypto_global.js").read_text(encoding="utf-8")
    assert "Math.random" not in source
    assert "setInterval(refresh,60000)" in source
    assert "createPlotlyCameraGuard" in source
    assert "matrix?.[i]?.[j]" in source
    assert "STALE SNAPSHOT" in source
