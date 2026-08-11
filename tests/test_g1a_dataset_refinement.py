import json

import pytest

from seiltanzer.config import Settings
from seiltanzer.g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION, PassiveLearningEngine

from test_g1a_dataset_contract import _fixed_forecast, _insert_resolved, _outcome


def test_runtime_validation_requires_current_measurement_valid_record(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    _insert_resolved(
        engine, "legacy", anchor="legacy-a", captured=captured, target=captured + 900,
        source_schema="passive-observation-f31-v1",
    )
    first = engine.g1_dataset_status()
    assert first["current_runtime_evaluated_n"] == 0
    assert first["measurement_valid_n"] == 0
    assert first["dataset_contract_runtime_validated"] is False

    _insert_resolved(
        engine, "current-manual", anchor="current-a", captured=captured + 1800,
        target=captured + 2700, origin="manual",
    )
    second = engine.g1_dataset_status()
    # Technically measured correctly, but excluded from prospective evidence.
    assert second["current_runtime_evaluated_n"] == 1
    assert second["measurement_valid_n"] == 1
    assert second["forecast_eval_eligible_n"] == 0
    assert second["dataset_contract_runtime_validated"] is True
    assert second["raw_n"] == second["raw_task_membership_n"]
    assert second["exclusion_counts"]["NOT_BACKGROUND_COLLECTOR"] >= 1
    engine.close()


def test_demo_and_synthetic_are_first_class_exclusions(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    _insert_resolved(
        engine, "demo", anchor="a1", captured=captured, target=captured + 900,
        price_kind="demo",
    )
    _insert_resolved(
        engine, "synthetic", anchor="a2", captured=captured + 1800,
        target=captured + 2700, price_kind="synthetic",
    )
    engine._g1_sync_membership()
    rows = {r["observation_id"]: json.loads(r["exclusion_reasons_json"])
            for r in engine._conn.execute(
                "SELECT observation_id,exclusion_reasons_json FROM g1_dataset_membership"
            ).fetchall()}
    assert "DEMO_DATA" in rows["demo"]
    assert "SYNTHETIC_DATA" in rows["synthetic"]
    engine.close()


@pytest.mark.parametrize(
    "schema,forecast_runtime",
    [
        ("passive-observation-f2-v1", MEASUREMENT_RUNTIME_VERSION),
        ("passive-observation-f3-v1", MEASUREMENT_RUNTIME_VERSION),
        ("passive-observation-f31-v1", MEASUREMENT_RUNTIME_VERSION),
        (PASSIVE_SCHEMA_VERSION, None),
    ],
)
def test_all_legacy_contract_classes_are_quarantined(tmp_path, schema, forecast_runtime):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    forecast = _fixed_forecast(15)
    if forecast_runtime is None:
        forecast.pop("measurement_runtime_contract", None)
    _insert_resolved(
        engine, "legacy", anchor="a", captured=captured, target=captured + 900,
        source_schema=schema, forecast=forecast,
    )
    engine._g1_sync_membership()
    row = dict(engine._conn.execute("SELECT * FROM g1_dataset_membership").fetchone())
    assert row["forecast_eval_eligible"] == 0
    reasons = json.loads(row["exclusion_reasons_json"])
    if schema != PASSIVE_SCHEMA_VERSION:
        assert "WRONG_SOURCE_SCHEMA" in reasons
    else:
        assert "WRONG_MEASUREMENT_RUNTIME" in reasons
    engine.close()


def test_non_overlapping_windows_each_add_effective_information(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    base = 1_800_000_000.0
    for i in range(4):
        captured = base + i * 3600
        target = captured + 900
        _insert_resolved(
            engine, f"o-{i}", anchor=f"a-{i}", captured=captured, target=target,
            horizon=15, forecast=_fixed_forecast(15),
        )
    cohort = engine.g1_dataset_cohorts()["items"][0]
    assert cohort["raw_n"] == 4
    assert cohort["unique_anchor_n"] == 4
    assert cohort["effective_n"] == 4
    engine.close()


@pytest.mark.parametrize("fail_after", [1, 4, 5])
def test_cut_atomicity_at_multiple_failure_positions(tmp_path, fail_after):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    base = 1_800_000_000.0
    for i in range(6):
        captured = base + i * 3600
        _insert_resolved(
            engine, f"o-{i}", anchor=f"a-{i}", captured=captured,
            target=captured + 900,
        )
    engine._g1_sync_membership()
    cutoff = base + 30_000
    with pytest.raises(RuntimeError, match="injected dataset cut failure"):
        engine.create_g1_dataset_cut(cutoff, _fail_after_members=fail_after)
    assert engine._conn.execute(
        "SELECT COUNT(*) FROM g1_dataset_cuts WHERE cutoff_ts=?", (cutoff,)
    ).fetchone()[0] == 0
    assert engine._conn.execute(
        "SELECT COUNT(*) FROM g1_dataset_cut_members"
    ).fetchone()[0] == 0
    engine.close()


def test_g1a_never_creates_fitted_model_registry_entries(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    _insert_resolved(engine, "one", anchor="a", captured=captured, target=captured + 900)
    engine.g1_dataset_status()
    engine.create_g1_dataset_cut(captured + 901)
    with engine._lock:
        # Existing repository registry remains untouched by G.1A.
        tables = {r[0] for r in engine._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "research_model_registry" in tables:
            assert engine._conn.execute("SELECT COUNT(*) FROM research_model_registry").fetchone()[0] == 0
    assert engine.g1_dataset_status()["g1_training_allowed"] is False
    engine.close()
