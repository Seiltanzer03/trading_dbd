from __future__ import annotations

import tempfile

import pytest

from seiltanzer.ai_policy_v6 import _input_audit
from seiltanzer.ai_snapshot_materializer import AISnapshotMaterializer, SnapshotNotReady
from seiltanzer.ai_verdict import build_snapshot
from seiltanzer.canonical_market_context import canonical_instrument_code
from seiltanzer.config import Settings
from seiltanzer.engine import Engine


@pytest.mark.parametrize("alias", ["USDCAD", "USD/CAD", "USD_CAD", "usd-cad"])
def test_usdcad_aliases_have_one_canonical_code(alias):
    assert canonical_instrument_code(alias) == "USDCAD"


def test_ai_snapshot_uses_same_materialized_usdcad_quote_during_feed_transition():
    with tempfile.TemporaryDirectory() as data_dir:
        engine = Engine(Settings(demo=True, data_dir=data_dir))
        try:
            engine.market.set_instrument("USDCAD")
            engine.market.refresh_price()
            engine.market.refresh_daily()
            engine.market.refresh_chain()
            engine.market.refresh_iv_surface()
            price = float(engine.market.price["value"])
            trade = engine.journal.open_trade(
                16, "USDCAD", "long", price, price - 0.01, price + 0.025)
            engine.on_trade_opened(trade)

            canonical_tick = engine.tick_payload()
            engine.bind_canonical_tick_provider(lambda: canonical_tick)
            engine.market.price = {
                "value": None,
                "status": "no_data",
                "ts": None,
                "error": "simulated source transition",
                "source": "Swissquote OTC USD/CAD",
            }

            snapshot = build_snapshot(engine)
            price_audit = snapshot["policy_manager"]["input_audit"]["rows"][
                "instrument_price"
            ]
            assert canonical_tick["instrument"] == "USDCAD"
            assert canonical_tick["feeds"]["price"]["ticker"] == "USD/CAD"
            assert snapshot["strategy"]["instrument"] == "USDCAD"
            assert snapshot["trade_geometry"]["current"] == pytest.approx(
                canonical_tick["feeds"]["price"]["value"], abs=5e-5)
            assert price_audit["available"] is True
        finally:
            engine.close()


@pytest.mark.parametrize(
    ("price", "reason"),
    [
        ({"value": None, "status": "no_data", "error": "source down"}, "source down"),
        ({"value": 1.3712, "status": "live", "fresh": False}, "canonical_quote_stale"),
    ],
)
def test_missing_or_stale_quote_is_not_available_or_numeric_zero(price, reason):
    audit = _input_audit({"feeds": {"price": price}}, {})
    row = audit["rows"]["instrument_price"]
    assert row["available"] is False
    assert row["reason"] == reason
    assert row["value"] is None or row["value"] == pytest.approx(1.3712)
    assert row["value"] != 0


class _Journal:
    def active_trade(self):
        return {
            "id": 1,
            "instrument": "USDCAD",
            "entry": 1.37,
            "stop": 1.36,
            "take": 1.395,
            "direction": "long",
        }


class _Market:
    price = {"value": 1.371, "status": "live"}
    chain = {"asof_ts": 1000.0}


class _CanonicalEngine:
    def __init__(self):
        self.journal = _Journal()
        self.market = _Market()
        self.tick = {
            "instrument": "USDCAD",
            "feeds": {"price": {"value": 1.371, "status": "live"}},
        }

    def canonical_tick_payload(self, *, copy_payload=True):
        return self.tick


def _materialized_snapshot():
    return {
        "trade_id": 1,
        "strategy": {"instrument": "USDCAD"},
        "trade_geometry": {"current_r": 0.1},
        "policy_manager": {
            "recommendation": {"next_rung_r": 0.2},
            "cancellation_boundary": {"available": False},
            "first_touch_clock": {"risk_barrier_r": -1.0},
            "input_audit": {
                "rows": {"instrument_price": {"available": True}}
            },
        },
    }


def test_materializer_invalidates_when_canonical_quote_becomes_stale():
    engine = _CanonicalEngine()
    materializer = AISnapshotMaterializer(engine, lambda _: _materialized_snapshot())
    materializer._build_once()
    engine.tick["feeds"]["price"]["fresh"] = False

    with pytest.raises(SnapshotNotReady) as exc:
        materializer.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "PRICE_AVAILABILITY_CHANGED"
