import numpy as np
import pytest

from seiltanzer.data.deribit import DeribitFetcher, get_deribit_fetcher


def _mock_book_summary_btc():
    return [
        {
            "instrument_name": "BTC-25DEC26-80000-C",
            "mark_price": 0.12,
            "bid_price": 0.118,
            "ask_price": 0.122,
            "open_interest": 500.0,
            "mark_iv": 45.0,
            "delta": 0.52,
            "gamma": 0.00002,
            "quote_currency": "USD",
        },
        {
            "instrument_name": "BTC-25DEC26-80000-P",
            "mark_price": 0.11,
            "bid_price": 0.108,
            "ask_price": 0.112,
            "open_interest": 450.0,
            "mark_iv": 46.0,
            "delta": -0.48,
            "gamma": 0.00002,
            "quote_currency": "USD",
        },
        {
            "instrument_name": "BTC-25DEC26-90000-C",
            "mark_price": 0.08,
            "bid_price": 0.078,
            "ask_price": 0.082,
            "open_interest": 600.0,
            "mark_iv": 44.0,
            "delta": 0.35,
            "gamma": 0.000018,
            "quote_currency": "USD",
        },
        {
            "instrument_name": "BTC-25DEC26-90000-P",
            "mark_price": 0.16,
            "bid_price": 0.158,
            "ask_price": 0.162,
            "open_interest": 300.0,
            "mark_iv": 47.0,
            "delta": -0.65,
            "gamma": 0.000018,
            "quote_currency": "USD",
        },
        {
            "instrument_name": "BTC-25DEC26-70000-C",
            "mark_price": 0.18,
            "bid_price": 0.178,
            "ask_price": 0.182,
            "open_interest": 400.0,
            "mark_iv": 48.0,
            "delta": 0.68,
            "gamma": 0.000019,
            "quote_currency": "USD",
        },
        {
            "instrument_name": "BTC-25DEC26-70000-P",
            "mark_price": 0.07,
            "bid_price": 0.068,
            "ask_price": 0.072,
            "open_interest": 550.0,
            "mark_iv": 49.0,
            "delta": -0.32,
            "gamma": 0.000019,
            "quote_currency": "USD",
        },
    ]


def test_deribit_fetcher_singleton():
    f1 = get_deribit_fetcher()
    f2 = get_deribit_fetcher()
    assert f1 is f2


def test_deribit_fetch_chain_mock(monkeypatch):
    fetcher = DeribitFetcher()
    monkeypatch.setattr(fetcher, "fetch_index_price", lambda c: 80000.0)
    monkeypatch.setattr(fetcher, "fetch_raw_options", lambda c: _mock_book_summary_btc())

    chain = fetcher.fetch_chain("BTC")
    assert chain is not None
    assert chain["spot"] == 80000.0
    assert len(chain["strikes"]) == 3
    np.testing.assert_array_equal(chain["strikes"], np.array([70000.0, 80000.0, 90000.0]))
    assert len(chain["call_mid"]) == 3
    assert len(chain["put_mid"]) == 3
    assert chain["t_years"] > 0
    assert chain["expiry"] == "25DEC26"
    assert chain["atm_iv"] > 0


def test_deribit_fetch_full_options_matrix_mock(monkeypatch):
    fetcher = DeribitFetcher()
    monkeypatch.setattr(fetcher, "fetch_index_price", lambda c: 80000.0)
    monkeypatch.setattr(fetcher, "fetch_raw_options", lambda c: _mock_book_summary_btc())
    monkeypatch.setattr(fetcher, "fetch_dvol", lambda c: 45.5)

    matrix = fetcher.fetch_full_options_matrix("BTC")
    assert matrix["currency"] == "BTC"
    assert matrix["spot"] == 80000.0
    assert matrix["dvol"] == 45.5
    assert "25DEC26" in matrix["expiries_list"]

    exp_data = matrix["matrix"]["25DEC26"]
    assert exp_data["strikes_count"] == 3
    rows = exp_data["rows"]
    assert rows[1]["strike"] == 80000.0
    assert rows[1]["call"]["mark_usd"] == 80000.0 * 0.12
    assert rows[1]["put"]["mark_usd"] == 80000.0 * 0.11


def test_deribit_usdc_settlement_scale(monkeypatch):
    fetcher = DeribitFetcher()
    monkeypatch.setattr(fetcher, "fetch_index_price", lambda c: 100.0)
    mock_sol_raw = [
        {
            "instrument_name": "SOL_USDC-25DEC26-100-C",
            "mark_price": 12.5,
            "bid_price": 12.0,
            "ask_price": 13.0,
            "open_interest": 1000.0,
            "mark_iv": 60.0,
            "delta": 0.5,
            "gamma": 0.01,
            "quote_currency": "USDC",
            "base_currency": "SOL",
        },
        {
            "instrument_name": "SOL_USDC-25DEC26-100-P",
            "mark_price": 11.5,
            "bid_price": 11.0,
            "ask_price": 12.0,
            "open_interest": 900.0,
            "mark_iv": 62.0,
            "delta": -0.5,
            "gamma": 0.01,
            "quote_currency": "USDC",
            "base_currency": "SOL",
        },
        {
            "instrument_name": "SOL_USDC-25DEC26-120-C",
            "mark_price": 5.0,
            "bid_price": 4.5,
            "ask_price": 5.5,
            "open_interest": 500.0,
            "mark_iv": 65.0,
            "delta": 0.3,
            "gamma": 0.008,
            "quote_currency": "USDC",
            "base_currency": "SOL",
        },
    ]
    monkeypatch.setattr(fetcher, "fetch_raw_options", lambda c: mock_sol_raw)

    matrix = fetcher.fetch_full_options_matrix("SOL")
    exp_data = matrix["matrix"]["25DEC26"]
    row_100 = exp_data["rows"][0]
    # For USDC-settled options, mark_usd must be direct 12.5, NOT multiplied by 100
    assert row_100["call"]["mark_usd"] == 12.5
    assert row_100["put"]["mark_usd"] == 11.5
