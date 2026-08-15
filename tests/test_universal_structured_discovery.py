from __future__ import annotations

import numpy as np
import pytest

from seiltanzer.edge_discovery.filters import CandidateTemplate, ConditionTemplate, candidate_templates
from seiltanzer.edge_discovery.rates_registry import RATES_FEATURE_DEFINITIONS
from seiltanzer.edge_discovery.universal_structured_discovery import (
    _aggregate_candidate,
    _candidate_uses_rates,
    _evaluate_rule,
)
from seiltanzer.edge_discovery.universal_target_scoring import UniversalTargetSpec
from seiltanzer.edge_discovery.universal_templates import (
    MAX_UNIVERSAL_TEMPLATES,
    universal_candidate_templates,
    universal_feature_definitions,
)


def _row(index: int, rate: float, target: float) -> dict:
    return {
        "instrument": "NAS100",
        "horizon_minutes": 30,
        "captured_ts": float(index * 1800),
        "target_ts": float(index * 1800 + 1800),
        "ede_features": {"rates.us10y_yield": rate},
        "universal_target_id": "RETURN_SIGMA",
        "universal_target_value": target,
    }


def test_universal_template_layer_does_not_mutate_legacy_template_universe() -> None:
    legacy_before = candidate_templates()
    definitions = universal_feature_definitions()
    eligible = [item.feature_id for item in RATES_FEATURE_DEFINITIONS]
    universal = universal_candidate_templates(
        eligible_feature_ids=eligible, feature_definitions=definitions)
    legacy_after = candidate_templates()
    assert legacy_before == legacy_after
    assert len(universal) <= MAX_UNIVERSAL_TEMPLATES
    rate_conditions = [condition.feature_id for template in universal
                       for condition in template.conditions
                       if condition.feature_id.startswith("rates.")]
    assert "rates.us10y_yield" in rate_conditions
    assert "rates.us02y_yield" in rate_conditions


def test_structured_continuous_rule_can_detect_train_fitted_state_shift() -> None:
    spec = UniversalTargetSpec("RETURN_SIGMA", "RETURN", "CONTINUOUS", (),
                               ("mae", "rmse"))
    template = CandidateTemplate((ConditionTemplate(
        "rates.us10y_yield", "train_relative", "ABOVE_MEDIAN"),))
    train = []
    for index in range(180):
        high = index >= 90
        if high:
            target = 0.72 if index % 2 else 0.88
        else:
            target = -0.72 if index % 2 else -0.88
        train.append(_row(index+1, 4.6 if high else 3.8, target))
    test = []
    for index in range(60):
        high = index >= 30
        if high:
            target = 0.70 if index % 2 else 0.82
        else:
            target = -0.70 if index % 2 else -0.82
        test.append(_row(index+1000, 4.7 if high else 3.7, target))
    result = _evaluate_rule(template, train, test, spec)
    assert result is not None
    assert result["rule"]["conditions"][0]["train_cutoff_ts"] == max(
        row["captured_ts"] for row in train)
    assert result["selected_train_raw_n"] == 90
    assert result["model"]["raw_n"] == 30
    assert result["improvement"]["mae"] > 0.0
    assert result["improvement"]["rmse"] > 0.0
    assert result["p_value"] < 0.10


def test_universal_candidate_identity_includes_target_horizon() -> None:
    spec = UniversalTargetSpec("RETURN_SIGMA", "RETURN", "CONTINUOUS", (),
                               ("mae", "rmse"))
    rows = [_row(2000+i, 4.2, target) for i, target in enumerate((0.4, 0.7, 0.6))]
    evaluation = {
        "rows": rows,
        "model_prediction": np.asarray((0.42, 0.68, 0.61)),
        "baseline_prediction": np.asarray((0.0, 0.0, 0.0)),
        "rule": {"conditions": [{"feature_id": "trend_efficiency_60"}]},
        "model": {"raw_n": 3, "effective_n": 3},
        "improvement": {"mae": 0.8, "rmse": 0.8},
    }
    occurrence = [{
        "fold_index": 0,
        "test_start_ts": rows[0]["captured_ts"],
        "test_end_ts": rows[-1]["target_ts"],
        "purge_embargo_valid": True,
        "evaluation": evaluation,
    }]
    short = _aggregate_candidate("same-template", occurrence, spec, horizon=15)
    long = _aggregate_candidate("same-template", occurrence, spec, horizon=60)
    assert short["candidate_id"] != long["candidate_id"]
    assert short["horizon_minutes"] == 15
    assert long["horizon_minutes"] == 60


def test_universal_feature_definitions_include_rates_without_claiming_live_confirmation() -> None:
    definitions = {item.feature_id: item for item in universal_feature_definitions()}
    assert definitions["rates.us10y_yield"].family == "RATES"
    assert definitions["rates.us10y_yield"].live_availability == "LIMITED"
    assert definitions["rates.us10y_yield"].historical_availability == "AVAILABLE"


def test_daily_rates_candidates_are_identified_for_dependency_quarantine() -> None:
    assert _candidate_uses_rates({
        "conditions": [{"feature_id": "rates.us10y_yield"}]}) is True
    assert _candidate_uses_rates({
        "conditions": [{"feature_id": "option_dynamics.gex_velocity"}]}) is False
