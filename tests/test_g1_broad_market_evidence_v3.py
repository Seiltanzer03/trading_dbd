from __future__ import annotations

import math
import sqlite3
import threading

import pytest

from seiltanzer.g1_broad_market_evidence_v3 import (
    MARKET_EVIDENCE_V3,
    build_market_evidence_v3,
)


class _Cache:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def chain_snapshots(self, proxy, limit=120):
        assert proxy == "QQQ"
        return self.snapshots[-limit:]


class _Engine:
    def __init__(self, snapshots):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.cache = _Cache(snapshots)
        self._conn.execute("""
            CREATE TABLE passive_market_bars(
                instrument TEXT NOT NULL,
                bar_start_ts REAL NOT NULL,
                bar_end_ts REAL NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL,
                low REAL NOT NULL, close REAL NOT NULL,
                source TEXT, quality REAL, kind TEXT NOT NULL,
                created_ts REAL NOT NULL,
                PRIMARY KEY(instrument,bar_start_ts))
        """)


def _option_snapshot(ts: float, index: int, *, absurd=False):
    iv = 9.0 if absurd else 0.22 + index * 0.004
    spot = 100.0 + index * 0.10
    return {
        "ts": ts,
        "proxy_spot": spot,
        "implied_move": {"sigma_annual": iv},
        "skew": {"value": -0.06 + index * 0.002},
        "term": {"slope": -0.10 + index * 0.003},
        "greek_context": {
            "available": True,
            "net_delta_oi_weighted": 0.05 + index * 0.01,
            "vega_per_spot_oi_weighted": 0.12 + index * 0.002,
            "vanna_oi_weighted": -0.03 + index * 0.002,
            "charm_per_day_oi_weighted": -0.004 + index * 0.0002,
        },
        "gex": {
            "strikes": [90, 95, 100, 105, 110],
            "net": [-(2.2-index*.03), -1.1, 0.25+index*.02, 1.4, 2.3+index*.04],
            "zero_flip": 99.2 + index * 0.15,
        },
    }


def _seed_bars(engine: _Engine, captured: float, minutes: int = 480):
    start = captured - minutes * 60.0
    rows = []
    for i in range(minutes):
        bar_start = start + i * 60.0
        bar_end = bar_start + 60.0
        trend = 0.010 * i
        cyc1 = 0.65 * math.sin(i / 17.0)
        cyc2 = 0.22 * math.sin(i / 5.0)
        close = 100.0 + trend + cyc1 + cyc2
        prior = 100.0 + 0.010 * max(i-1, 0) + 0.65 * math.sin(max(i-1, 0)/17.0) + 0.22 * math.sin(max(i-1, 0)/5.0)
        high = max(prior, close) + 0.06
        low = min(prior, close) - 0.06
        rows.append((
            "NAS100", bar_start, bar_end, prior, high, low, close,
            "test_direct_1m", 0.88, "direct", captured,
        ))
    engine._conn.executemany(
        "INSERT INTO passive_market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    engine._conn.commit()


def _features(captured: float, *, cross_asof: float | None = None):
    cross_asof = captured - 30 if cross_asof is None else cross_asof
    assets = ["NAS", "SP500", "VXN", "GOLD"]
    matrix = [
        [1.0, .81, -.58, .22],
        [.81, 1.0, -.63, .18],
        [-.58, -.63, 1.0, -.12],
        [.22, .18, -.12, 1.0],
    ]
    baseline = [
        [1.0, .72, -.50, .20],
        [.72, 1.0, -.55, .15],
        [-.50, -.55, 1.0, -.10],
        [.20, .15, -.10, 1.0],
    ]
    delta = [[matrix[i][j]-baseline[i][j] for j in range(4)] for i in range(4)]
    return {
        "volatility": {"reference_volatility_annual": 0.20},
        "option_distribution": {"proxy": "QQQ", "available": True},
        "cross_asset": {
            "available": True,
            "data": {
                "asof": cross_asof,
                "assets": assets,
                "matrix_short": matrix,
                "matrix_baseline": baseline,
                "matrix_delta": delta,
                "dynamic_pairs": [{"source": "NAS", "target": "SP500", "delta_15m": .09}],
            },
        },
    }


def _assert_no_future_timestamps(value, captured: float, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "ts" or key.endswith("_ts") or key in {"timestamp", "as_of"}:
                if isinstance(child, (int, float)):
                    assert float(child) <= captured + 1e-6, child_path
            _assert_no_future_timestamps(child, captured, child_path)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _assert_no_future_timestamps(child, captured, f"{path}[{i}]")


def test_v3_collects_rich_pre_t0_state_without_enabling_training():
    captured = 2_000_000_000.0
    snapshots = [
        _option_snapshot(captured - (45-i*5)*60.0, i)
        for i in range(9)
    ]
    # This value is intentionally absurd and after T0. It must never become the
    # latest option state just because it exists in the cache.
    snapshots.append(_option_snapshot(captured + 60.0, 99, absurd=True))
    engine = _Engine(snapshots)
    _seed_bars(engine, captured)

    block = build_market_evidence_v3(
        engine, "NAS100", captured, 104.0, _features(captured),
        provenance={"options": {"quality": 0.72}},
    )

    assert block["contract_version"] == MARKET_EVIDENCE_V3
    assert block["semantics"]["collect_wide_train_controlled"] is True
    assert block["semantics"]["production_authority"] is False
    assert all(row["training_enabled"] is False
               for row in block["feature_families"].values())
    assert all(row["ablation_required"] is True
               for row in block["feature_families"].values())

    price = block["price_volatility"]
    assert price["available"] is True
    assert price["ret_30m"] is not None
    assert price["ret_120m"] is not None
    assert price["realized_vol_240m"] is not None
    assert price["return_dynamics"]["estimator"] == "ewls_huber_irls"
    assert price["rv_dynamics"]["estimator"] == "ewls_huber_irls"

    option = block["option_static"]
    assert option["available"] is True
    assert option["iv"] < 1.0  # proves the future 9.0 IV snapshot was ignored
    assert option["quality"]["source_quality"] == pytest.approx(0.72)
    assert block["option_dynamics"]["estimator"] == \
        "existing_option_shadow_state.robust_derivative"
    assert block["option_dynamics"]["derivatives"]["iv"]["available"] is True
    assert block["option_dynamics"]["derivatives"]["vanna"]["available"] is True

    gex = block["gex"]
    assert gex["available"] is True
    assert gex["dealer_positioning_claim"] is False
    assert gex["field_score"] is not None
    assert gex["dynamics"]["force_score"]["available"] is True

    cross = block["cross_asset"]
    assert cross["available"] is True
    assert cross["instrument_node"]["id"] == "NAS"
    assert cross["systemic_coupling"] is not None
    assert cross["causal_direction_claim"] is False
    assert len(cross["full_observed_graph"]["links"]) == 6

    macro = block["macro"]
    assert macro["available"] is True
    assert macro["transition_velocity"] is not None
    assert macro["velocity_vector"]
    assert macro["vol_index_available"] is False

    wavelet = block["wavelet"]
    assert wavelet["available"] is True
    assert wavelet["dominant_period_hours"] is not None
    assert wavelet["phase_stability"] is not None
    assert "energy_transfer" in wavelet

    _assert_no_future_timestamps(block, captured)


def test_v3_rejects_future_cross_asset_source_without_freezing_future_timestamp():
    captured = 2_000_000_000.0
    snapshots = [_option_snapshot(captured-(40-i*5)*60.0, i) for i in range(9)]
    engine = _Engine(snapshots)
    _seed_bars(engine, captured)
    block = build_market_evidence_v3(
        engine, "NAS100", captured, 104.0,
        _features(captured, cross_asof=captured+120.0),
        provenance={"options": {"quality": 0.65}},
    )
    assert block["cross_asset"]["available"] is False
    assert block["cross_asset"]["reason"] == "cross_asset_after_t0"
    assert "source_ts" not in block["cross_asset"]
    _assert_no_future_timestamps(block, captured)
