from __future__ import annotations

import datetime as dt
import sys
import types
from types import SimpleNamespace

import numpy as np
import pandas as pd

from seiltanzer import option_feed_resilience as resilience
from seiltanzer.config import INSTRUMENTS, Settings
from seiltanzer.core import options as opt
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import MarketData, _status_dict


def _expiry(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).strftime("%Y-%m-%d")


def _frames(spot: float, expiry: str, n: int):
    exp = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
        hour=16, tzinfo=dt.timezone.utc)
    t_years = max((exp - dt.datetime.now(dt.timezone.utc)).total_seconds(), 3600.0) / (
        365.0 * 24 * 3600)
    strikes = np.linspace(spot * 0.94, spot * 1.06, n)
    sigma = 0.12
    call_mid = np.array([opt.bs_call(spot, float(k), t_years, sigma) for k in strikes])
    put_mid = np.array([opt.bs_put(spot, float(k), t_years, sigma) for k in strikes])

    def frame(mids):
        mids = np.maximum(mids, 1e-5)
        return pd.DataFrame({
            "strike": strikes,
            "bid": mids * 0.99,
            "ask": mids * 1.01,
            "lastPrice": mids,
            "openInterest": np.full(n, 25.0),
            "impliedVolatility": np.full(n, sigma),
        })

    return SimpleNamespace(calls=frame(call_mid), puts=frame(put_mid))


class _FakeTicker:
    def __init__(self, spot: float):
        self.fast_info = SimpleNamespace(last_price=spot)
        self.options = [_expiry(3), _expiry(30)]
        self._spot = spot

    def option_chain(self, expiry: str):
        # Nearest FXC-style expiry is too sparse to support a density. The next
        # listed expiry is dense enough and must be selected instead of disabling
        # the entire USD/CAD option model.
        return _frames(self._spot, expiry, 2 if expiry == self.options[0] else 21)


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


def test_thin_nearest_expiry_falls_forward_to_first_valid_candidate(monkeypatch, tmp_path):
    market = _market(tmp_path)
    ticker = _FakeTicker(73.0)
    _install_fake_yfinance(monkeypatch, ticker)
    monkeypatch.setattr(
        resilience, "_BASE_REFRESH_CHAIN",
        lambda owner: setattr(owner, "chain", {"metrics": None, **_status_dict(error="nearest invalid")}),
    )

    resilience.resilient_refresh_chain(market)

    metrics = market.chain["metrics"]
    assert metrics is not None
    assert metrics["proxy"] == "FXC"
    assert metrics["expiry"] == ticker.options[1]
    assert metrics["expiry_selection"]["candidate_index"] == 1
    assert metrics["expiry_selection"]["rejected_before_selected"]
    assert market.chain["status"] == "delayed"
    assert "validated expiry scan" in market.chain["source"]


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
