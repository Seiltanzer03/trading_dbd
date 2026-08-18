import json
import sqlite3
import threading

from seiltanzer.g1_historical_analog import historical_analogs
from seiltanzer.g1_historical_analog_analyst import explain_historical_analogs
from seiltanzer.g1_short_horizon_feature_contract_v2 import (
    FEATURE_CONTRACT_V2,
    V2_FEATURE_SETS,
    _v2_values,
)


class FakeRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY,
                captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL,
                instrument TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                training_eligible INTEGER NOT NULL,
                price_quality REAL,
                option_quality REAL,
                frozen_features_json TEXT NOT NULL,
                frozen_forecast_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY,
                resolved_ts REAL NOT NULL,
                terminal_log_return REAL NOT NULL,
                direction_label TEXT NOT NULL,
                mfe_log_return REAL,
                mae_log_return REAL
            )
        """)

    @staticmethod
    def _feature_vector(row, feature_set):
        values = _v2_values(row)
        vector = [0.0 if values.get(name) is None else float(values[name])
                  for name in V2_FEATURE_SETS[feature_set]]
        return vector, values


def features(seed: float, *, missing_option: bool = False):
    option = {
        "available": not missing_option,
        "iv_rv_ratio": None if missing_option else 1.0 + seed * 0.01,
        "skew": None if missing_option else seed * 0.001,
        "term_slope": None if missing_option else seed * 0.0001,
        "gex_zero_flip_log_moneyness": None if missing_option else seed * 0.0002,
        "gex_net_balance": None if missing_option else seed * 0.002,
        "greek_context": {
            "available": not missing_option,
            "net_delta_oi_weighted": None if missing_option else seed * 0.003,
            "vega_per_spot_oi_weighted": None if missing_option else 0.2 + seed * 0.001,
            "vanna_oi_weighted": None if missing_option else seed * 0.0004,
            "charm_per_day_oi_weighted": None if missing_option else seed * 0.00005,
        },
    }
    block = {
        "contract_version": FEATURE_CONTRACT_V2,
        "intraday": {
            "available": True,
            "ret_5m": seed * 0.0001,
            "ret_15m": seed * 0.0002,
            "ret_60m": seed * 0.0003,
            "realized_vol_15m": 0.01 + seed * 0.00001,
            "realized_vol_60m": 0.02 + seed * 0.00001,
        },
        "wavelet": {
            "available": True,
            "low_pct": 50.0 + seed * 0.01,
            "high_pct": 50.0 - seed * 0.01,
            "resonance": 0.5 + seed * 0.001,
        },
        "option_context": option,
        "cross_asset": {
            "available": True,
            "primary_corr": 0.4 + seed * 0.001,
            "primary_delta": seed * 0.0001,
            "risk_corr": -0.2 + seed * 0.001,
            "risk_delta": -seed * 0.0001,
        },
    }
    return json.dumps({"g1s_evidence_v2": block}, sort_keys=True)


def add(runtime, obs_id, captured, *, instrument="USDCAD", horizon=60,
        resolved=None, direction="UP", seed=0.0, missing_option=False):
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
        (obs_id, captured, captured + horizon * 60, instrument, horizon, 1,
         0.99, 0.95, features(seed, missing_option=missing_option), "{}"),
    )
    if resolved is not None:
        sign = 1.0 if direction == "UP" else (-1.0 if direction == "DOWN" else 0.0)
        runtime._conn.execute(
            "INSERT INTO g1s_resolutions VALUES(?,?,?,?,?,?)",
            (obs_id, resolved, sign * 0.01, direction, 0.015, -0.008),
        )
    runtime._conn.commit()


def test_analogs_are_causal_same_instrument_same_horizon_and_resolved_by_t0():
    runtime = FakeRuntime()
    for index in range(10):
        add(runtime, f"old-{index}", 1000 + index * 100,
            resolved=1700 + index * 100,
            direction="UP" if index % 2 == 0 else "DOWN", seed=float(index))
    # Older capture but result was not known at current T0: must never leak in.
    add(runtime, "future-resolution", 2500, resolved=20_000, seed=0.1)
    # Valid-looking but different market/horizon: not an analog in MVP.
    add(runtime, "other-instrument", 2400, instrument="NAS100", resolved=3000, seed=0.1)
    add(runtime, "other-horizon", 2300, horizon=120, resolved=3000, seed=0.1)
    add(runtime, "current", 10_000, seed=4.2)

    result = historical_analogs(runtime, "current", k=8)

    assert result["status"] == "OK"
    assert result["analog_n"] == 8
    assert result["causal_rules"]["candidate_outcome_resolved_by_current_t0"] is True
    ids = {item["observation_id"] for item in result["analogs"]}
    assert "future-resolution" not in ids
    assert "other-instrument" not in ids
    assert "other-horizon" not in ids
    assert all(item["resolved_ts"] <= 10_000 for item in result["analogs"])
    assert result["production_authority"] is False
    assert result["may_change_position_manager"] is False


def test_analog_report_is_deterministic_and_exposes_canonical_outcomes():
    runtime = FakeRuntime()
    for index in range(12):
        add(runtime, f"old-{index}", 1000 + index * 100, resolved=3000 + index,
            direction="UP" if index < 7 else "DOWN", seed=float(index))
    add(runtime, "current", 10_000, seed=5.1)

    first = historical_analogs(runtime, "current", k=10)
    second = historical_analogs(runtime, "current", k=10)

    assert first == second
    assert first["up_n"] + first["down_n"] + first["flat_n"] == 10
    assert first["mean_terminal_log_return"] is not None
    assert first["median_mfe_log_return"] == 0.015
    assert first["median_mae_log_return"] == -0.008
    assert first["analog_set_sha256"]
    assert first["top_feature_differences"]


def test_insufficient_feature_coverage_fails_soft_instead_of_fabricating_zero_signal():
    runtime = FakeRuntime()
    for index in range(10):
        add(runtime, f"old-{index}", 1000 + index * 100, resolved=4000,
            seed=float(index))
    add(runtime, "current", 10_000, seed=4.0, missing_option=True)

    result = historical_analogs(runtime, "current", k=8)

    assert result["status"] in {"INSUFFICIENT_FEATURES", "INSUFFICIENT_ANALOGS"}
    assert result["production_authority"] is False
    assert "p_up" not in result


def test_unknown_or_old_contract_is_unavailable():
    runtime = FakeRuntime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("legacy", 10_000, 13_600, "USDCAD", 60, 1, 0.99, 0.95,
         json.dumps({}), "{}"),
    )
    runtime._conn.commit()

    result = historical_analogs(runtime, "legacy")

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "FEATURE_CONTRACT_UNAVAILABLE"


def test_analog_llm_is_on_demand_compact_and_cached_by_fixed_analog_set():
    runtime = FakeRuntime()
    for index in range(12):
        add(runtime, f"old-{index}", 1000 + index * 100, resolved=3000 + index,
            direction="UP" if index < 8 else "DOWN", seed=float(index))
    add(runtime, "current", 10_000, seed=5.0)
    calls = []

    def provider(summary, model):
        calls.append((summary, model))
        assert len(summary["analogs"]) <= 20
        assert len(summary["top_feature_differences"]) <= 8
        assert "warning" in summary
        return "Исторические аналоги умеренно согласованы; вывод остаётся описательным."

    first = explain_historical_analogs(runtime, "current", k=10, provider=provider)
    second = explain_historical_analogs(
        runtime, "current", k=10,
        provider=lambda *_: (_ for _ in ()).throw(AssertionError("cached explanation must be reused")),
    )

    assert first["status"] == "OK"
    assert first["cache_hit"] is False
    assert second["status"] == "OK"
    assert second["cache_hit"] is True
    assert len(calls) == 1
    assert first["analog_set_sha256"] == second["analog_set_sha256"]
    assert first["production_authority"] is False
    assert first["may_change_position_manager"] is False


def test_analog_llm_never_runs_when_deterministic_analog_report_is_unavailable():
    runtime = FakeRuntime()
    add(runtime, "current", 10_000, seed=5.0)

    result = explain_historical_analogs(
        runtime, "current", provider=lambda *_: (_ for _ in ()).throw(AssertionError("must not call LLM")),
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "ANALOG_REPORT_UNAVAILABLE"
    assert result["production_authority"] is False
