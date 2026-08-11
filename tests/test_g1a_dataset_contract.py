import datetime as dt
import json
import math
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from seiltanzer.config import Settings
from seiltanzer.g1_dataset_runtime import (
    G1_DATASET_CONTRACT_VERSION,
    G1_EFFECTIVE_N_CONTRACT_VERSION,
)
from seiltanzer.g1_routes import install_g1_dataset_routes
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION
from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION, PassiveLearningEngine


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fixed_forecast(horizon_minutes=15):
    return {
        "version": "passive-forecast-f32-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "horizon_kind": "fixed_trading_time",
        "horizon_minutes": horizon_minutes,
        "probability_measure": "unavailable",
        "q_source_contract": "unavailable",
        "q_terminal_distribution_available": False,
        "q_first_touch_available": False,
        "physical_probability_published": False,
        "variance_clock_version": "variance-clock-f31-v1",
    }


def _q_forecast(captured, target, *, transform="direct", source="FXC", instrument="USDCAD"):
    return {
        "version": "passive-forecast-f32-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "horizon_kind": "option_native_expiry",
        "horizon_minutes": int(round((target - captured) / 60)),
        "probability_measure": "risk_neutral_Q_terminal",
        "q_source_contract": OPTION_Q_CONTRACT_VERSION,
        "q_terminal_distribution_available": True,
        "q_first_touch_available": False,
        "physical_probability_published": False,
        "terminal_q_cdf": {
            "support": [-0.2, -0.1, 0.0, 0.1, 0.2],
            "cdf": [0.0, 0.15, 0.50, 0.85, 1.0],
        },
        "source_expiry_ts_utc": target,
        "calendar_ttm_seconds": target - captured,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "q_source_instrument": source,
        "q_target_instrument": instrument,
        "proxy_symbol": source,
        "proxy_transform": transform,
    }


def _outcome(target, *, clean=True, authoritative=True, lookahead=False, pit=None, price=101.0):
    return {
        "version": "passive-resolver-f32a-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "future_log_return": math.log(price / 100.0),
        "terminal": {
            "terminal_price": price,
            "terminal_price_ts": target,
            "terminal_age_to_target_sec": 0.0,
            "terminal_lookahead_used": lookahead,
            "terminal_authoritative": authoritative,
            "clean_label": clean,
            "terminal_log_return": math.log(price / 100.0),
            "terminal_pit_q": pit,
        },
        "first_touch": {
            "label": "no_touch",
            "clean_label": False,
            "authoritative_path": False,
        },
    }


def _insert_resolved(
    engine,
    observation_id,
    *,
    anchor,
    captured,
    target,
    resolved=None,
    instrument="NAS100",
    horizon=15,
    forecast=None,
    outcome=None,
    source_schema=PASSIVE_SCHEMA_VERSION,
    origin="background_collector",
    evidence=1,
    replay=0,
    price_kind="direct",
    market_regime="NORMAL",
    session="OPEN",
):
    forecast = forecast or _fixed_forecast(horizon)
    outcome = outcome or _outcome(target)
    resolved = target if resolved is None else resolved
    features = {"measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION}
    with engine._lock, engine._conn:
        engine._conn.execute(
            "INSERT INTO passive_market_observations("
            "observation_id,anchor_group_id,captured_ts,target_ts,instrument,horizon_minutes,"
            "trigger_reason,market_price,price_source,price_age_sec,price_quality,price_kind,"
            "option_source,option_age_sec,option_quality,option_kind,market_regime,session,"
            "feature_contract_version,forecast_model_version,calibrator_version,scenario_version,"
            "features_json,forecast_json,evidence_eligible,resolution_status,resolved_ts,outcome_json,"
            "calendar_elapsed,trading_elapsed,market_open_fraction,retrospective_replay,"
            "observation_origin,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id, anchor, captured, target, instrument, horizon,
                "cadence", 100.0, "authoritative_test_feed", 0.0, 0.99, price_kind,
                "option_test", 0.0, 0.99, "proxy", market_regime, session,
                source_schema, "passive-forecast-f32-v1", "identity-only-unpromoted",
                "standardized-geometry-f31-v1", _json(features), _json(forecast), evidence,
                "resolved", resolved, _json(outcome), target - captured, target - captured, 1.0,
                replay, origin, captured,
            ),
        )


def test_integration_capture_resolve_membership_and_cut(tmp_path, monkeypatch):
    import seiltanzer.measurement_q_runtime as runtime

    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    ts = dt.datetime(2026, 8, 11, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    monkeypatch.setattr(runtime.time, "time", lambda: ts)
    engine._f32a_background_capture = True
    try:
        ids = engine.capture_observation(
            instrument="NAS100",
            captured_ts=ts,
            market_price=100.0,
            features={
                "source_observation_ts": ts,
                "volatility": {"reference_volatility_annual": 0.20, "volatility_status": "valid"},
                "market_regime": "NORMAL",
                "session": "OPEN",
            },
            forecast={"reference_volatility_annual": 0.20},
            provenance={"price": {"source": "TradingView OANDA", "kind": "direct", "quality": 0.98, "age_sec": 0.0}},
            trigger_reason="cadence",
            evidence_eligible=True,
        )
    finally:
        engine._f32a_background_capture = False
    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (ids[0],)
    ).fetchone())
    target = float(row["target_ts"])
    engine.record_market_point(
        "NAS100", target, 101.0, source="TradingView stream OANDA:NAS100USD",
        quality=0.99, kind="direct",
    )
    assert engine._resolve_one(row, target) == "resolved"
    engine._g1_sync_membership()
    membership = dict(engine._conn.execute(
        "SELECT * FROM g1_dataset_membership WHERE observation_id=?", (ids[0],)
    ).fetchone())
    assert membership["forecast_eval_eligible"] == 1
    assert membership["q_to_p_eligible"] == 0
    assert membership["dependency_group_id"] == row["anchor_group_id"]
    cut = engine.create_g1_dataset_cut(target + 1)
    assert cut["unique_observation_n"] == 1
    assert cut["effective_n"] == 1
    assert cut["status"] == "FROZEN"
    engine.close()


def test_q_eligibility_and_inverse_proxy_cohort_separation(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 2 * 86400
    direct = _q_forecast(captured, target, transform="direct")
    inverse = _q_forecast(captured + 10, target + 10, transform="inverse")
    _insert_resolved(
        engine, "q-direct", anchor="a1", captured=captured, target=target,
        instrument="USDCAD", horizon=2880, forecast=direct,
        outcome=_outcome(target, pit=0.6),
    )
    _insert_resolved(
        engine, "q-inverse", anchor="a2", captured=captured + 10, target=target + 10,
        instrument="USDCAD", horizon=2880, forecast=inverse,
        outcome=_outcome(target + 10, pit=0.4),
    )
    engine._g1_sync_membership()
    rows = [dict(r) for r in engine._conn.execute(
        "SELECT observation_id,q_to_p_eligible,first_touch_q_eligible,base_cohort_id,base_cohort_json "
        "FROM g1_dataset_membership ORDER BY observation_id"
    ).fetchall()]
    assert all(r["q_to_p_eligible"] == 1 for r in rows)
    assert all(r["first_touch_q_eligible"] == 0 for r in rows)
    assert rows[0]["base_cohort_id"] != rows[1]["base_cohort_id"]
    transforms = {json.loads(r["base_cohort_json"])["proxy_transform"] for r in rows}
    assert transforms == {"direct", "inverse"}
    engine.close()


def test_fixed_forecast_is_eval_eligible_but_not_q_to_p(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    _insert_resolved(engine, "fixed", anchor="a", captured=captured, target=target)
    engine._g1_sync_membership()
    row = dict(engine._conn.execute("SELECT * FROM g1_dataset_membership").fetchone())
    assert row["forecast_eval_eligible"] == 1
    assert row["q_to_p_eligible"] == 0
    assert "Q_SEMANTIC_UNAVAILABLE" in json.loads(row["q_exclusion_reasons_json"])
    engine.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"origin": "test"}, "NOT_BACKGROUND_COLLECTOR"),
        ({"origin": "manual"}, "NOT_BACKGROUND_COLLECTOR"),
        ({"replay": 1}, "RETROSPECTIVE_REPLAY"),
        ({"price_kind": "derived"}, "NON_DIRECT_T0_PRICE"),
        ({"source_schema": "passive-observation-f31-v1"}, "WRONG_SOURCE_SCHEMA"),
    ],
)
def test_quarantine_reasons_are_explicit(tmp_path, changes, reason):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    kwargs = dict(anchor="a", captured=captured, target=target)
    kwargs.update(changes)
    _insert_resolved(engine, "bad", **kwargs)
    engine._g1_sync_membership()
    row = dict(engine._conn.execute("SELECT * FROM g1_dataset_membership").fetchone())
    assert row["forecast_eval_eligible"] == 0
    assert reason in json.loads(row["exclusion_reasons_json"])
    report = engine.g1_dataset_exclusions()
    assert report["reason_counts"][reason] == 1
    engine.close()


def test_dirty_authority_and_lookahead_terminal_are_rejected(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    for index, outcome in enumerate((
        _outcome(captured + 900, clean=False),
        _outcome(captured + 1800, authoritative=False),
        _outcome(captured + 2700, lookahead=True),
    )):
        target = captured + (index + 1) * 900
        _insert_resolved(
            engine, f"bad-{index}", anchor=f"a{index}", captured=captured, target=target,
            outcome=outcome,
        )
    engine._g1_sync_membership()
    reasons = [json.loads(r[0]) for r in engine._conn.execute(
        "SELECT exclusion_reasons_json FROM g1_dataset_membership ORDER BY observation_id"
    ).fetchall()]
    flattened = {x for group in reasons for x in group}
    assert "TERMINAL_NOT_CLEAN" in flattened
    assert "TERMINAL_NOT_AUTHORITATIVE" in flattened
    assert "TERMINAL_LOOKAHEAD" in flattened
    engine.close()


def test_same_anchor_seven_horizons_do_not_inflate_aggregate_effective_n(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    for horizon in (15, 30, 60, 120, 240, 480, 1440):
        target = captured + horizon * 60
        _insert_resolved(
            engine, f"o-{horizon}", anchor="same-anchor", captured=captured,
            target=target, horizon=horizon, forecast=_fixed_forecast(horizon),
        )
    status = engine.g1_dataset_status()
    assert status["forecast_eval_eligible_n"] == 7
    assert status["unique_anchor_n"] == 1
    assert status["effective_n"] == 1
    assert status["effective_n_contract_version"] == G1_EFFECTIVE_N_CONTRACT_VERSION
    engine.close()


def test_overlapping_windows_have_conservative_effective_n(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    base = 1_800_000_000.0
    intervals = [
        (base, base + 4 * 3600),
        (base + 15 * 60, base + 4 * 3600 + 15 * 60),
        (base + 30 * 60, base + 4 * 3600 + 30 * 60),
        (base + 4 * 3600 + 30 * 60, base + 8 * 3600 + 30 * 60),
    ]
    for i, (captured, target) in enumerate(intervals):
        _insert_resolved(
            engine, f"o-{i}", anchor=f"a-{i}", captured=captured, target=target,
            horizon=240, forecast=_fixed_forecast(240),
        )
    cohorts = engine.g1_dataset_cohorts()["items"]
    assert len(cohorts) == 1
    assert cohorts[0]["raw_n"] == 4
    assert cohorts[0]["effective_n"] == 2
    engine.close()


def test_cutoff_uses_resolved_time_not_present_day_knowledge(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    resolved = target + 120
    _insert_resolved(
        engine, "late-resolution", anchor="a", captured=captured, target=target, resolved=resolved
    )
    engine._g1_sync_membership()
    early = engine.create_g1_dataset_cut(target + 60)
    late = engine.create_g1_dataset_cut(resolved + 1)
    assert early["unique_observation_n"] == 0
    assert late["unique_observation_n"] == 1
    engine.close()


def test_dataset_cut_is_deterministic_and_immutable(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    _insert_resolved(engine, "one", anchor="a", captured=captured, target=target)
    first = engine.create_g1_dataset_cut(target + 1)
    second = engine.create_g1_dataset_cut(target + 1)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["cut_id"] == second["cut_id"]
    with pytest.raises(Exception, match="immutable G1 dataset cut"):
        with engine._lock, engine._conn:
            engine._conn.execute("UPDATE g1_dataset_cuts SET status='CHANGED'")
    engine.close()


def test_source_mutation_is_detected_and_excluded_from_new_cut(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    _insert_resolved(engine, "one", anchor="a", captured=captured, target=target)
    engine._g1_sync_membership()
    with engine._lock, engine._conn:
        changed = _outcome(target, price=103.0)
        engine._conn.execute(
            "UPDATE passive_market_observations SET outcome_json=? WHERE observation_id='one'",
            (_json(changed),),
        )
    status = engine.g1_dataset_status()
    assert status["source_mutation_error_n"] == 1
    assert status["forecast_eval_eligible_n"] == 0
    cut = engine.create_g1_dataset_cut(target + 1)
    assert cut["unique_observation_n"] == 0
    engine.close()


def test_atomic_cut_rolls_back_partial_members(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    for i in range(3):
        row_captured = captured + i * 3600
        row_target = row_captured + 900
        _insert_resolved(
            engine, f"o-{i}", anchor=f"a-{i}", captured=row_captured, target=row_target,
        )
    engine._g1_sync_membership()
    cutoff = captured + 20_000
    with pytest.raises(RuntimeError, match="injected dataset cut failure"):
        engine.create_g1_dataset_cut(cutoff, _fail_after_members=1)
    with engine._lock:
        assert engine._conn.execute(
            "SELECT COUNT(*) FROM g1_dataset_cuts WHERE cutoff_ts=?", (cutoff,)
        ).fetchone()[0] == 0
        assert engine._conn.execute("SELECT COUNT(*) FROM g1_dataset_cut_members").fetchone()[0] == 0
    engine.close()


def test_authority_remains_research_only_even_with_samples(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    _insert_resolved(engine, "one", anchor="a", captured=captured, target=captured + 900)
    status = engine.g1_dataset_status()
    assert status["g1_stage"] == "G.1A"
    assert status["dataset_contract_version"] == G1_DATASET_CONTRACT_VERSION
    assert status["authority"] == "research_only"
    assert status["g1_training_allowed"] is False
    assert status["physical_probability_published"] is False
    assert status["promotion_allowed"] is False
    assert status["production_replacement_allowed"] is False
    assert status["sample_count_auto_promotion"] is False
    engine.close()


def test_g1_routes_are_installed_without_training_endpoint():
    app = FastAPI()
    passive = SimpleNamespace(
        g1_dataset_status=lambda: {},
        g1_dataset_cohorts=lambda: {},
        g1_dataset_exclusions=lambda: {},
        g1_dataset_cuts=lambda limit=20: {"limit": limit},
    )
    app.state.engine = SimpleNamespace(passive=passive)
    install_g1_dataset_routes(app)
    paths = {route.path for route in app.routes}
    assert "/api/research/g1/dataset/status" in paths
    assert "/api/research/g1/dataset/cohorts" in paths
    assert "/api/research/g1/dataset/exclusions" in paths
    assert "/api/research/g1/dataset/cuts" in paths
    assert all("train" not in path for path in paths if path.startswith("/api/research/g1/"))
