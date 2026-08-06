import math

from seiltanzer.lattice_revaluation import (
    LatticeRevaluationTracker,
    _source_quality,
    distribution_buckets,
)


class MemoryCache:
    def __init__(self):
        self.data = {}

    def get(self, key, max_age=None):
        row = self.data.get(key)
        return (row, 1.0) if row is not None else None

    def put(self, key, value, ts=None):
        self.data[key] = value


def _payload(ts, p_take, ev, q50, *, proxy_status="live"):
    probs = [0.10, 0.20, 0.35, 0.25, 0.10]
    return {
        "ts": ts,
        "trade": {
            "id": 42, "direction": "long",
            "entry": 100.0, "stop": 90.0, "take": 120.0,
        },
        "prob": {"T": 2.0, "r": 0.1},
        "market": {
            "available": True,
            "scenario_probs": probs,
            "scenario_edges": [-3.0, -1.0, 0.0, 1.0, 2.0, 4.0],
            "p_take_horizon": p_take,
            "p_stop_horizon": 0.25,
            "p_unresolved_horizon": 1.0 - p_take - 0.25,
            "horizon_barrier_ev": ev,
            "scenario_p10_r": -1.4,
            "scenario_median_r": q50,
            "scenario_p90_r": 2.2,
        },
        "feeds": {
            "proxy_price": {"status": proxy_status},
            "chain": {"status": "delayed", "age_sec": 120},
        },
        "options_summary": {"experimental": False},
    }


def test_distribution_buckets_are_exhaustive_and_do_not_fold_tails():
    out = distribution_buckets(
        [0.10, 0.20, 0.35, 0.25, 0.10],
        [-3.0, -1.0, 0.0, 1.0, 2.0, 4.0],
        2.0,
    )
    assert out is not None
    assert math.isclose(sum(out.values()), 1.0, abs_tol=1e-9)
    assert out == {
        "stop_tail": 0.1,
        "red_zone": 0.2,
        "green_zone": 0.6,
        "take_tail": 0.1,
    }


def test_tracker_keeps_entry_average_current_and_ignores_duplicate_calls():
    tracker = LatticeRevaluationTracker(MemoryCache())
    first = tracker.update(_payload(100.0, 0.40, -0.10, -0.20))
    duplicate = tracker.update(_payload(100.4, 0.55, 0.15, 0.10))
    second = tracker.update(_payload(101.2, 0.60, 0.30, 0.30))

    assert first["sample_count"] == 1
    assert duplicate["sample_count"] == 1
    assert second["sample_count"] == 2
    assert second["entry"]["p_take"] == 0.40
    assert second["current"]["p_take"] == 0.60
    assert second["average"]["p_take"] == 0.50
    assert second["change_from_entry"]["p_take"] == 0.20
    assert second["change_from_entry"]["barrier_ev_r"] == 0.40
    assert second["score"]["raw"] > 0


def test_indicative_mapping_is_discounted_but_not_disabled():
    live = _source_quality(_payload(100, 0.4, 0.0, 0.0, proxy_status="live"))
    indicative = _source_quality(
        _payload(100, 0.4, 0.0, 0.0, proxy_status="delayed")
    )
    assert live["mode"] == "live_mapping"
    assert indicative["mode"] == "indicative_mapping"
    assert 0.3 < indicative["weight"] < live["weight"] < 1.0
    assert indicative["context_only"] is False
