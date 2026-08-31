from __future__ import annotations

from types import SimpleNamespace
from threading import Event

from fastapi import FastAPI

from seiltanzer import visual_universe_routes as universe
from seiltanzer.visual_universe_page import install_visual_universe_page


def _rate(value: float, previous: float, asof: float = 1_700_000_000.0):
    return {"value": value, "previous": previous, "asof": asof, "exchange": "TEST"}


def test_rates_orbit_uses_observed_yields_and_basis_point_math():
    observed = {
        "^IRX": _rate(5.10, 5.00),
        "^FVX": _rate(4.00, 3.95),
        "^TNX": _rate(4.20, 4.10),
        "^TYX": _rate(4.45, 4.40),
    }
    payload = universe.build_rates_orbit_payload(
        now=1_700_000_100.0,
        fetcher=lambda ticker: observed[ticker],
        use_cache=False,
    )

    assert payload["available"] is True
    assert payload["curve_available"] is True
    assert payload["semantics"]["synthetic_fallback"] is False
    assert payload["semantics"]["irx_is_13_week_tbill"] is True
    nodes = {row["id"]: row for row in payload["series"]}
    assert nodes["UST_13W"]["yield_pct"] == 5.10
    assert nodes["UST_13W"]["change_bps"] == 10.0
    assert nodes["UST_10Y"]["change_bps"] == 10.0
    spreads = {(row["from"], row["to"]): row for row in payload["spreads"]}
    assert spreads[("UST_13W", "UST_10Y")]["spread_bps"] == -90.0
    assert spreads[("UST_5Y", "UST_10Y")]["spread_bps"] == 20.0
    assert payload["curve_state"] == "SHORT_10Y_INVERTED"


def test_rates_orbit_never_interpolates_failed_source():
    def fetcher(ticker: str):
        if ticker == "^FVX":
            raise RuntimeError("source down")
        return _rate(4.0, 4.0)

    payload = universe.build_rates_orbit_payload(
        now=1_700_000_100.0, fetcher=fetcher, use_cache=False)
    row = next(item for item in payload["series"] if item["ticker"] == "^FVX")
    assert row["available"] is False
    assert row["yield_pct"] is None
    assert "source down" in row["reason"]
    assert payload["semantics"]["interpolation"] is False


def test_parse_yahoo_chart_prefers_observed_regular_market_value():
    body = {
        "chart": {
            "error": None,
            "result": [{
                "meta": {"regularMarketPrice": 4.321, "regularMarketTime": 1234,
                         "chartPreviousClose": 4.20},
                "timestamp": [1000, 1200],
                "indicators": {"quote": [{"close": [4.20, 4.30]}]},
            }],
        },
    }
    row = universe.parse_yahoo_chart(body, "^TNX")
    assert row["value"] == 4.321
    assert row["previous"] == 4.20
    assert row["asof"] == 1234


class _Journal:
    def active_trade(self):
        return {"id": 7, "instrument": "NAS100", "direction": "long"}


class _Runtime:
    def status(self):
        return {
            "contract_version": "g1s-test",
            "horizons": [{"horizon_minutes": 60, "resolved_n": 42,
                          "effective_n": 31, "edge_candidate_n": 3}],
        }


class _Management:
    def status(self):
        return {"status": "EARLY", "unique_trade_n": 12}

    def edge(self):
        return {"available": True, "effective_n": 8, "lift_r": 0.04}


def test_edge_universe_reuses_active_edge_weight_and_exact_feature_ids(monkeypatch):
    active = {
        "available": True,
        "matched_structured_signal_n": 2,
        "supporting_position_n": 2,
        "opposing_position_n": 0,
        "strict_supporting_position_n": 2,
        "strict_opposing_position_n": 0,
        "matched_groups": [
            {"target_family": "RETURN", "target_id": "RETURN_60M",
             "signal_horizon_minutes": 60, "matched_n": 1,
             "strict_matched_n": 1, "net_vote": 1, "net_vote_ratio": 1.0},
            {"target_family": "PATH_FIRST_TOUCH", "target_id": "FIRST_TOUCH_120M",
             "signal_horizon_minutes": 120, "matched_n": 1,
             "strict_matched_n": 1, "net_vote": 1, "net_vote_ratio": 1.0},
        ],
    }
    monkeypatch.setattr(
        universe.active_edge_ai, "build_active_edge_context",
        lambda engine, snapshot: active,
    )
    monkeypatch.setattr(
        universe, "_latest_frozen_context",
        lambda engine, snapshot: {"observation_t0": snapshot["captured_ts"]})
    monkeypatch.setattr(
        universe, "canonical_current_feature_map",
        lambda frozen, instrument: {
            "vol.rv15_over_rv60": {
                "feature_id": "vol.rv15_over_rv60", "value": 1.18,
                "available": True, "stale": False,
            },
            "option_dynamics.gex_acceleration": {
                "feature_id": "option_dynamics.gex_acceleration", "value": -0.3,
                "available": True, "stale": False,
            },
            "option.iv": {
                "feature_id": "option.iv", "value": None,
                "available": False, "stale": False,
            },
        })
    engine = SimpleNamespace(
        journal=_Journal(), market=SimpleNamespace(instrument_code="NAS100"),
        short_horizon=_Runtime(), management_local=_Management(),
        cross_asset_payload=lambda: {
            "version": "cross-asset-test",
            "available": True,
            "summary": {
                "systemic_coupling": 0.61,
                "network_tension": 0.22,
                "fragmentation": 0.10,
                "active_breaks_count": 2,
                "stress_pairs": 4,
            },
            "break_alerts": [{"source": "NAS100", "target": "VIX"}],
        },
    )

    payload = universe.build_edge_universe_payload(engine, now=1_700_000_100.0)

    assert payload["production_weight"]["weight_fraction"] == 0.40
    assert payload["production_weight"]["absolute_cap"] == 0.40
    assert payload["production_weight"]["hard_risk_override"] is False
    assert payload["production_weight"]["cvar_override"] is False
    assert payload["production_weight"]["automatic_execution"] is False
    assert payload["canonical_features"]["available_n"] == 2
    assert "vol.rv15_over_rv60" in payload["canonical_features"]["items"]
    assert "option_dynamics.gex_acceleration" in payload["canonical_features"]["items"]
    assert payload["g1s"]["horizons"][0]["resolved_n"] == 42
    assert payload["management_attribution"]["status"]["unique_trade_n"] == 12
    assert payload["cross_asset"]["available"] is True
    assert payload["cross_asset"]["summary"]["systemic_coupling"] == 0.61
    assert payload["cross_asset"]["summary"]["network_tension"] == 0.22
    assert payload["cross_asset"]["summary"]["fragmentation"] == 0.10
    assert payload["cross_asset"]["independent_vote"] is False
    assert payload["visualization_only"] is True
    assert payload["production_authority"] is False


def test_edge_universe_cache_shares_one_heavy_build_between_requests():
    release = Event()
    calls = []

    def builder(engine):
        calls.append(engine)
        release.wait(1.0)
        return {"captured_ts": 123.0, "instrument": "NAS100", "trade_id": 7}

    engine = SimpleNamespace(
        journal=_Journal(), market=SimpleNamespace(instrument_code="NAS100"))
    cache = universe.EdgeUniversePayloadCache(
        engine, builder=builder, ttl_sec=60.0, initial_wait_sec=1.0)

    cache.start_refresh()
    cache.start_refresh()
    release.set()
    payload = cache.get()
    cached = cache.get()

    assert len(calls) == 1
    assert payload["instrument"] == "NAS100"
    assert payload["transport"]["cache_state"] == "FRESH"
    assert cached["transport"]["cache_state"] == "FRESH"


def test_universe_page_is_standalone_route():
    app = FastAPI()
    install_visual_universe_page(app)
    paths = {route.path for route in app.routes}
    assert "/universe" in paths
