from __future__ import annotations

import json

from seiltanzer.g1_short_horizon_baseline_refinement import (
    BASELINE_CONTRACT_VERSION, MOMENTUM_DOWN_P, MOMENTUM_UP_P,
    _momentum_probability,
)


def _row(ret):
    return {"frozen_features_json": json.dumps({
        "price_state": {"g1s_intraday": {"available": True, "ret_15m": ret}}
    })}


def test_momentum_baseline_is_fixed_and_untuned():
    assert BASELINE_CONTRACT_VERSION == "g1s-fixed-momentum-baseline-v1"
    assert _momentum_probability(_row(0.01)) == MOMENTUM_UP_P == 0.55
    assert _momentum_probability(_row(-0.01)) == MOMENTUM_DOWN_P == 0.45
    assert _momentum_probability(_row(0.0)) == 0.5
    assert _momentum_probability({"frozen_features_json": "{}"}) == 0.5
