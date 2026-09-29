import pytest
import time

from seiltanzer.config import Settings, get_instrument
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import DemoMarket, MarketData


def test_crypto_instruments_config():
    btc = get_instrument("BTCUSD")
    assert btc is not None
    assert btc.asset_class == "crypto"
    assert btc.annual_days == 365.0
    assert btc.deribit_currency == "BTC"
    assert btc.binance_symbol == "BTCUSDT"
    assert btc.proxy_transform == "direct"

    eth = get_instrument("ETHUSD")
    assert eth is not None
    assert eth.asset_class == "crypto"
    assert eth.deribit_currency == "ETH"

    sol = get_instrument("SOLUSD")
    assert sol is not None
    assert sol.asset_class == "crypto"
    assert sol.deribit_currency == "SOL"


def test_demo_market_crypto():
    dm = DemoMarket(seed=42)

    assert dm.prices["BTCUSD"] > 10000.0
    assert dm.prices["ETHUSD"] > 1000.0
    assert dm.prices["SOLUSD"] > 50.0

    chain = dm.chain("BTCUSD")
    assert chain["spot"] > 10000.0
    assert len(chain["strikes"]) > 0
    assert len(chain["call_mid"]) > 0

    vols = dm.vols
    assert "btc_dvol" in vols
    assert vols["btc_dvol"] > 0


def test_market_data_set_instrument_crypto(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    cache = DiskCache(settings.cache_db)
    md = MarketData(settings, cache)

    md.set_instrument("BTCUSD")
    assert md.instrument_code == "BTCUSD"
    assert md.instrument.asset_class == "crypto"

    # Refresh in demo mode
    md.refresh_price()
    assert md.price["status"] == "demo"
    assert md.price["value"] > 10000.0

    md.refresh_proxy_price()
    # For crypto, proxy_price directly matches native instrument price
    assert md.proxy_price["status"] == "demo"
    assert md.proxy_price["source"] == "direct scale (crypto native)"
    assert md.proxy_price["value"] == md.price["value"]


def test_crypto_rest_quote_is_exact_spot_trade_with_exchange_time(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"p": "123456.78", "T": int((time.time() - 2) * 1000)}]

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, params):
            assert url.endswith("/aggTrades")
            assert params == {"symbol": "BTCUSDT", "limit": 1}
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=False, data_dir=str(tmp_path)), cache)
        md.set_instrument("BTCUSD")
        md.refresh_price()
        assert md.price["value"] == pytest.approx(123456.78)
        assert md.price["source"] == "Binance REST trade BTCUSDT"
        assert md.price["instrument_type"] == "crypto_spot"
        assert 1 <= time.time() - md.price["ts"] <= 8
    finally:
        cache.close()


def test_crypto_rest_accepts_trade_arriving_during_request(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"p": "80000", "T": int(time.time() * 1000)}]

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, params):
            time.sleep(0.02)
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=False, data_dir=str(tmp_path)), cache)
        md.set_instrument("BTCUSD")
        md.refresh_price()
        assert md.price["value"] == pytest.approx(80000)
        assert md.price["status"] == "live"
        assert md.price["provider_timestamp_verified"] is True
    finally:
        cache.close()

