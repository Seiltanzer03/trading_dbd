from __future__ import annotations

import pytest

from seiltanzer.edge_discovery.shadow import ShadowLedger


def test_shadow_prediction_must_exist_before_target(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    with pytest.raises(ValueError):
        ledger.append_prediction({
            "shadow_prediction_id": "late",
            "prediction_created_ts": 200.0,
            "target_ts": 200.0,
        })
