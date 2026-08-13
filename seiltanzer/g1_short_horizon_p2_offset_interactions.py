"""Predeclared sign×context refinement for P2D persistence-offset research.

The persistence offset already contains the direction sign. To learn *confidence*
rather than a one-sided global shift, the correction needs sign-conditioned
context terms. For every selected causal P2 feature ``x`` we therefore expose:

  x
  sign(ret5) * x

plus ``sign(ret5)`` itself and instrument dummies. A negative coefficient on
``sign(ret5)*RV`` can reduce persistence confidence in high volatility for both
positive and negative ret5 states. No target/future value is used to construct
these interactions.

This module is branch-only research. It patches the P2D fitter only inside an
offline experiment call and creates no production model/cohort/authority.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from . import g1_short_horizon_p2_persistence_offset as _offset
from . import g1_short_horizon_p2_regime_research as _p2
from .g1_short_horizon_historical_wf import _weights


INTERACTION_CONTRACT_VERSION = "g1s-p2d-offset-sign-interactions-v1"


def interaction_feature_names(family: str) -> list[str]:
    base = list(_p2.FEATURE_FAMILIES[family])
    return (
        ["persistence_sign"]
        + base
        + [f"persistence_sign*{name}" for name in base]
        + [f"instrument:{code}" for code in _p2.INSTRUMENT_DUMMIES]
    )


def offset_interaction_matrix(rows: list[dict[str, Any]], family: str) -> np.ndarray:
    names = _p2.FEATURE_FAMILIES[family]
    matrix = []
    for row in rows:
        features = row["p2_features"]
        sign = 1.0 if float(row["features"]["ret_5m"]) > 0 else -1.0
        base = [float(features[name]) for name in names]
        interactions = [sign * value for value in base]
        dummies = [1.0 if row["instrument"] == code else 0.0
                   for code in _p2.INSTRUMENT_DUMMIES]
        vector = [sign] + base + interactions + dummies
        if not np.all(np.isfinite(vector)):
            raise ValueError(f"non-finite offset interaction vector: {family}")
        matrix.append(vector)
    return np.asarray(matrix, dtype=float)


def fit_offset_logistic_interactions(train: list[dict[str, Any]], family: str,
                                     l2: float) -> dict[str, Any]:
    x = offset_interaction_matrix(train, family)
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, effective = _weights(train)
    baseline = _offset._persistence_prediction(train, train)
    raw_offset = _offset._logit_array(baseline)
    mean, std = _offset._fit_standardization(x, weights)
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    regularizer = np.eye(design.shape[1], dtype=float) * float(l2)
    regularizer[0, 0] = float(l2) * 0.25
    for _ in range(80):
        score = raw_offset + design @ beta
        probability = 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))
        variance = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = design.T @ (weights * (probability - y)) + regularizer @ beta
        hessian = design.T @ ((weights * variance)[:, None] * design) + regularizer
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return {
        "contract_version": INTERACTION_CONTRACT_VERSION,
        "model_family": _offset.OFFSET_MODEL_FAMILY,
        "feature_family": family,
        "feature_names": interaction_feature_names(family),
        "l2": float(l2),
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "correction_intercept_and_coefficients": beta.tolist(),
        "train_raw_n": len(train), "train_effective_n": effective,
        "baseline": "causal_ret5_persistence_probability",
        "sign_context_interactions": True,
        "beta_zero_recovers_baseline_exactly": True,
    }


def predict_offset_interactions(train: list[dict[str, Any]], rows: list[dict[str, Any]],
                                artifact: dict[str, Any]) -> np.ndarray:
    baseline = _offset._persistence_prediction(train, rows)
    x = offset_interaction_matrix(rows, str(artifact["feature_family"]))
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    std = np.asarray(artifact["feature_std"], dtype=float)
    beta = np.asarray(artifact["correction_intercept_and_coefficients"], dtype=float)
    z = (x - mean) / np.where(std < 1e-12, 1.0, std)
    design = np.column_stack([np.ones(len(z)), z])
    score = _offset._logit_array(baseline) + design @ beta
    return 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))


def run_offset_experiment_interactions(runtime) -> dict[str, Any]:
    previous_fit = _offset._fit_offset_logistic
    previous_predict = _offset._predict_offset
    _offset._fit_offset_logistic = fit_offset_logistic_interactions
    _offset._predict_offset = predict_offset_interactions
    try:
        result = _offset.run_offset_experiment(runtime)
        result["interaction_contract_version"] = INTERACTION_CONTRACT_VERSION
        result["sign_context_interactions"] = True
        return result
    finally:
        _offset._fit_offset_logistic = previous_fit
        _offset._predict_offset = previous_predict
