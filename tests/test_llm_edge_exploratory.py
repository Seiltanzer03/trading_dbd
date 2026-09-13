import json
import sqlite3
import threading
from types import SimpleNamespace

from seiltanzer import ai_verdict
from seiltanzer import llm_edge_exploratory as exploratory
from seiltanzer.llm_edge_lifecycle import publish_materialized_lifecycle_cache
from seiltanzer.llm_edge_researcher import _ensure_tables as ensure_research_tables


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


def _rows():
    rows = []
    for index in range(80):
        expanding = index % 2 == 0
        captured = 1_700_000_000.0 + index * 86_400.0
        rows.append({
            "observation_id": f"obs-{index}",
            "instrument": "EURUSD",
            "asset_family": "FX",
            "captured_ts": captured,
            "target_ts": captured + 900.0,
            "resolved_ts": captured + 900.0,
            "horizon_minutes": 15,
            "direction_label": "UP" if expanding else "DOWN",
            "terminal_log_return": 0.01 if expanding else -0.01,
            "mfe_log_return": 0.012 if expanding else 0.002,
            "mae_log_return": -0.002 if expanding else -0.012,
            "path_quality_status": "complete",
            "ede_features": {
                "regime.volatility": "EXPANDING" if expanding else "CONTRACTING",
                "vol.rv_60m": 0.02,
            },
        })
    return rows


def test_first_touch_proxy_abstains_when_order_is_unknown():
    one_side = {
        "mfe_log_return": 0.02,
        "mae_log_return": -0.001,
        "path_quality_status": "complete",
    }
    both_sides = {**one_side, "mae_log_return": -0.02}
    assert exploratory._first_touch_value(
        one_side, "FIRST_TOUCH:up_1s_down_0p5s", 0.01) == "UP_FIRST"
    assert exploratory._first_touch_value(
        both_sides, "FIRST_TOUCH:up_1s_down_0p5s", 0.01) is None


def test_evaluate_all_persists_non_authoritative_rolling_advantage(monkeypatch):
    runtime = Runtime()
    ensure_research_tables(runtime)
    with runtime._conn:
        runtime._conn.execute(
            "CREATE TABLE g1s_resolutions(observation_id TEXT PRIMARY KEY,resolved_ts REAL)"
        )
        runtime._conn.execute(
            "INSERT INTO g1s_resolutions VALUES(?,?)", ("obs-79", 1_800_000_000.0)
        )
        runtime._conn.execute(
            """INSERT INTO llm_edge_hypotheses(
                hypothesis_id,first_run_id,first_observation_id,first_snapshot_sha256,
                name,target_id,target_family,horizon_minutes,conditions_json,rationale,
                source,status,evaluation_state,created_ts
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "hyp-1", "run-1", "obs-1", "s" * 64, "vol expansion",
                "DIRECTION", "DIRECTION", 15,
                json.dumps([{
                    "feature_id": "regime.volatility",
                    "kind": "categorical",
                    "state": "EXPANDING",
                }]),
                "test", "LLM", "RESEARCH", "INSUFFICIENT_DATA", 1000.0,
            ),
        )

    class Adapter:
        def __init__(self, runtime, available_asof):
            pass

        def rows(self, *, resolved_only, strict, horizon_minutes):
            assert resolved_only is True
            assert strict is False
            return _rows() if horizon_minutes == 15 else []

    monkeypatch.setattr(exploratory, "ProspectiveFeatureAdapter", Adapter)
    report = exploratory.evaluate_all(runtime, now=1_900_000_000.0)
    assert report["status"] == "OK"
    assert report["advantage_n"] == 1

    stored = exploratory.status(runtime)
    assert stored["advantage_n"] == 1
    assert stored["production_authority"] is False
    assert stored["position_manager_weight_cap"] == 0.15
    assert stored["position_manager_weight_requires"] == "LIMITED_AND_CURRENT_T0_MATCH"

    row = runtime._conn.execute(
        "SELECT result_json FROM llm_edge_exploratory_evaluations WHERE hypothesis_id='hyp-1'"
    ).fetchone()
    result = json.loads(row[0])
    assert result["status"] == "EARLY_ADVANTAGE"
    assert result["selected_test_n"] >= 3
    assert result["automatic_execution"] is False


def test_ai_context_includes_only_early_advantages_with_bounded_authority():
    runtime = Runtime()
    publish_materialized_lifecycle_cache(runtime, json.dumps({
        "research_hypotheses": [
            {
                "hypothesis_id": "adv", "name": "advantage", "target": "DIRECTION",
                "horizon_minutes": 15, "conditions": [],
                "exploratory_verdict": {
                    "status": "EARLY_ADVANTAGE", "confidence": "VERY_LOW",
                    "selected_test_n": 5, "evaluated_fold_count": 1,
                    "primary_improvement": 0.01, "q_value": 0.8,
                },
            },
            {
                "hypothesis_id": "bad", "name": "disadvantage", "target": "DIRECTION",
                "horizon_minutes": 15, "conditions": [],
                "exploratory_verdict": {"status": "EARLY_DISADVANTAGE"},
            },
        ],
    }))
    context = ai_verdict._compact_exploratory_verdicts(
        SimpleNamespace(short_horizon=runtime))
    assert [item["hypothesis_id"] for item in context["items"]] == ["adv"]
    assert context["position_manager_weight_cap"] == 0.15
    assert context["may_influence_policy_selection"] is True
    assert context["production_authority"] is False
    assert context["may_independently_trigger_exit_or_close"] is False
