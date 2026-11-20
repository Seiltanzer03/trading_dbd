import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd

from seiltanzer.core.options import _trapezoid
from seiltanzer.data.adaptive_chain import _raw_chain
from seiltanzer.data.cache import production_chain_snapshot
from seiltanzer.option_q_adapter import adapt_option_q_forecast


def _options(center, spot=100.0):
    strikes = np.linspace(62, 145, 113)
    density = np.exp(-0.5 * ((strikes - center) / 3.0) ** 2)
    density /= _trapezoid(density, strikes)
    return {
        "demo": False, "spot": spot, "proxy_spot": spot, "t_years": 14 / 365,
        "density": {"strikes": strikes.tolist(), "q": density.tolist()},
        "implied_move": {"sigma_annual": 0.20},
    }


def test_cached_density_with_materially_wrong_center_is_not_live_evidence():
    assert not production_chain_snapshot(_options(72))


def test_historical_q_does_not_promote_a_normalized_but_miscentered_density():
    output = adapt_option_q_forecast(
        _options(72), 390, 0.02, "SP500", 6500.0, "option_native_expiry",
    )
    assert output["q_available"] is False
    assert output["q_unavailable_reason"] == "OPTION_DENSITY_CENTER_MISMATCH"


def test_valid_legacy_density_is_checked_at_snapshot_spot_before_proxy_mapping():
    assert production_chain_snapshot(_options(100))
    options = _options(100)
    options["proxy_transform"] = "inverse"
    output = adapt_option_q_forecast(
        options, 390, 0.02, "USDCAD", 1.4, "option_native_expiry",
    )
    assert output["q_terminal_distribution_available"] is True


def test_invalid_or_crossed_quotes_do_not_use_last_execution_price():
    frame = pd.DataFrame({
        "strike": range(98, 103),
        "bid": [2.0, 0.0, 3.0, 4.0, float("nan")],
        "ask": [2.2, 3.0, 2.0, 4.0, 5.0],
        "lastPrice": [11.0, 12.0, 13.0, 14.0, 15.0],
        "openInterest": [10] * 5,
        "impliedVolatility": [0.20] * 5,
    })
    ticker = SimpleNamespace(option_chain=lambda expiry: SimpleNamespace(calls=frame, puts=frame))
    expiry = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    chain = _raw_chain(ticker, expiry, 100.0)
    np.testing.assert_allclose(chain["call_mid"], [2.1, np.nan, np.nan, 4.0, np.nan])
    np.testing.assert_allclose(chain["put_mid"], chain["call_mid"])
