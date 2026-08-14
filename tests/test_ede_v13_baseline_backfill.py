from __future__ import annotations

import json
import math
import sqlite3
import threading

from seiltanzer.edge_discovery.baseline_rows import baseline_eligible_rows
from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute(
            "CREATE TABLE passive_market_bars ("
            "instrument TEXT, bar_end_ts REAL, high REAL, low REAL, close REAL, "
            "quality REAL, created_ts REAL)"
        )


def _runtime_with_bars(t0: float = 10_000.0) -> _Runtime:
    runtime = _Runtime()
    # 13 completed 5m bars cover the full pre-T0 hour.
    for index in range(13):
        end = t0 - 3600.0 + index * 300.0
        close = 100.0 + index
        runtime._conn.execute(
            "INSERT INTO passive_market_bars VALUES (?,?,?,?,?,?,?)",
            ("NAS100", end, close + 0.25, close - 0.25, close, 1.0, end),
        )
    # Neither a future bar nor a bar written after the immutable observation
    # record may alter the recovered T0 features.
    runtime._conn.execute(
        "INSERT INTO passive_market_bars VALUES (?,?,?,?,?,?,?)",
        ("NAS100", t0 + 300.0, 9999.0, 9998.0, 9998.5, 1.0, t0 + 300.0),
    )
    runtime._conn.execute(
        "INSERT INTO passive_market_bars VALUES (?,?,?,?,?,?,?)",
        ("NAS100", t0, 8889.0, 8887.0, 8888.0, 1.0, t0 + 1.0),
    )
    runtime._conn.commit()
    return runtime


def _source(*, t0: float = 10_000.0, frozen: dict | None = None) -> dict:
    return {
        "instrument": "NAS100",
        "captured_ts": t0,
        "created_ts": t0,
        "horizon_minutes": 15,
        "frozen_features_json": json.dumps(frozen or {}),
    }


def test_missing_baseline_returns_are_recovered_from_only_causal_retained_bars():
    runtime = _runtime_with_bars()
    adapter = ProspectiveFeatureAdapter(runtime, available_asof=20_000.0)
    values, rejected, provenance = adapter._feature_values(_source(), strict=True)

    assert "price.ret_5m" not in rejected
    assert "price.ret_15m" not in rejected
    assert math.isclose(values["price.ret_5m"].value, math.log(112.0 / 111.0))
    assert math.isclose(values["price.ret_15m"].value, math.log(112.0 / 109.0))
    assert provenance["price.ret_5m"]["provenance"] == "CAUSAL_RECOMPUTED"
    assert provenance["price.ret_5m"]["future_points_used"] is False
    assert provenance["price.ret_5m"]["bar_end_ts_lte_t0"] is True
    assert provenance["price.ret_5m"]["bar_created_ts_lte_capture_record"] is True

    accepted, gate = baseline_eligible_rows([{
        "features": {
            "ret_5m": values["price.ret_5m"].value,
            "ret_15m": values["price.ret_15m"].value,
        }
    }])
    assert len(accepted) == 1
    assert gate["eligible_rows"] == 1
    assert gate["excluded_rows"] == 0


def test_frozen_t0_baseline_values_remain_authoritative_over_backfill():
    runtime = _runtime_with_bars()
    adapter = ProspectiveFeatureAdapter(runtime, available_asof=20_000.0)
    frozen = {
        "g1s_evidence_v3": {
            "price_volatility": {
                "ret_5m": 0.123,
                "ret_15m": 0.456,
                "quality": {"source_ts": 10_000.0, "source_quality": 1.0, "stale": False},
            }
        }
    }
    values, _, provenance = adapter._feature_values(
        _source(frozen=frozen), strict=True)

    assert values["price.ret_5m"].value == 0.123
    assert values["price.ret_15m"].value == 0.456
    assert provenance["price.ret_5m"]["provenance"] == "FROZEN_T0"
    assert provenance["price.ret_15m"]["provenance"] == "FROZEN_T0"
