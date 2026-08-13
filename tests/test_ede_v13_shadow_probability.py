from __future__ import annotations

from seiltanzer.edge_discovery.shadow import _probability_fit


def _candidate():
    return {
        "horizon_minutes": 15,
        "deployment_refit": {"deployment_rule": [{
            "feature_id": "price.ret_5m", "kind": "train_relative",
            "state": "ABOVE_MEDIAN", "lower": 0.0, "upper": 0.0,
            "train_cutoff_ts": 50.0,
        }]},
    }


def _row(i: int, resolved_ts: float, ret5: float, up: bool):
    return {
        "observation_id": f"o{i}", "instrument": "NAS100",
        "captured_ts": float(i), "target_ts": float(i)+900.0,
        "resolved_ts": resolved_ts, "horizon_minutes": 15,
        "outcome_available": True,
        "direction_label": "UP" if up else "DOWN",
        "features": {"ret_5m": ret5, "ret_15m": ret5},
        "ede_features": {"price.ret_5m": ret5},
    }


def test_probability_fit_uses_only_outcomes_known_by_cutoff():
    rows = [
        _row(i, 100.0+i, .02 if i % 3 else -.02, bool(i % 2))
        for i in range(1, 121)
    ]
    rows.append(_row(999, 10_000.0, .02, True))
    fit = _probability_fit(_candidate(), rows, cutoff_ts=1000.0)
    assert fit is not None
    assert fit["global_train_raw"] == 120
    assert fit["conditional_train_raw"] >= 20
    assert fit["fit_cutoff_ts"] == 1000.0
