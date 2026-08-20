from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "production_ede_inventory.py"
_SPEC = importlib.util.spec_from_file_location("production_ede_inventory", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
inventory = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inventory)


def _row(feature_id: str, *, real: int = 0, usable: bool = False,
         diagnosis: dict | None = None) -> dict:
    return {
        "feature_id": feature_id,
        "real_observations": real,
        "usable_for_ede": usable,
        "zero_coverage_diagnosis": diagnosis,
    }


def test_coverage_state_separates_missing_history_from_real_insufficient_n() -> None:
    definitions = {item.feature_id: item for item in inventory.FEATURES}

    assert inventory._coverage_state(
        definitions["option.iv"], _row("option.iv")) == "HISTORICAL_DATA_MISSING"
    assert inventory._coverage_state(
        definitions["price.ret_5m"],
        _row("price.ret_5m", real=25),
    ) == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    assert inventory._coverage_state(
        definitions["price.ret_5m"],
        _row("price.ret_5m", real=1000, usable=True),
    ) == "DATA_READY"


def test_coverage_state_preserves_scope_and_known_causal_backfill_diagnosis() -> None:
    definitions = {item.feature_id: item for item in inventory.FEATURES}

    assert inventory._coverage_state(
        definitions["option.rnd_geometry"],
        _row("option.rnd_geometry"),
    ) == "G1M_ONLY"
    assert inventory._coverage_state(
        definitions["quality.availability"],
        _row("quality.availability"),
    ) == "QUALITY_ONLY"
    assert inventory._coverage_state(
        definitions["price.trend_efficiency_60"],
        _row(
            "price.trend_efficiency_60",
            diagnosis={"causal_backfill": True},
        ),
    ) == "CAUSAL_BACKFILL_NO_COVERAGE"


def test_enriched_inventory_exposes_registry_source_and_actionable_family_counts() -> None:
    rows = [
        _row("option.iv"),
        _row("option.skew", real=20),
        _row("price.ret_5m", real=1000, usable=True),
    ]

    enriched = inventory._enrich_features(rows)
    by_id = {row["feature_id"]: row for row in enriched}
    assert by_id["option.iv"]["family"] == "OPTIONS"
    assert by_id["option.iv"]["producer"]
    assert by_id["option.iv"]["history_strategy"] == "PROSPECTIVE_ONLY"
    assert by_id["option.iv"]["coverage_state"] == "HISTORICAL_DATA_MISSING"
    assert by_id["option.skew"]["coverage_state"] == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    assert by_id["price.ret_5m"]["coverage_state"] == "DATA_READY"

    families = {row["family"]: row for row in inventory._family_summary(enriched)}
    assert families["OPTIONS"]["feature_count"] == 2
    assert families["OPTIONS"]["with_real_observations"] == 1
    assert families["OPTIONS"]["coverage_state_counts"] == {
        "HISTORICAL_DATA_MISSING": 1,
        "INSUFFICIENT_INDEPENDENT_EVIDENCE": 1,
    }
    assert families["PRICE"]["usable_for_ede"] == 1
    assert families["PRICE"]["production_authority"] is False
