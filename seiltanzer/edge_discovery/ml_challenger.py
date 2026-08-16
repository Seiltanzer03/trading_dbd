"""Bounded research-only ML challenger for universal market outcomes.

PASS 6 asks one deliberately narrow question: after the accepted causal
instrument/family/global baseline has already explained stable cross-sectional
base rates, can a fixed shallow nonlinear model explain an incremental market-
state residual on purged OOS folds?

This is not AutoML, not a production model, and not terminal authority.
scikit-learn is imported lazily through the optional ``research`` dependency.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _historical_folds, _weights

from .rates import RatesState
from .scoring import benjamini_hochberg
from .universal_structured_discovery import build_universal_discovery_rows
from .universal_target_scoring import (
    BASELINE_METHOD,
    DEPENDENCY_PVALUE_METHOD,
    UniversalTargetSpec,
    eligible_target_rows,
    paired_target_pvalue,
    relative_target_improvement,
    structural_baseline_predictions,
    target_metrics,
    universal_target_specs,
)


ML_CHALLENGER_VERSION = "g1s-ml-challenger-v2"
MODEL_FAMILY = "sklearn.HistGradientBoostingRegressor.structural_residual"
MAX_FEATURES = 32
MIN_FEATURE_COVERAGE = 0.70
MIN_TRAIN_RAW = 500
MIN_TRAIN_EFFECTIVE = 200
MIN_TEST_RAW = 100
MIN_TEST_EFFECTIVE = 50
MIN_POSITIVE_FOLDS = 3
MIN_RELATIVE_IMPROVEMENT = 0.005
MAX_Q_VALUE = 0.10
EPS = 1e-6

# Data-quality/availability fields may predict collection conditions rather than
# the market and therefore are never admissible model inputs.
_FORBIDDEN_FEATURE_TOKENS = (
    "asof", "stale", "coverage", "peer_count", "quality", "metadata",
)


@dataclass(frozen=True)
class NumericFeatureSchema:
    feature_ids: tuple[str, ...]
    medians: tuple[float, ...]
    coverage: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_ids": list(self.feature_ids),
            "medians": list(self.medians),
            "coverage": list(self.coverage),
        }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _admissible_feature_id(feature_id: str) -> bool:
    value = str(feature_id)
    if value.startswith("rates."):
        # PASS 4 Treasury context is repeated daily data. It remains quarantined
        # from ML until a dedicated rates-coverage/dependency gate is accepted.
        return False
    lower = value.lower()
    return not any(token in lower for token in _FORBIDDEN_FEATURE_TOKENS)


def fit_numeric_feature_schema(rows: list[dict[str, Any]]) -> NumericFeatureSchema:
    """Fit an outcome-blind numeric feature schema on outer-train rows only."""
    if not rows:
        return NumericFeatureSchema((), (), ())
    values: dict[str, list[float]] = {}
    counts: Counter[str] = Counter()
    for row in rows:
        for feature_id, raw in (row.get("ede_features") or {}).items():
            if not _admissible_feature_id(str(feature_id)):
                continue
            number = _finite(raw)
            if number is None:
                continue
            values.setdefault(str(feature_id), []).append(number)
            counts[str(feature_id)] += 1
    candidates: list[tuple[float, str, float]] = []
    total = float(len(rows))
    for feature_id, observed in values.items():
        coverage = counts[feature_id]/total
        if coverage < MIN_FEATURE_COVERAGE or len(observed) < 3:
            continue
        if float(np.std(np.asarray(observed, dtype=float))) <= 1e-12:
            continue
        median = float(np.median(np.asarray(observed, dtype=float)))
        candidates.append((coverage, feature_id, median))
    # Selection uses availability/variance only, never target association.
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = candidates[:MAX_FEATURES]
    return NumericFeatureSchema(
        tuple(item[1] for item in selected),
        tuple(item[2] for item in selected),
        tuple(float(item[0]) for item in selected),
    )


def transform_numeric_features(
    rows: list[dict[str, Any]], schema: NumericFeatureSchema,
) -> np.ndarray:
    matrix = np.empty((len(rows), len(schema.feature_ids)), dtype=float)
    for row_index, row in enumerate(rows):
        features = row.get("ede_features") or {}
        for column, (feature_id, median) in enumerate(
                zip(schema.feature_ids, schema.medians)):
            value = _finite(features.get(feature_id))
            matrix[row_index, column] = median if value is None else value
    return matrix


def _require_regressor():
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:  # pragma: no cover - research workflow only
        raise RuntimeError(
            "ML challenger requires optional dependency: pip install -e '.[research]'"
        ) from exc
    return HistGradientBoostingRegressor


def sklearn_version() -> str:
    try:
        import sklearn
    except ImportError as exc:  # pragma: no cover - research workflow only
        raise RuntimeError(
            "ML challenger requires optional dependency: pip install -e '.[research]'"
        ) from exc
    return str(sklearn.__version__)


def _new_regressor():
    regressor = _require_regressor()
    return regressor(
        learning_rate=0.05,
        max_iter=80,
        max_leaf_nodes=15,
        max_depth=3,
        min_samples_leaf=40,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=17,
    )


def _baseline_predictions(
    train: list[dict[str, Any]], test: list[dict[str, Any]], spec: UniversalTargetSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Accepted PASS 5 baseline, fitted strictly on this outer-train cohort."""
    train_baseline = structural_baseline_predictions(train, train, spec)
    test_baseline = structural_baseline_predictions(train, test, spec)
    return np.asarray(train_baseline), np.asarray(test_baseline)


def _targets(rows: list[dict[str, Any]], spec: UniversalTargetSpec) -> np.ndarray:
    if spec.kind == "CONTINUOUS":
        return np.asarray([
            float(row["universal_target_value"]) for row in rows], dtype=float)
    if spec.kind == "BINARY":
        positive = spec.classes[-1]
        return np.asarray([
            1.0 if str(row["universal_target_value"]) == positive else 0.0
            for row in rows
        ], dtype=float)
    if spec.kind == "MULTICLASS":
        index = {label: position for position, label in enumerate(spec.classes)}
        output = np.zeros((len(rows), len(spec.classes)), dtype=float)
        for row_index, row in enumerate(rows):
            output[row_index, index[str(row["universal_target_value"])]] = 1.0
        return output
    raise ValueError(f"unsupported target kind: {spec.kind}")


def fit_residual_model(
    train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]],
    x_train: np.ndarray, x_test: np.ndarray, spec: UniversalTargetSpec,
) -> tuple[np.ndarray, np.ndarray, tuple[Any, ...]]:
    """Fit fixed shallow trees to residuals from the structural train baseline."""
    train_baseline, test_baseline = _baseline_predictions(
        train_rows, test_rows, spec)
    target = _targets(train_rows, spec)
    sample_weight, _effective = _weights(train_rows)
    models: list[Any] = []

    if spec.kind == "CONTINUOUS":
        model = _new_regressor()
        model.fit(x_train, target-train_baseline, sample_weight=sample_weight)
        prediction = test_baseline+np.asarray(model.predict(x_test), dtype=float)
        return prediction, test_baseline, (model,)

    if spec.kind == "BINARY":
        model = _new_regressor()
        model.fit(x_train, target-train_baseline, sample_weight=sample_weight)
        prediction = np.clip(
            test_baseline+np.asarray(model.predict(x_test), dtype=float),
            EPS, 1.0-EPS)
        return prediction, test_baseline, (model,)

    if spec.kind == "MULTICLASS":
        corrections: list[np.ndarray] = []
        for class_index in range(len(spec.classes)):
            model = _new_regressor()
            model.fit(
                x_train,
                target[:, class_index]-train_baseline[:, class_index],
                sample_weight=sample_weight,
            )
            models.append(model)
            corrections.append(np.asarray(model.predict(x_test), dtype=float))
        prediction = test_baseline+np.column_stack(corrections)
        prediction = np.maximum(prediction, EPS)
        prediction = prediction/np.maximum(
            prediction.sum(axis=1, keepdims=True), EPS)
        return prediction, test_baseline, tuple(models)

    raise ValueError(f"unsupported target kind: {spec.kind}")


def predict_residual_models(
    models: tuple[Any, ...], x: np.ndarray, baseline: np.ndarray,
    spec: UniversalTargetSpec,
) -> np.ndarray:
    if spec.kind in {"CONTINUOUS", "BINARY"}:
        prediction = np.asarray(baseline)+np.asarray(
            models[0].predict(x), dtype=float)
        if spec.kind == "BINARY":
            prediction = np.clip(prediction, EPS, 1.0-EPS)
        return prediction
    if spec.kind != "MULTICLASS":
        raise ValueError(f"unsupported target kind: {spec.kind}")
    corrections = np.column_stack([
        np.asarray(model.predict(x), dtype=float) for model in models])
    prediction = np.asarray(baseline)+corrections
    prediction = np.maximum(prediction, EPS)
    return prediction/np.maximum(prediction.sum(axis=1, keepdims=True), EPS)


def _metric_ratio(model: dict[str, Any], baseline: dict[str, Any],
                  spec: UniversalTargetSpec) -> float:
    return float(np.mean([
        float(model[name])/max(abs(float(baseline[name])), 1e-12)
        for name in spec.primary_metrics
    ]))


def permutation_importance_diagnostic(
    rows: list[dict[str, Any]], x: np.ndarray, baseline_prediction: np.ndarray,
    models: tuple[Any, ...], schema: NumericFeatureSchema, spec: UniversalTargetSpec,
    *, max_features: int = 16,
) -> dict[str, float]:
    """Single-permutation OOS diagnostics only; never evidence/feature selection."""
    original_prediction = predict_residual_models(
        models, x, baseline_prediction, spec)
    baseline_metrics = target_metrics(rows, baseline_prediction, spec)
    original_metrics = target_metrics(rows, original_prediction, spec)
    original_ratio = _metric_ratio(original_metrics, baseline_metrics, spec)
    output: dict[str, float] = {}
    for column, feature_id in enumerate(schema.feature_ids[:max_features]):
        permuted = np.array(x, copy=True)
        seed = int(hashlib.sha256(feature_id.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        permuted[:, column] = permuted[
            rng.permutation(len(permuted)), column]
        prediction = predict_residual_models(
            models, permuted, baseline_prediction, spec)
        metrics = target_metrics(rows, prediction, spec)
        output[feature_id] = (
            _metric_ratio(metrics, baseline_metrics, spec)-original_ratio)
    return output


def _sample_allowed(
    train: list[dict[str, Any]], test: list[dict[str, Any]],
) -> bool:
    if len(train) < MIN_TRAIN_RAW or len(test) < MIN_TEST_RAW:
        return False
    _train_weights, train_effective = _weights(train)
    _test_weights, test_effective = _weights(test)
    return (
        int(train_effective) >= MIN_TRAIN_EFFECTIVE
        and int(test_effective) >= MIN_TEST_EFFECTIVE
    )


def _evaluate_fold(
    fold: dict[str, Any], spec: UniversalTargetSpec,
) -> dict[str, Any] | None:
    train = list(fold["train"])
    test = list(fold["test"])
    if not _sample_allowed(train, test):
        return None
    schema = fit_numeric_feature_schema(train)
    if len(schema.feature_ids) < 2:
        return None
    x_train = transform_numeric_features(train, schema)
    x_test = transform_numeric_features(test, schema)
    prediction, baseline_prediction, models = fit_residual_model(
        train, test, x_train, x_test, spec)
    model = target_metrics(test, prediction, spec)
    baseline = target_metrics(test, baseline_prediction, spec)
    improvement = relative_target_improvement(model, baseline, spec)
    p_value = paired_target_pvalue(
        test, prediction, baseline_prediction, spec)
    importance = permutation_importance_diagnostic(
        test, x_test, baseline_prediction, models, schema, spec)
    return {
        "fold_index": int(fold["fold_index"]),
        "test_start_ts": fold["test_start_ts"],
        "test_end_ts": fold["test_end_ts"],
        "purge_embargo_valid": (
            fold["train_target_max_ts"] < fold["purge_boundary_ts"]),
        "schema": schema.as_dict(),
        "model": model,
        "baseline": baseline,
        "improvement": improvement,
        "joint_positive": all(
            float(improvement[name]) > 0.0 for name in spec.primary_metrics),
        "p_value": float(p_value),
        "permutation_importance": importance,
        "rows": test,
        "prediction": prediction,
        "baseline_prediction": baseline_prediction,
    }


def run_ml_challenger(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
    rates_states: Iterable[RatesState] = (),
) -> dict[str, Any]:
    """Run one fixed ML hypothesis per universal target+horizon on purged OOS."""
    # Rates remain deliberately excluded from ML input. Structured PASS 5 keeps
    # them as dependency-quarantined diagnostics instead.
    _ = tuple(rates_states)
    sample_rows = build_universal_discovery_rows(
        sources, 15, rates_states=())
    barrier_ids: set[str] = set()
    for row in sample_rows[:100]:
        barrier_ids.update(
            (row.get("universal_outcome") or {}).get("barriers", {}).keys())
    specs = universal_target_specs(barrier_ids)

    candidates: list[dict[str, Any]] = []
    horizon_reports: list[dict[str, Any]] = []
    for horizon in (15, 30, 60, 120, 240):
        raw_rows = (
            sample_rows if horizon == 15
            else build_universal_discovery_rows(
                sources, horizon, rates_states=())
        )
        target_reports: list[dict[str, Any]] = []
        for spec in specs:
            rows = eligible_target_rows(raw_rows, spec)
            folds = _historical_folds(rows, horizon)
            evaluated = [
                item for fold in folds
                if (item := _evaluate_fold(fold, spec)) is not None
            ]
            if not evaluated:
                target_reports.append({
                    "target_id": spec.target_id,
                    "raw_rows": len(rows),
                    "folds_evaluated": 0,
                    "reason": "INSUFFICIENT_ML_SAMPLE_OR_FEATURE_COVERAGE",
                })
                continue

            combined_rows: list[dict[str, Any]] = []
            model_parts: list[np.ndarray] = []
            baseline_parts: list[np.ndarray] = []
            feature_frequency: Counter[str] = Counter()
            importance_values: dict[str, list[float]] = {}
            for item in evaluated:
                combined_rows.extend(item["rows"])
                model_parts.append(np.asarray(item["prediction"]))
                baseline_parts.append(np.asarray(item["baseline_prediction"]))
                feature_frequency.update(item["schema"]["feature_ids"])
                for feature_id, value in item["permutation_importance"].items():
                    importance_values.setdefault(feature_id, []).append(float(value))

            model_prediction = np.concatenate(model_parts, axis=0)
            baseline_prediction = np.concatenate(baseline_parts, axis=0)
            model = target_metrics(combined_rows, model_prediction, spec)
            baseline = target_metrics(
                combined_rows, baseline_prediction, spec)
            improvement = relative_target_improvement(model, baseline, spec)
            p_value = paired_target_pvalue(
                combined_rows, model_prediction, baseline_prediction, spec)
            candidate = {
                "candidate_id": "g1s-ml-" + _sha({
                    "version": ML_CHALLENGER_VERSION,
                    "target": spec.target_id,
                    "horizon": horizon,
                    "model": MODEL_FAMILY,
                    "baseline": BASELINE_METHOD,
                    "dependency": DEPENDENCY_PVALUE_METHOD,
                })[:24],
                "target_id": spec.target_id,
                "target_family": spec.family,
                "target_kind": spec.kind,
                "horizon_minutes": horizon,
                "model_family": MODEL_FAMILY,
                "model_library_version": sklearn_version(),
                "model_hyperparameters": {
                    "learning_rate": 0.05,
                    "max_iter": 80,
                    "max_leaf_nodes": 15,
                    "max_depth": 3,
                    "min_samples_leaf": 40,
                    "l2_regularization": 1.0,
                    "early_stopping": False,
                    "random_state": 17,
                    "tuning": "NONE_FIXED_A_PRIORI_V2",
                },
                "baseline_method": BASELINE_METHOD,
                "dependency_pvalue_method": DEPENDENCY_PVALUE_METHOD,
                "baseline": baseline,
                "model": model,
                "improvement": improvement,
                "primary_improvement": min(
                    float(improvement[name]) for name in spec.primary_metrics),
                "p_value": float(p_value),
                "fold_positive": sum(
                    bool(item["joint_positive"]) for item in evaluated),
                "fold_evaluated": len(evaluated),
                "folds": [
                    {key: value for key, value in item.items()
                     if key not in {"rows", "prediction", "baseline_prediction"}}
                    for item in evaluated
                ],
                "feature_frequency": dict(sorted(feature_frequency.items())),
                "permutation_importance_median": {
                    feature_id: float(np.median(values))
                    for feature_id, values in sorted(importance_values.items())
                },
                "rates_features_excluded": True,
                "rates_exclusion_reason": (
                    "daily Treasury dependency/coverage confirmation pending"),
                "discovery_only": True,
                "prospective_confirmation": False,
                "production_authority": False,
                "auto_promotion": False,
            }
            candidates.append(candidate)
            target_reports.append({
                "target_id": spec.target_id,
                "raw_rows": len(rows),
                "folds_evaluated": len(evaluated),
                "candidate_id": candidate["candidate_id"],
            })
        horizon_reports.append({
            "horizon_minutes": horizon,
            "raw_rows": len(raw_rows),
            "targets": target_reports,
        })

    # One fixed challenger hypothesis exists for each evaluated target+horizon;
    # all of them share one explicit BH family. No tuning/search hypotheses are
    # hidden outside this correction family.
    q_values = benjamini_hochberg([
        float(item["p_value"]) for item in candidates])
    signals = []
    for candidate, q_value in zip(candidates, q_values):
        candidate["q_value"] = float(q_value)
        qualified = (
            float(q_value) <= MAX_Q_VALUE
            and float(candidate["primary_improvement"]) >= MIN_RELATIVE_IMPROVEMENT
            and int(candidate["fold_positive"]) >= MIN_POSITIVE_FOLDS
        )
        candidate["status"] = (
            "ML_DISCOVERY_SIGNAL" if qualified else "ML_RESEARCH_DIAGNOSTIC")
        if qualified:
            signals.append(candidate)

    candidates.sort(key=lambda item: (
        item["status"] != "ML_DISCOVERY_SIGNAL",
        -float(item["primary_improvement"]),
        float(item["q_value"]),
        str(item["candidate_id"]),
    ))
    return {
        "contract_version": ML_CHALLENGER_VERSION,
        "source_set_sha256": str(source_set_sha256),
        "model_family": MODEL_FAMILY,
        "model_library": "scikit-learn",
        "model_library_version": sklearn_version(),
        "baseline_method": BASELINE_METHOD,
        "dependency_pvalue_method": DEPENDENCY_PVALUE_METHOD,
        "fdr_family": "ALL_EVALUATED_TARGET_HORIZON_CHALLENGERS",
        "strategy_agnostic": True,
        "discovery_only": True,
        "prospective_confirmation": False,
        "production_authority": False,
        "auto_promotion": False,
        "hyperparameter_search": False,
        "shap_is_edge_proof": False,
        "rates_dependency_signal_gate": "EXCLUDED_FROM_ML_V2",
        "hypotheses_tested": len(candidates),
        "discovery_signal_count": len(signals),
        "horizons": horizon_reports,
        "candidates": candidates,
        "verdict": (
            "ML_DISCOVERY_SIGNALS_FOUND_NEED_FROZEN_PROSPECTIVE_CONFIRMATION"
            if signals else "NO_ML_DISCOVERY_SIGNAL_ON_CURRENT_EVIDENCE"
        ),
    }
