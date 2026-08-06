import math

from seiltanzer.lattice_visual_history import (
    LatticeVisualHistoryTracker,
    rebin_visual_distribution,
)


class MemoryCache:
    def __init__(self):
        self.data = {}

    def get(self, key, max_age=None):
        value = self.data.get(key)
        return (value, 1.0) if value is not None else None

    def put(self, key, value, ts=None):
        self.data[key] = value


def payload(ts, probs):
    return {
        "ts": ts,
        "trade": {
            "id": 9,
            "direction": "long",
            "entry": 100.0,
            "stop": 90.0,
            "take": 120.0,
        },
        "prob": {"T": 2.0, "r": 0.1},
        "market": {
            "scenario_probs": probs,
            "scenario_edges": [-4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0],
        },
    }


def test_visual_rebin_keeps_outer_tails_separate():
    out = rebin_visual_distribution(
        [0.08, 0.12, 0.20, 0.28, 0.20, 0.12],
        [-4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0],
        [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0],
    )
    assert out is not None
    assert out["left_tail"] == 0.08
    assert out["right_tail"] == 0.06
    assert math.isclose(
        out["left_tail"] + out["visible_mass"] + out["right_tail"],
        1.0,
        abs_tol=1e-7,
    )
    assert math.isclose(sum(out["probs"]), 1.0, abs_tol=1e-7)


def test_tracker_returns_entry_average_current_on_one_grid():
    tracker = LatticeVisualHistoryTracker(MemoryCache())
    first = tracker.update(payload(100.0, [0.08, 0.12, 0.20, 0.28, 0.20, 0.12]))
    duplicate = tracker.update(payload(100.4, [0.04, 0.08, 0.16, 0.28, 0.26, 0.18]))
    second = tracker.update(payload(101.2, [0.02, 0.06, 0.12, 0.25, 0.30, 0.25]))

    assert first["sample_count"] == 1
    assert duplicate["sample_count"] == 1
    assert second["sample_count"] == 2
    assert second["entry"]["edges"] == second["average"]["edges"] == second["current"]["edges"]
    assert second["entry"]["probs"] != second["current"]["probs"]
    for layer in ("entry", "average", "current"):
        assert math.isclose(sum(second[layer]["probs"]), 1.0, abs_tol=1e-6)
    expected = [
        (a + b) / 2.0
        for a, b in zip(second["entry"]["probs"], second["current"]["probs"])
    ]
    total = sum(expected)
    expected = [value / total for value in expected]
    assert all(
        math.isclose(actual, target, abs_tol=2e-6)
        for actual, target in zip(second["average"]["probs"], expected)
    )
    assert any(abs(value) > 1e-5 for value in second["delta_probs_from_entry"])
