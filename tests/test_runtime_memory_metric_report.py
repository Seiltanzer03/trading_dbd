from __future__ import annotations

import json
import sqlite3
import threading

from seiltanzer import ai_runtime_report_v20 as report_v20
from seiltanzer import g1_short_horizon_champion_runtime as champion
from seiltanzer.g1_champion_progress_scalability import _refresh_progress_bounded


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()


def _progress_runtime() -> _Runtime:
    runtime = _Runtime()
    runtime._conn.executescript("""
        CREATE TABLE g1s_observations(
            observation_id TEXT PRIMARY KEY,
            captured_ts REAL NOT NULL,
            target_ts REAL NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            market_regime TEXT,
            oos_eligible INTEGER NOT NULL
        );
        CREATE TABLE g1s_resolutions(
            observation_id TEXT PRIMARY KEY,
            direction_label TEXT NOT NULL,
            terminal_log_return REAL,
            resolved_ts REAL NOT NULL
        );
    """)
    champion._ensure_tables(runtime)
    runtime._conn.execute("""
        INSERT INTO g1s_validation_cohorts(
            validation_cohort_id,target,horizon_minutes,feature_set,model_family,
            champion_model_id,frozen_at,training_cutoff_ts,oos_start_ts,source,status,
            auto_promotion,production_authority,created_ts
        ) VALUES('c1',?,15,'base','logistic','m1',1,0,1,'test','LIVE_VALIDATING',0,0,1)
    """, (champion.DIRECTION_TARGET,))
    base = 1_700_000_000.0
    rows = [
        ("o1", base, base + 900, 15, "XAU", "LOW", 1, "UP", 0.01, base + 901),
        ("o2", base + 100, base + 1000, 15, "XAU", "LOW", 1, "DOWN", -0.01, base + 1001),
        ("o3", base + 1000, base + 1900, 15, "XAU", "HIGH", 1, "UP", 0.02, base + 1901),
        # Linked but excluded from the direction evidence because it resolved FLAT.
        ("o4", base + 2000, base + 2900, 15, "XAG", "HIGH", 1, "FLAT", 0.0, base + 2901),
    ]
    for index, row in enumerate(rows):
        obs_id, captured, target_ts, horizon, instrument, regime, eligible, label, ret, resolved = row
        runtime._conn.execute(
            "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?)",
            (obs_id, captured, target_ts, horizon, instrument, regime, eligible),
        )
        runtime._conn.execute(
            "INSERT INTO g1s_resolutions VALUES(?,?,?,?)",
            (obs_id, label, ret, resolved),
        )
        runtime._conn.execute("""
            INSERT INTO g1s_champion_prediction_links(
                link_id,validation_cohort_id,target,prediction_id,observation_id,model_id,
                captured_ts,target_ts,prediction_created_ts,contract_version,created_ts
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            f"l{index}", "c1", champion.DIRECTION_TARGET, f"p{index}", obs_id, "m1",
            captured, target_ts, captured - 1, champion.CHAMPION_LINK_VERSION, captured,
        ))
    runtime._conn.commit()
    return runtime


def test_champion_progress_uses_sql_aggregates_with_dependency_key_parity():
    runtime = _progress_runtime()
    try:
        _refresh_progress_bounded(runtime)
        row = runtime._conn.execute(
            "SELECT * FROM g1s_champion_progress WHERE validation_cohort_id='c1'"
        ).fetchone()
        assert row["oos_raw_n"] == 3
        # o1/o2 share the same XAU 15m dependency bucket; o3 is the next bucket.
        assert row["oos_effective_n"] == 2
        assert row["positive_n"] == 2
        assert row["negative_n"] == 1
        assert row["temporal_blocks"] == 1
        assert row["regimes"] == 2
        # Link count deliberately includes the FLAT row, matching the old contract.
        assert row["linked_prediction_n"] == 4
        assert row["latest_resolved_ts"] == 1_700_001_901.0
    finally:
        runtime._conn.close()


def _report_snapshot() -> dict:
    return {
        "trade_geometry": {
            "current": 100.0,
            "entry": 100.0,
            "original_stop": 99.0,
            "take_first": None,
            "stop_or_be_first": None,
            "no_touch": None,
        },
        "policy_manager": {
            "management_decision": {"policy": "HOLD"},
            "recommendation": {"policy": "HOLD"},
            "selection_rule": {
                "indifference_band_r": 0.03,
                "eligible": ["HOLD"],
                "cvar_floor_r": -1.01,
            },
            "policies": {
                "HOLD": {"expected_final_r": -1.01, "cvar10_r": -1.01},
                "CLOSE_10": {"expected_final_r": -1.174, "cvar10_r": -1.174},
                "CLOSE_25": {"expected_final_r": -1.421, "cvar10_r": -1.421},
                "CLOSE_50": {"expected_final_r": -1.831, "cvar10_r": -1.831},
                "EXIT": {"expected_final_r": -2.652, "cvar10_r": -2.652},
            },
            "scenario_geometry": {},
            "input_audit": {},
            "option_derivative_state": {
                "metrics": {
                    "p_take": {"value": 0.0, "confidence": 0.157},
                    "p_stop": {"value": 1.0, "confidence": 0.157},
                    "gex_force": {"value": 1.0, "confidence": 0.023},
                    "iv": {"value": 0.25, "confidence": 0.40},
                }
            },
        },
    }


def test_quality_separates_family_coverage_from_numerical_availability(monkeypatch):
    monkeypatch.setattr(
        report_v20,
        "_BASE_QUALITY_LINES",
        lambda _snapshot: [
            "Покрытие decision metrics: 12/12 (100.0%). Input audit: COMPACTED.",
            "Надёжность расчёта: низкая.",
        ],
        raising=False,
    )
    lines = report_v20._quality_lines(_report_snapshot())
    joined = "\n".join(lines)
    assert "Контрактное покрытие семейств decision metrics: 12/12" in joined
    assert "Операционная численная доступность: PARTIAL" in joined
    assert "НЕ означает" in joined
    assert "execution_mc=UNAVAILABLE" in joined


def test_economic_section_recovers_indifference_and_exit_delta_from_policy_table():
    body = report_v20._economic_body(_report_snapshot())
    assert body is not None
    text = "\n".join(body)
    assert "Зона безразличия Expected: +0.030R" in text
    assert "Другой NET-CVaR-eligible политики кроме HOLD сейчас нет" in text
    assert "Полный EXIT: Expected против HOLD -1.642R" in text


def test_metric_audit_marks_boundary_values_without_changing_numbers(monkeypatch):
    monkeypatch.setattr(
        report_v20,
        "_BASE_METRIC_AUDIT_LINES",
        lambda _snapshot: [
            "Текущие значения и производные разделены.",
            "p_take: current=0 probability; confidence=15.7%.",
            "p_stop: current=1 probability; confidence=15.7%.",
            "gex_force: current=1 normalized_score; confidence=2.3%.",
            "iv: current=0.25 annualized_volatility; confidence=40.0%.",
        ],
        raising=False,
    )
    lines = report_v20._metric_audit_lines(_report_snapshot())
    joined = "\n".join(lines)
    assert "boundary_values=3" in joined
    assert "p_take: current=0 probability" in joined
    assert "p_stop: current=1 probability" in joined
    assert "BOUNDARY_VALUE" in joined
    assert "Boundary value не означает 100% уверенности" in joined


def test_combined_provider_uses_one_call_and_returns_non_authoritative_shadow(monkeypatch):
    snapshot = _report_snapshot()
    snapshot["captured_ts"] = 123.0
    snapshot["trade_id"] = 7
    calls = {"n": 0}

    provider_content = json.dumps({
        "explanation_ru": (
            "Расчёт ограничен качеством источников: часть вероятностей недоступна, "
            "поэтому сервер сохраняет рассчитанную стратегическую политику. "
            "Следующий пересчёт нужен при новом рыночном снимке или обновлении цепочки."
        ),
        "shadow_decision": {
            "policy": "HOLD",
            "confidence": 0.61,
            "reason_ru": "HOLD единственная политика внутри опубликованного CVaR feasible set.",
            "key_evidence": ["HOLD CVaR10=-1.01R"],
            "counter_evidence": ["execution-MC unavailable"],
        },
    }, ensure_ascii=False)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": provider_content}}],
                "model": "test-model",
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            calls["n"] += 1
            return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(report_v20.httpx, "Client", Client)
    monkeypatch.setattr(report_v20.ai_verdict, "render_policy_report", lambda _snapshot: "DETERMINISTIC")
    monkeypatch.setattr(report_v20.ai_verdict, "_validate_model_report", lambda _text, _snapshot: [])
    monkeypatch.setattr(report_v20._provider, "_explanation_facts", lambda _snapshot: {})

    result = report_v20.request_explanation_with_shadow(
        snapshot, authoritative_snapshot=snapshot,
    )
    assert calls["n"] == 1
    assert result["provider_mode"] == "llm_explanation_plus_decision_shadow"
    assert result["llm_shadow_decision"]["policy"] == "HOLD"
    assert result["llm_shadow_decision"]["production_authority"] is False
    assert result["llm_shadow_decision"]["automatic_execution_allowed"] is False
    assert "LLM SHADOW DECISION" in result["verdict"]
