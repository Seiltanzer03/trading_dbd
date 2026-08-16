from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

from seiltanzer import active_edge_ai_integration as active


def _engine(tmp_path):
    return SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))


def test_structured_active_edge_is_related_to_current_long(monkeypatch, tmp_path):
    research = tmp_path / "research"
    research.mkdir()
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
    path = research / "active_structured_30m_latest.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    now = time.time()
    os.utime(path, (now, now))
    monkeypatch.setattr(active, "_current_values", lambda *_: {})
    monkeypatch.setattr(active, "_conditions_match", lambda *_: True)

    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": now + 1.0, "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
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


def test_stale_active_edge_report_is_not_used(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    path = research / "active_ml_latest.json"
    path.write_text(json.dumps({
        "edge_policy": active.POLICY_VERSION,
        "production_authority": False,
        "candidates": [{
            "candidate_id": "ml-1",
            "status": "ML_DISCOVERY_SIGNAL",
            "target_id": "DIRECTION",
            "horizon_minutes": 15,
            "primary_improvement": 0.02,
            "q_value": 0.9,
            "fold_positive": 2,
        }],
    }), encoding="utf-8")
    old = time.time() - active.MAX_REPORT_AGE_SEC - 10
    os.utime(path, (old, old))
    context = active.build_active_edge_context(
        _engine(tmp_path),
        {"captured_ts": time.time(), "strategy": {"direction": "long", "instrument": "NAS100"}},
    )
    assert context["available"] is False
    assert context["signals"] == []
    assert context["matched_groups"] == []


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
    path = research / "active_structured_60m_latest.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    now = time.time()
    os.utime(path, (now, now))
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
