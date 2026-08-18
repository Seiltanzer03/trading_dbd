from __future__ import annotations

import datetime as dt
import sys
import time
import types
from types import SimpleNamespace

import numpy as np
import pandas as pd

from seiltanzer import option_feed_resilience as resilience
from seiltanzer.config import INSTRUMENTS, Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import MarketData, _status_dict


def _expiry(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).strftime("%Y-%m-%d")


def _frames(spot: float, n: int):
    strikes = np.linspace(spot * 0.94, spot * 1.06, n)
    sigma = 0.12

    def frame():
        # Reproduce the important FXC failure mode: the option rows and reported
        # IV exist, but quoted/last mids are unusable. Canonical raw-mid BL must
        # stay strict; the lower-evidence rescue is allowed to use reported IV.
        return pd.DataFrame({
            "strike": strikes,
            "bid": np.zeros(n),
            "ask": np.zeros(n),
            "lastPrice": np.zeros(n),
            "openInterest": np.full(n, 25.0),
            "impliedVolatility": np.full(n, sigma),
        })

    return SimpleNamespace(calls=frame(), puts=frame())


class _FakeTicker:
    def __init__(self, spot: float):
        self.fast_info = SimpleNamespace(last_price=spot)
        self.options = [_expiry(3), _expiry(30)]
        self._spot = spot

    def option_chain(self, expiry: str):
        # The nearest expiry is too sparse even for an IV-smile model; the next
        # listed expiry has enough reported IV points but still no usable mids.
        return _frames(self._spot, 2 if expiry == self.options[0] else 21)


def _install_fake_yfinance(monkeypatch, ticker):
    module = types.ModuleType("yfinance")
    module.Ticker = lambda symbol: ticker
    monkeypatch.setitem(sys.modules, "yfinance", module)


def _market(tmp_path) -> MarketData:
    settings = Settings(demo=False, data_dir=str(tmp_path))
    market = MarketData(settings, DiskCache(settings.cache_db))
    market.set_instrument("USDCAD")
    return market


def test_usdcad_keeps_explicit_inverse_fxc_mapping():
    inst = INSTRUMENTS["USDCAD"]
    assert inst.options_proxy == "FXC"
    assert inst.proxy_transform == "inverse"
    assert inst.proxy_experimental is True


def test_existing_adaptive_metrics_get_inverse_mapping_and_real_expiry(monkeypatch, tmp_path):
    market = _market(tmp_path)
    expiry = _expiry(30)
    monkeypatch.setattr(
        resilience, "_BASE_REFRESH_CHAIN",
        lambda owner: setattr(owner, "chain", {
            "metrics": {"expiry": expiry, "t_years": 0.01},
            **_status_dict(True, "delayed", time.time(), source="adaptive test"),
        }),
    )

    resilience.resilient_refresh_chain(market)

    metrics = market.chain["metrics"]
    assert metrics["proxy_transform"] == "inverse"
    assert metrics["expiry_date"] == expiry
    assert metrics["expiry_ts_utc"] > time.time()
    assert metrics["t_years"] > 0.02


def test_invalid_raw_mids_rescue_from_reported_iv_smile(monkeypatch, tmp_path):
    market = _market(tmp_path)
    ticker = _FakeTicker(73.0)
    _install_fake_yfinance(monkeypatch, ticker)
    monkeypatch.setattr(
        resilience, "_BASE_REFRESH_CHAIN",
        lambda owner: setattr(owner, "chain", {
            "metrics": None,
            **_status_dict(error="canonical raw-mid candidates invalid"),
        }),
    )

    resilience.resilient_refresh_chain(market)

    metrics = market.chain["metrics"]
    assert metrics is not None
    assert metrics["proxy"] == "FXC"
    assert metrics["proxy_transform"] == "inverse"
    assert metrics["expiry"] == ticker.options[1]
    assert metrics["density_input"]["mode"] == "reported_iv_smile_bs_reconstruction"
    assert metrics["density_input"]["quality_tier"] == "experimental_proxy_iv_rescue"
    assert metrics["expiry_selection"]["selected_ordinal"] == 1
    assert market.chain["status"] == "delayed"
    assert "IV-smile rescue" in market.chain["source"]


def test_iv_surface_skips_sparse_expiry_instead_of_aborting(monkeypatch, tmp_path):
    market = _market(tmp_path)
    ticker = _FakeTicker(73.0)
    _install_fake_yfinance(monkeypatch, ticker)
    monkeypatch.setattr(
        resilience, "_BASE_REFRESH_IV_SURFACE",
        lambda owner: setattr(owner, "iv_surface", _status_dict(error="nearest invalid")),
    )

    resilience.resilient_refresh_iv_surface(market)

    assert market.iv_surface["status"] == "delayed"
    assert market.iv_surface["value"]
    assert market.iv_surface["value"][0]["expiry"] == ticker.options[1]
    assert market.iv_surface["expiry_selection"]["usable_expiries"] == [ticker.options[1]]
