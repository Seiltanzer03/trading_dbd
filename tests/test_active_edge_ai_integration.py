from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

from seiltanzer import active_edge_ai_integration as active


TEST_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _engine(tmp_path):
    return SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))


def _published(report: dict, *, sha: str = TEST_SHA) -> dict:
    return {
        **report,
        "publication_contract_version": active.PUBLICATION_CONTRACT_VERSION,
        "published_for_sha": sha,
        "publication_run_id": "123-test",
    }


def _write_complete_set(research, now: float, *, structured_override=None, ml_override=None):
    structured_override = structured_override or {}
    for horizon in active.EXPECTED_STRUCTURED_HORIZONS:
        payload = structured_override.get(horizon, {
            "edge_policy": active.POLICY_VERSION,
            "production_authority": False,
            "horizons": [],
        })
        path = research / f"active_structured_{horizon}m_latest.json"
        path.write_text(json.dumps(_published(payload)), encoding="utf-8")
        os.utime(path, (now, now))
    ml = ml_override or {
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "candidates": [],
    }
    path = research / "active_ml_latest.json"
    path.write_text(json.dumps(_published(ml)), encoding="utf-8")
    os.utime(path, (now, now))


def _exact_runtime(monkeypatch):
    monkeypatch.setattr(active, "runtime_git_sha", lambda: TEST_SHA)


def test_structured_active_edge_is_related_to_current_long(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    report = {
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "horizons": [{
            "targets": [{
                "candidates": [{
                    "candidate_id": "risk-1",
                    "status": "DISCOVERY_SIGNAL",
                    "target_id": "RETURN_SIGMA",
                    "horizon_minutes": 30,
                    "primary_improvement": 0.027,
                    "q_value": 0.74,
                    "fold_positive": 2,
                    "strict_reference_qualified": False,
                    "conditions": [{"feature_id": "price.ret_5m"}],
                    "prediction_shift": {
                        "kind": "SCALAR_TARGET_SHIFT",
                        "candidate_minus_structural_baseline": -0.31,
                        "interpretation": "MORE_DOWNSIDE_RETURN",
                    },
                }]
            }]
        }],
    }
    _write_complete_set(research, now, structured_override={30: report})
    _exact_runtime(monkeypatch)
    monkeypatch.setattr(active, "_current_values", lambda *_: {})
    monkeypatch.setattr(active, "_conditions_match", lambda *_: True)

    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1.0, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["measurement_available"] is True
    assert context["report_state"] == "CURRENT_SHA_REPORTS_COMPLETE"
    assert context["source_report_n"] == active.EXPECTED_REPORT_COUNT
    assert context["available"] is True
    assert context["matched_structured_signal_n"] == 1
    assert context["supporting_position_n"] == 0
    assert context["opposing_position_n"] == 1
    assert context["net_position_vote"] == -1
    assert context["net_position_vote_ratio"] == -1.0
    assert context["matched_group_n"] == 1
    assert context["matched_groups"][0]["target_family"] == "RETURN"
    assert context["matched_groups"][0]["net_vote"] == -1
    signal = context["signals"][0]
    assert signal["market_bias"] == "BEARISH"
    assert signal["position_relation"] == "OPPOSES_POSITION"
    assert signal["strict_reference_qualified"] is False
    assert context["automatic_execution"] is False


def test_legacy_structured_ids_match_live_canonical_values():
    values = {
        "vol.rv15_over_rv60": {
            "feature_id": "vol.rv15_over_rv60", "value": 1.2, "available": True,
        },
        "cross.family_breadth": {
            "feature_id": "cross.family_breadth", "value": 0.72, "available": True,
        },
        "regime.asset": {
            "feature_id": "regime.asset", "value": "NAS100", "available": True,
        },
    }
    candidate = {
        "conditions": [
            {
                "feature_id": "rv15_over_rv60", "kind": "train_relative",
                "state": "ABOVE_MEDIAN", "lower": 0.9, "upper": 0.9,
                "train_cutoff_ts": 1.0,
            },
            {
                "feature_id": "family_breadth_state", "kind": "categorical",
                "state": "POSITIVE", "lower": None, "upper": None,
                "train_cutoff_ts": 1.0,
            },
            {
                "feature_id": "asset", "kind": "categorical",
                "state": "NAS100", "lower": None, "upper": None,
                "train_cutoff_ts": 1.0,
            },
        ]
    }
    assert active._conditions_match(values, candidate) is True


def test_stale_active_edge_report_set_is_not_used(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    _write_complete_set(research, now)
    old = now - active.MAX_REPORT_AGE_SEC - 10
    for path in research.iterdir():
        os.utime(path, (old, old))
    _exact_runtime(monkeypatch)

    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["measurement_available"] is False
    assert context["report_state"] == "CURRENT_SHA_REPORTS_MISSING"
    assert context["source_report_n"] == 0
    assert context["available"] is False
    assert context["signals"] == []
    assert context["matched_groups"] == []


def test_partial_current_sha_report_set_fails_closed(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    path = research / "active_structured_15m_latest.json"
    path.write_text(json.dumps(_published({
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "horizons": [{
            "targets": [{
                "candidates": [{
                    "candidate_id": "must-not-vote-yet",
                    "status": "DISCOVERY_SIGNAL",
                    "target_id": "DIRECTION",
                    "horizon_minutes": 15,
                    "conditions": [{"feature_id": "price.ret_5m"}],
                    "prediction_shift": {"interpretation": "MORE_UP"},
                }],
            }],
        }],
    })), encoding="utf-8")
    os.utime(path, (now, now))
    _exact_runtime(monkeypatch)
    monkeypatch.setattr(active, "_conditions_match", lambda *_: True)

    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["report_state"] == "CURRENT_SHA_REPORTS_PARTIAL"
    assert context["measurement_available"] is False
    assert context["source_report_n"] == 1
    assert context["total_active_signal_n"] == 0
    assert context["matched_structured_signal_n"] == 0


def test_fresh_previous_sha_reports_are_not_used(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    for horizon in active.EXPECTED_STRUCTURED_HORIZONS:
        path = research / f"active_structured_{horizon}m_latest.json"
        path.write_text(json.dumps(_published({
            "edge_policy": active.POLICY_VERSION,
            "production_authority": False,
            "horizons": [],
        }, sha=OTHER_SHA)), encoding="utf-8")
        os.utime(path, (now, now))
    path = research / "active_ml_latest.json"
    path.write_text(json.dumps(_published({
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "candidates": [],
    }, sha=OTHER_SHA)), encoding="utf-8")
    os.utime(path, (now, now))
    _exact_runtime(monkeypatch)

    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["report_state"] == "CURRENT_SHA_REPORTS_MISSING"
    assert context["measurement_available"] is False
    assert context["source_report_n"] == 0


def test_unstamped_active_edge_reports_fail_closed(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    path = research / "active_ml_latest.json"
    path.write_text(json.dumps({
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "candidates": [],
    }), encoding="utf-8")
    os.utime(path, (now, now))
    _exact_runtime(monkeypatch)
    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["report_state"] == "CURRENT_SHA_REPORTS_MISSING"
    assert context["measurement_available"] is False


def test_unknown_runtime_sha_fails_closed(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    _write_complete_set(research, now)
    monkeypatch.setattr(active, "runtime_git_sha", lambda: None)
    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["report_state"] == "RUNTIME_SHA_UNAVAILABLE"
    assert context["measurement_available"] is False
    assert context["runtime_sha"] is None


def test_complete_reports_with_zero_signals_are_honest_zero(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    now = time.time()
    _write_complete_set(research, now)
    _exact_runtime(monkeypatch)
    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["measurement_available"] is True
    assert context["report_state"] == "CURRENT_SHA_REPORTS_COMPLETE"
    assert context["available"] is False
    assert context["total_active_signal_n"] == 0
    assert context["matched_structured_signal_n"] == 0


def test_all_matching_candidates_are_aggregated_beyond_top_eight(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    candidates = []
    for index in range(12):
        candidates.append({
            "candidate_id": f"risk-{index}",
            "status": "DISCOVERY_SIGNAL",
            "target_id": "RETURN_SIGMA",
            "horizon_minutes": 60,
            "primary_improvement": 0.01 + index / 1000.0,
            "q_value": 0.95,
            "fold_positive": 2,
            "strict_reference_qualified": index in {0, 1, 2},
            "conditions": [{"feature_id": "price.ret_5m"}],
            "prediction_shift": {
                "kind": "SCALAR_TARGET_SHIFT",
                "candidate_minus_structural_baseline": -0.1,
                "interpretation": "MORE_DOWNSIDE_RETURN",
            },
        })
    report = {
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "horizons": [{"targets": [{"candidates": candidates}]}],
    }
    now = time.time()
    _write_complete_set(research, now, structured_override={60: report})
    _exact_runtime(monkeypatch)
    monkeypatch.setattr(active, "_current_values", lambda *_: {})
    monkeypatch.setattr(active, "_conditions_match", lambda *_: True)

    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1.0, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )

    assert context["aggregate_scope"] == "ALL_ACTIVE_CANDIDATES_WITH_ALL_MATCHED_STRUCTURED_VOTES"
    assert context["total_active_signal_n"] == 12
    assert context["structured_signal_n"] == 12
    assert context["ml_signal_n"] == 0
    assert context["matched_structured_signal_n"] == 12
    assert context["supporting_position_n"] == 0
    assert context["opposing_position_n"] == 12
    assert context["net_position_vote"] == -12
    assert context["net_position_vote_ratio"] == -1.0
    assert context["strict_reference_signal_n"] == 3
    assert context["matched_strict_reference_signal_n"] == 3
    assert context["strict_supporting_position_n"] == 0
    assert context["strict_opposing_position_n"] == 3
    assert context["strict_net_position_vote"] == -3
    assert context["strict_net_position_vote_ratio"] == -1.0
    assert context["matched_group_n"] == 1
    group = context["matched_groups"][0]
    assert group["target_id"] == "RETURN_SIGMA"
    assert group["target_family"] == "RETURN"
    assert group["signal_horizon_minutes"] == 60
    assert group["matched_n"] == 12
    assert group["opposing_n"] == 12
    assert group["strict_opposing_n"] == 3
    assert context["serialized_signal_n"] == active.MAX_SIGNALS
    assert context["details_truncated"] is True
    assert len(context["signals"]) == active.MAX_SIGNALS
