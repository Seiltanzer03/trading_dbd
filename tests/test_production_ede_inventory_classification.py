from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "production_ede_inventory.py"
_SPEC = importlib.util.spec_from_file_location("production_ede_inventory", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
inventory = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inventory)


def _bucket(*, raw: int = 0, effective: int = 0, resolved: int = 0,
            temporal_blocks: int = 0, coverage_pct: float = 0.0,
            maturity: str = "INSUFFICIENT_DATA") -> dict:
    return {
        "raw": raw,
        "effective": effective,
        "resolved": resolved,
        "temporal_blocks": temporal_blocks,
        "coverage_pct": coverage_pct,
        "data_maturity": maturity,
        "edge_maturity": "INSUFFICIENT_DATA",
    }


def _row(feature_id: str, *, real: int = 0, usable: bool = False,
         maturity: str = "INSUFFICIENT_DATA",
         diagnosis: dict | None = None,
         by_horizon: dict[str, dict] | None = None) -> dict:
    return {
        "feature_id": feature_id,
        "real_observations": real,
        "usable_for_ede": usable,
        "data_maturity": maturity,
        "zero_coverage_diagnosis": diagnosis,
        "by_horizon": by_horizon or {
            str(horizon): _bucket() for horizon in inventory.HORIZONS
        },
    }


def test_feature_horizon_contract_accepts_complete_buckets() -> None:
    inventory._validate_feature_horizons([_row("price.ret_5m")])


def test_feature_horizon_contract_rejects_missing_or_malformed_buckets() -> None:
    missing_horizon = _row("price.ret_5m")
    missing_horizon["by_horizon"].pop("240")
    with pytest.raises(AssertionError, match="price.ret_5m horizons mismatch"):
        inventory._validate_feature_horizons([missing_horizon])

    non_mapping = _row("price.ret_5m")
    non_mapping["by_horizon"]["60"] = []
    with pytest.raises(AssertionError, match="horizon 60 must be a mapping"):
        inventory._validate_feature_horizons([non_mapping])

    missing_field = _row("price.ret_5m")
    missing_field["by_horizon"]["120"].pop("data_maturity")
    with pytest.raises(AssertionError, match="horizon 120 missing fields"):
        inventory._validate_feature_horizons([missing_field])


@pytest.mark.parametrize(("field", "value"), (
    ("raw", None),
    ("raw", True),
    ("effective", -1),
    ("resolved", 1.5),
    ("temporal_blocks", -1),
    ("coverage_pct", float("nan")),
    ("coverage_pct", 100.1),
    ("data_maturity", "UNKNOWN_DATA_TIER"),
    ("edge_maturity", "ROBUST_EDGE"),
))
def test_feature_horizon_contract_rejects_invalid_values(
    field: str, value: object,
) -> None:
    row = _row("price.ret_5m")
    row["by_horizon"]["15"].update({
        "raw": 3,
        "effective": 2,
        "resolved": 2,
        "temporal_blocks": 1,
        "coverage_pct": 50.0,
        "data_maturity": "DATA_READY_EARLY",
        "edge_maturity": "INSUFFICIENT_DATA",
        field: value,
    })
    with pytest.raises(AssertionError, match="horizon 15"):
        inventory._validate_feature_horizons([row])


@pytest.mark.parametrize("counts", (
    {"raw": 1, "resolved": 2, "effective": 1, "temporal_blocks": 1},
    {"raw": 2, "resolved": 1, "effective": 2, "temporal_blocks": 1},
    {"raw": 2, "resolved": 1, "effective": 1, "temporal_blocks": 2},
))
def test_feature_horizon_contract_rejects_inconsistent_counts(
    counts: dict[str, int],
) -> None:
    row = _row("price.ret_5m")
    row["by_horizon"]["30"].update(counts)
    with pytest.raises(AssertionError, match="count relationships invalid"):
        inventory._validate_feature_horizons([row])


def test_feature_horizon_contract_binds_maturity_to_counts() -> None:
    valid = _row("price.ret_5m")
    valid["by_horizon"]["60"].update({
        "raw": 100,
        "effective": 50,
        "resolved": 100,
        "temporal_blocks": 2,
        "coverage_pct": 50.0,
        "data_maturity": "DATA_READY_EARLY",
    })
    inventory._validate_feature_horizons([valid])

    overclaim = _row("price.ret_5m")
    overclaim["by_horizon"]["60"].update({
        "raw": 1,
        "effective": 1,
        "resolved": 1,
        "temporal_blocks": 1,
        "data_maturity": "DATA_READY_ROBUST",
    })
    with pytest.raises(AssertionError, match="does not match canonical counts"):
        inventory._validate_feature_horizons([overclaim])


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
        _row(
            "price.ret_5m",
            real=1000,
            usable=True,
            maturity="DATA_READY_ROBUST",
        ),
    ) == "DATA_READY"


def test_coverage_state_fails_closed_on_missing_or_unknown_data_maturity() -> None:
    definitions = {item.feature_id: item for item in inventory.FEATURES}
    definition = definitions["price.ret_5m"]

    missing = _row("price.ret_5m", real=1000, usable=True)
    missing.pop("data_maturity")
    assert inventory._coverage_state(
        definition, missing) == "INSUFFICIENT_INDEPENDENT_EVIDENCE"

    assert inventory._coverage_state(
        definition,
        _row(
            "price.ret_5m",
            real=1000,
            usable=True,
            maturity="FUTURE_UNREVIEWED_TIER",
        ),
    ) == "INSUFFICIENT_INDEPENDENT_EVIDENCE"


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


def test_horizon_classification_uses_only_recognized_mature_training_evidence() -> None:
    definitions = {item.feature_id: item for item in inventory.FEATURES}
    definition = definitions["price.ret_5m"]

    insufficient = _row(
        "price.ret_5m",
        real=1000,
        usable=True,
        maturity="DATA_READY_ROBUST",
        by_horizon={
            "15": _bucket(
                raw=100, effective=49, resolved=100,
                temporal_blocks=2, coverage_pct=100.0),
        },
    )
    assert inventory._horizon_coverage_state(
        definition, insufficient, 15) == "INSUFFICIENT_INDEPENDENT_EVIDENCE"

    ready = _row(
        "price.ret_5m",
        real=1000,
        usable=True,
        maturity="DATA_READY_ROBUST",
        by_horizon={
            "15": _bucket(
                raw=100, effective=50, resolved=100, temporal_blocks=2,
                coverage_pct=100.0, maturity="DATA_READY_EARLY"),
        },
    )
    assert inventory._horizon_coverage_state(
        definition, ready, 15) == "DATA_READY"

    unknown = _row(
        "price.ret_5m",
        real=1000,
        usable=True,
        maturity="DATA_READY_ROBUST",
        by_horizon={
            "15": _bucket(
                raw=1000, effective=500, resolved=900, temporal_blocks=4,
                coverage_pct=100.0, maturity="FUTURE_UNREVIEWED_TIER"),
        },
    )
    assert inventory._horizon_coverage_state(
        definition, unknown, 15) == "INSUFFICIENT_INDEPENDENT_EVIDENCE"


def test_horizon_classification_fails_closed_without_training_eligible_evidence() -> None:
    definitions = {item.feature_id: item for item in inventory.FEATURES}
    row = _row(
        "price.ret_5m",
        real=1000,
        usable=True,
        maturity="DATA_READY_ROBUST",
        by_horizon={
            # A globally observed feature can still have no eligible evidence at
            # one horizon (for example after stale values were rejected).
            "15": _bucket(
                raw=0, effective=0, resolved=0,
                temporal_blocks=0, coverage_pct=0.0,
                maturity="DATA_READY_ROBUST"),
        },
    )
    assert inventory._horizon_coverage_state(
        definitions["price.ret_5m"], row, 15) == "NO_REAL_OBSERVATIONS"


def test_enriched_inventory_exposes_registry_source_and_family_horizon_counts() -> None:
    rows = [
        _row("option.iv"),
        _row(
            "option.skew",
            real=20,
            by_horizon={
                "15": _bucket(
                    raw=20, effective=10, resolved=15, temporal_blocks=1,
                    coverage_pct=50.0),
            },
        ),
        _row(
            "price.ret_5m",
            real=1000,
            usable=True,
            maturity="DATA_READY_ROBUST",
            by_horizon={
                "15": _bucket(
                    raw=1000, effective=500, resolved=900, temporal_blocks=4,
                    coverage_pct=100.0, maturity="DATA_READY_ROBUST"),
            },
        ),
    ]

    enriched = inventory._enrich_features(rows)
    by_id = {row["feature_id"]: row for row in enriched}
    assert by_id["option.iv"]["family"] == "OPTIONS"
    assert by_id["option.iv"]["producer"]
    assert by_id["option.iv"]["history_strategy"] == "PROSPECTIVE_ONLY"
    assert by_id["option.iv"]["coverage_state"] == "HISTORICAL_DATA_MISSING"
    assert by_id["option.skew"]["coverage_state"] == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    assert by_id["price.ret_5m"]["coverage_state"] == "DATA_READY"

    assert by_id["option.skew"]["by_horizon"]["15"] == _bucket(
        raw=20, effective=10, resolved=15, temporal_blocks=1,
        coverage_pct=50.0)
    assert by_id["option.iv"]["coverage_state_by_horizon"]["15"] == (
        "HISTORICAL_DATA_MISSING")
    assert by_id["option.skew"]["coverage_state_by_horizon"]["15"] == (
        "INSUFFICIENT_INDEPENDENT_EVIDENCE")
    assert by_id["price.ret_5m"]["coverage_state_by_horizon"]["15"] == "DATA_READY"

    families = {row["family"]: row for row in inventory._family_summary(enriched)}
    assert families["OPTIONS"]["feature_count"] == 2
    assert families["OPTIONS"]["with_real_observations"] == 1
    assert families["OPTIONS"]["coverage_state_counts"] == {
        "HISTORICAL_DATA_MISSING": 1,
        "INSUFFICIENT_INDEPENDENT_EVIDENCE": 1,
    }
    horizon = families["OPTIONS"]["by_horizon"]["15"]
    assert horizon["feature_count"] == 2
    assert horizon["with_training_eligible_observations"] == 1
    assert horizon["with_resolved_observations"] == 1
    assert horizon["data_ready_features"] == 0
    assert horizon["zero_coverage_features"] == 1
    assert horizon["raw_feature_observations"] == 20
    assert horizon["effective_feature_observations"] == 10
    assert horizon["resolved_feature_observations"] == 15
    assert horizon["temporal_block_counts"] == {"0": 1, "1": 1}
    assert horizon["coverage_pct_min"] == 0.0
    assert horizon["coverage_pct_mean"] == 25.0
    assert horizon["coverage_pct_max"] == 50.0
    assert horizon["data_maturity_counts"] == {"INSUFFICIENT_DATA": 2}
    assert horizon["coverage_state_counts"] == {
        "HISTORICAL_DATA_MISSING": 1,
        "INSUFFICIENT_INDEPENDENT_EVIDENCE": 1,
    }
    assert horizon["production_authority"] is False

    assert families["PRICE"]["usable_for_ede"] == 1
    assert families["PRICE"]["by_horizon"]["15"]["data_ready_features"] == 1
    assert families["PRICE"]["production_authority"] is False
