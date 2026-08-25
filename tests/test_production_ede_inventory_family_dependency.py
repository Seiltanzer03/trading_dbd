from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "production_ede_inventory.py"
_SPEC = importlib.util.spec_from_file_location("production_ede_inventory_dependency", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
inventory = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inventory)


def test_family_horizon_preserves_release_dependency_semantics() -> None:
    rows = [
        {
            "coverage_state_by_horizon": {"15": "INSUFFICIENT_INDEPENDENT_EVIDENCE"},
            "by_horizon": {
                "15": {
                    "raw": 4503,
                    "effective": 3,
                    "resolved": 2330,
                    "temporal_blocks": 3,
                    "coverage_pct": 100.0,
                    "data_maturity": "INSUFFICIENT_DATA",
                    "independent_release_n": 3,
                    "dependency_unit": "OFFICIAL_MACRO_RELEASE_ID",
                    "repeated_t0_increases_effective_n": False,
                }
            },
        },
        {
            "coverage_state_by_horizon": {"15": "INSUFFICIENT_INDEPENDENT_EVIDENCE"},
            "by_horizon": {
                "15": {
                    "raw": 4503,
                    "effective": 2,
                    "resolved": 2330,
                    "temporal_blocks": 2,
                    "coverage_pct": 100.0,
                    "data_maturity": "INSUFFICIENT_DATA",
                    "independent_release_n": 2,
                    "dependency_unit": "OFFICIAL_MACRO_RELEASE_ID",
                    "repeated_t0_increases_effective_n": False,
                }
            },
        },
    ]

    summary = inventory._family_horizon_summary(rows, 15)

    # Additive feature totals are useful for inventory sizing but must never be
    # misread as five independent releases.  The canonical macro dependence
    # metadata remains explicit at family×horizon level.
    assert summary["effective_feature_observations"] == 5
    assert summary["aggregate_counts_are_feature_observation_totals"] is True
    assert summary["dependency_unit_counts"] == {"OFFICIAL_MACRO_RELEASE_ID": 2}
    assert summary["release_dependency_feature_count"] == 2
    assert summary["independent_release_n_min"] == 2
    assert summary["independent_release_n_max"] == 3
    assert summary["repeated_t0_increases_effective_n"] is False
    assert summary["data_ready_features"] == 0
    assert summary["production_authority"] is False


def test_non_release_family_does_not_invent_release_independence() -> None:
    rows = [{
        "coverage_state_by_horizon": {"15": "DATA_READY"},
        "by_horizon": {
            "15": {
                "raw": 1000,
                "effective": 500,
                "resolved": 900,
                "temporal_blocks": 4,
                "coverage_pct": 90.0,
                "data_maturity": "DATA_READY_ROBUST",
            }
        },
    }]

    summary = inventory._family_horizon_summary(rows, 15)
    assert summary["dependency_unit_counts"] == {}
    assert summary["release_dependency_feature_count"] == 0
    assert summary["independent_release_n_min"] is None
    assert summary["independent_release_n_max"] is None
    assert summary["repeated_t0_increases_effective_n"] is None
