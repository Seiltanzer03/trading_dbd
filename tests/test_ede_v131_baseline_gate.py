from __future__ import annotations

import json

from seiltanzer.edge_discovery.baseline_rows import baseline_eligible_rows
from seiltanzer.edge_discovery.shadow_runtime import _load_latest_audit


def _row(ret5, ret15, direction="UP", observation_id="o"):
    return {
        "observation_id": observation_id,
        "direction_label": direction,
        "features": {"ret_5m": ret5, "ret_15m": ret15},
    }


def test_baseline_gate_treats_missing_as_missing_not_zero():
    rows = [
        _row(0.0, 0.0, observation_id="zero-is-valid"),
        _row(None, 0.1, observation_id="missing-ret5"),
        _row(0.1, None, observation_id="missing-ret15"),
        _row(float("nan"), 0.1, observation_id="nan-ret5"),
        _row(0.1, 0.2, direction=None, observation_id="missing-label"),
    ]

    eligible, report = baseline_eligible_rows(rows)

    assert [row["observation_id"] for row in eligible] == ["zero-is-valid"]
    assert report["input_rows"] == 5
    assert report["eligible_rows"] == 1
    assert report["excluded_rows"] == 4
    assert report["missing_by_feature"] == {"ret_5m": 2, "ret_15m": 1}
    assert report["invalid_direction_rows"] == 1
    assert report["missing_is_zero"] is False
    assert report["filter_before_temporal_folds"] is True


def test_shadow_runtime_accepts_v131_audit_contract(tmp_path):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps({
        "contract_version": "g1s-ede-production-audit-v1.3.1",
        "selective_search": {},
        "frozen_evidence": {},
    }), encoding="utf-8")

    assert _load_latest_audit(path) is not None
