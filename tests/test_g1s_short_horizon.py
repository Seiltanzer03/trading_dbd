from __future__ import annotations

import json
import time

import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_short_horizon_runtime import (
    G1S_CONTRACT_VERSION,
    ShortHorizonRuntime,
)
from seiltanzer.passive_learning import PassiveLearningEngine
from seiltanzer import measurement_q_runtime as _mq


class _Engine:
    def __init__(self, passive):
        self.passive = passive


@pytest.fixture
def runtime(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    passive = PassiveLearningEngine(
        str(tmp_path / "trades.db"),
        Settings(demo=False, data_dir=str(tmp_path)),
        cache,
    )
    engine = _Engine(passive)
    rt = ShortHorizonRuntime(engine)
    yield rt, passive
    passive.close(); cache.close()


def _capture(passive, ts, *, price=100.0, instrument="XAU"):
    features = {
        "source_observation_ts": ts,
        "price_state": {"price": price, "ts": ts, "available": True},
        "market_regime": "test-regime",
        "session": "OPEN",
        "volatility": {"reference_volatility_annual": 0.20},
    }
    forecast = {
        "reference_volatility_annual": 0.20,
        "forecast_created_ts": ts,
    }
    provenance = {
        "price": {"source": "test-direct", "age_sec": 0.0,
                  "quality": 1.0, "kind": "direct"},
        "options": {"source": None, "age_sec": None,
                    "quality": 0.0, "kind": "unavailable"},
    }
    # Exercise the real F.3.2a background-capture contract, not trigger_reason=test:
    # test-origin rows are intentionally ineligible for research training.
    old_time = _mq.time.time
    passive._f32a_background_capture = True
    _mq.time.time = lambda: float(ts)
    try:
        return passive.capture_observation(
            instrument=instrument, captured_ts=ts, market_price=price,
            features=features, forecast=forecast, provenance=provenance,
            trigger_reason="cadence", evidence_eligible=True,
            observation_origin="background_collector",
        )
    finally:
        passive._f32a_background_capture = False
        _mq.time.time = old_time


def test_existing_prospective_fixed_horizons_become_fast_g1s_evidence(runtime):
    rt, passive = runtime
    ts = 1_700_000_000.0  # weekday; XAU session contract is 24h on weekdays.
    source_ids = _capture(passive, ts)
    assert len(source_ids) >= 5
    assert rt.materialize_new() == 5

    before = rt._conn.execute(
        "SELECT observation_id,t0_sha256,training_eligible FROM g1s_observations "
        "WHERE horizon_minutes=15").fetchone()
    assert before is not None
    assert before["training_eligible"] == 1

    passive.record_market_point("XAU", ts, 100.0,
                                source="test-direct", quality=1.0, kind="direct")
    for minute in range(5, 61, 5):
        passive.record_market_point(
            "XAU", ts + minute * 60.0, 100.0 + minute / 100.0,
            source="test-direct", quality=1.0, kind="direct")

    source_resolution = passive.resolve_due(now=ts + 60 * 60.0)
    assert source_resolution["resolved"] >= 3
    assert rt.resolve_new() >= 3

    status = rt.status()
    by_horizon = {row["horizon_minutes"]: row for row in status["horizons"]}
    assert by_horizon[15]["raw_resolved"] >= 1
    assert by_horizon[30]["raw_resolved"] >= 1
    assert by_horizon[60]["raw_resolved"] >= 1
    assert status["authority"]["production_authority"] is False
    assert status["authority"]["edge_claim_allowed"] is False

    after = rt._conn.execute(
        "SELECT t0_sha256 FROM g1s_observations WHERE observation_id=?",
        (before["observation_id"],)).fetchone()
    assert after["t0_sha256"] == before["t0_sha256"]
    resolution = rt._conn.execute(
        "SELECT resolution_json FROM g1s_resolutions WHERE observation_id=?",
        (before["observation_id"],)).fetchone()
    payload = json.loads(resolution["resolution_json"])
    assert payload["future_data_source"] == "independently_resolved_passive_market_observation"


def test_g1s_never_materializes_option_native_row_as_short_horizon(runtime):
    rt, passive = runtime
    ts = 1_700_000_000.0
    _capture(passive, ts)
    # Make one option-native-looking source row by copying a fixed row but changing
    # only its frozen forecast semantics before G1S sees it. Passive source rows are
    # append-only, so insert a distinct row instead of mutating the frozen T0.
    src = passive._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 LIMIT 1").fetchone()
    forecast = json.loads(src["forecast_json"])
    forecast["horizon_kind"] = "option_native_expiry"
    values = dict(src)
    values["observation_id"] = "native-test-row"
    values["anchor_group_id"] = "native-test-anchor"
    values["horizon_minutes"] = 17
    values["forecast_json"] = json.dumps(forecast, sort_keys=True, separators=(",", ":"))
    columns = [r[1] for r in passive._conn.execute(
        "PRAGMA table_info(passive_market_observations)").fetchall()]
    insert_cols = [c for c in columns if c in values and c != "resolved_ts" and c != "outcome_json"]
    passive._conn.execute(
        f"INSERT INTO passive_market_observations({','.join(insert_cols)}) "
        f"VALUES({','.join('?' for _ in insert_cols)})",
        [values[c] for c in insert_cols],
    )
    passive._conn.commit()
    assert rt.materialize_new() == 5
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_observations WHERE source_observation_id='native-test-row'"
    ).fetchone()[0] == 0


def test_overlap_dependency_groups_do_not_equal_raw_n(runtime):
    rt, _ = runtime
    base = 1_700_000_040.0
    rows = [
        {"instrument": "XAU", "horizon_minutes": 60,
         "captured_ts": base + i * 60.0, "direction_label": "UP" if i % 2 else "DOWN"}
        for i in range(10)
    ]
    evidence = rt._evidence(rows)
    assert evidence["raw_resolved"] == 10
    assert evidence["effective_n"] < evidence["raw_resolved"]
    assert evidence["fit_allowed"] is False


def test_no_model_backfill_for_observation_created_before_model(runtime):
    rt, passive = runtime
    ts = 1_700_000_000.0
    _capture(passive, ts)
    rt.materialize_new()
    old = rt._conn.execute(
        "SELECT observation_id FROM g1s_observations WHERE horizon_minutes=15 LIMIT 1"
    ).fetchone()[0]
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_shadow_predictions WHERE observation_id=?", (old,)
    ).fetchone()[0] == 0
    # Fitting later is allowed, but old T0 rows never receive retrospective
    # predictions because prediction creation happens only during T0 materialization.
    assert rt.status()["contract_version"] == G1S_CONTRACT_VERSION
