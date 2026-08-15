"""Research-only interpretable discovery across universal market outcomes.

PASS 5 intentionally runs beside the frozen legacy directional EDE.  It asks a
more general question: does a causal market-state filter shift a future market
path distribution relative to an unconditional train-only baseline?

No result in this module has production authority or prospective-confirmation
status.  Candidate freeze/confirmation remains a later PASS.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _historical_folds, _weights
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import _inner_split

from .filters import CandidateTemplate, fit_rule, rule_mask
from .rates import RatesState, attach_rates_context
from .scoring import benjamini_hochberg
from .universal_outcome_adapter import resolve_historical_universal_outcome
from .universal_target_scoring import (
    UniversalTargetSpec,
    eligible_target_rows,
    fitted_constant_predictions,
    paired_target_pvalue,
    relative_target_improvement,
    target_metrics,
    universal_target_specs,
)
from .universal_templates import universal_candidate_templates, universal_feature_definitions


UNIVERSAL_STRUCTURED_DISCOVERY_VERSION = "g1s-universal-structured-discovery-v1"
MAX_Q_VALUE = 0.10
MIN_RELATIVE_IMPROVEMENT = 0.005
OUTER_SELECTION_LIMIT = 8
MIN_INNER_TRAIN_RAW = 80
MIN_INNER_TRAIN_EFFECTIVE = 40
MIN_INNER_VALIDATION_RAW = 20
MIN_INNER_VALIDATION_EFFECTIVE = 10
MIN_INNER_CLASS = 5
MIN_OUTER_TEST_RAW = 20
MIN_STABLE_FOLDS = 3


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def build_universal_discovery_rows(
    sources: list[dict[str, Any]], horizon: int, *,
    rates_states: Iterable[RatesState] = (),
) -> list[dict[str, Any]]:
    """Attach PASS 2 outcomes and optional causal slow RATES context."""
    from .historical import build_discovery_rows

    rows = build_discovery_rows(sources, horizon)
    by_instrument = {str(source["instrument"]): source for source in sources}
    output: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        source = by_instrument.get(str(row["instrument"]))
        if source is None:
            continue
        outcome = resolve_historical_universal_outcome(source, row)
        row["universal_outcome"] = outcome
        if bool(outcome.get("available")):
            output.append(row)
    if rates_states:
        attach_rates_context(output, tuple(rates_states), confirmatory_frozen_at_t0=False)
    return output


def _effective_n(rows: list[dict[str, Any]]) -> int:
    _w, effective = _weights(rows)
    return int(effective)


def _class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row["universal_target_value"])] += 1
    return dict(counts)


def _sample_allowed(train: list[dict[str, Any]], validation: list[dict[str, Any]],
                    spec: UniversalTargetSpec) -> bool:
    if (len(train) < MIN_INNER_TRAIN_RAW or _effective_n(train) < MIN_INNER_TRAIN_EFFECTIVE
            or len(validation) < MIN_INNER_VALIDATION_RAW
            or _effective_n(validation) < MIN_INNER_VALIDATION_EFFECTIVE):
        return False
    if spec.kind in {"BINARY", "MULTICLASS"}:
        train_counts = _class_counts(train); validation_counts = _class_counts(validation)
        required = tuple(spec.classes)
        if any(train_counts.get(label, 0) < MIN_INNER_CLASS for label in required):
            return False
        if any(validation_counts.get(label, 0) < MIN_INNER_CLASS for label in required):
            return False
    if spec.kind == "CONTINUOUS":
        values = np.asarray([float(row["universal_target_value"]) for row in train], dtype=float)
        if len(values) < 3 or float(np.std(values)) <= 1e-12:
            return False
    return True


def _evaluate_rule(
    template: CandidateTemplate, train: list[dict[str, Any]], test: list[dict[str, Any]],
    spec: UniversalTargetSpec,
) -> dict[str, Any] | None:
    rule = fit_rule(template, train)
    if rule is None:
        return None
    selected_train = [row for row, keep in zip(train, rule_mask(train, rule)) if keep]
    selected_test = [row for row, keep in zip(test, rule_mask(test, rule)) if keep]
    if not _sample_allowed(selected_train, selected_test, spec):
        return None
    model_prediction, baseline_prediction = fitted_constant_predictions(
        train, selected_train, selected_test, spec)
    model = target_metrics(selected_test, model_prediction, spec)
    baseline = target_metrics(selected_test, baseline_prediction, spec)
    improvement = relative_target_improvement(model, baseline, spec)
    p_value = paired_target_pvalue(
        selected_test, model_prediction, baseline_prediction, spec)
    primary = min(float(improvement[name]) for name in spec.primary_metrics)
    return {
        "template_id": template.template_id,
        "complexity": template.complexity,
        "rule": rule.as_dict(),
        "target_id": spec.target_id,
        "target_family": spec.family,
        "target_kind": spec.kind,
        "model": model,
        "baseline": baseline,
        "improvement": improvement,
        "primary_improvement": primary,
        "p_value": float(p_value),
        "selected_train_raw_n": len(selected_train),
        "selected_train_effective_n": _effective_n(selected_train),
        "rows": selected_test,
        "model_prediction": model_prediction,
        "baseline_prediction": baseline_prediction,
    }


def _inner_discovery(
    outer_train: list[dict[str, Any]], *, horizon: int,
    spec: UniversalTargetSpec, templates: tuple[CandidateTemplate, ...],
) -> dict[str, Any]:
    train, validation = _inner_split(outer_train, horizon)
    if not train or not validation:
        return {"tested": len(templates), "sample_gate_passed": 0,
                "fdr_passed": 0, "selected": [],
                "reason": "INSUFFICIENT_PURGED_INNER_SPLIT"}
    evaluated: list[dict[str, Any]] = []
    for template in templates:
        item = _evaluate_rule(template, train, validation, spec)
        if item is not None:
            evaluated.append(item)
    q_values = benjamini_hochberg([float(item["p_value"]) for item in evaluated])
    for item, q_value in zip(evaluated, q_values):
        item["q_value"] = float(q_value)
    positive = [item for item in evaluated
                if float(item["q_value"]) <= MAX_Q_VALUE
                and float(item["primary_improvement"]) > 0.0]
    positive.sort(key=lambda item: (
        -float(item["primary_improvement"]), float(item["q_value"]),
        int(item["complexity"]), str(item["template_id"])))
    return {
        "tested": len(templates),
        "sample_gate_passed": len(evaluated),
        "fdr_passed": sum(float(item["q_value"]) <= MAX_Q_VALUE for item in evaluated),
        "positive_fdr_passed": len(positive),
        "selected": positive[:OUTER_SELECTION_LIMIT],
        "multiple_testing": "Benjamini-Hochberg FDR within target+horizon inner fold",
    }


def _candidate_uses_rates(candidate: dict[str, Any]) -> bool:
    return any(
        str(condition.get("feature_id") or "").startswith("rates.")
        for condition in candidate.get("conditions") or []
    )


def _aggregate_candidate(
    template_id: str, occurrences: list[dict[str, Any]], spec: UniversalTargetSpec,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    model_parts: list[np.ndarray] = []
    baseline_parts: list[np.ndarray] = []
    folds: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for occurrence in occurrences:
        evaluation = occurrence["evaluation"]
        rows.extend(evaluation["rows"])
        model_parts.append(np.asarray(evaluation["model_prediction"]))
        baseline_parts.append(np.asarray(evaluation["baseline_prediction"]))
        rules.append(evaluation["rule"])
        folds.append({
            "fold_index": occurrence["fold_index"],
            "outer_test_start_ts": occurrence["test_start_ts"],
            "outer_test_end_ts": occurrence["test_end_ts"],
            "purge_embargo_valid": occurrence["purge_embargo_valid"],
            "raw_n": evaluation["model"]["raw_n"],
            "effective_n": evaluation["model"]["effective_n"],
            "improvement": evaluation["improvement"],
            "joint_positive": all(float(evaluation["improvement"][name]) > 0.0
                                  for name in spec.primary_metrics),
        })
    model_prediction = np.concatenate(model_parts, axis=0)
    baseline_prediction = np.concatenate(baseline_parts, axis=0)
    model = target_metrics(rows, model_prediction, spec)
    baseline = target_metrics(rows, baseline_prediction, spec)
    improvement = relative_target_improvement(model, baseline, spec)
    p_value = paired_target_pvalue(rows, model_prediction, baseline_prediction, spec)
    fold_positive = sum(bool(item["joint_positive"]) for item in folds)
    conditions = rules[0]["conditions"] if rules else []
    uses_rates = any(
        str(condition.get("feature_id") or "").startswith("rates.")
        for condition in conditions
    )
    return {
        "candidate_id": "g1s-universal-" + _sha({
            "target": spec.target_id, "template": template_id})[:24],
        "template_id": template_id,
        "target_id": spec.target_id,
        "target_family": spec.family,
        "target_kind": spec.kind,
        "conditions": conditions,
        "fold_rules": rules,
        "model": model,
        "baseline": baseline,
        "improvement": improvement,
        "primary_improvement": min(float(improvement[name]) for name in spec.primary_metrics),
        "p_value": float(p_value),
        "fold_positive": fold_positive,
        "fold_evaluated": len(folds),
        "folds": folds,
        "raw_n": len(rows),
        "effective_n": _effective_n(rows),
        "uses_slow_daily_rates": uses_rates,
        "rates_dependency_correction_complete": not uses_rates,
        "rates_dependency_note": (
            "daily Treasury state is shared across many intraday T0 rows; PASS 5 permits "
            "discovery diagnostics but withholds DISCOVERY_SIGNAL until a dedicated "
            "Treasury-day dependency correction is implemented"
            if uses_rates else None
        ),
        "production_authority": False,
        "prospective_confirmation": False,
        "discovery_only": True,
    }


def run_universal_structured_discovery(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
    rates_states: Iterable[RatesState] = (),
) -> dict[str, Any]:
    """Run nested, purged, target-aware discovery without production promotion."""
    rates_states = tuple(rates_states)
    horizon_reports: list[dict[str, Any]] = []
    total_tested = total_sample = total_fdr = 0
    all_discovery_signals: list[dict[str, Any]] = []

    barrier_ids: set[str] = set()
    sample_rows = build_universal_discovery_rows(sources, 15, rates_states=rates_states)
    for row in sample_rows[:100]:
        barrier_ids.update((row.get("universal_outcome") or {}).get("barriers", {}).keys())
    specs = universal_target_specs(barrier_ids)
    definitions = universal_feature_definitions()

    for horizon in (15, 30, 60, 120, 240):
        raw_rows = sample_rows if horizon == 15 else build_universal_discovery_rows(
            sources, horizon, rates_states=rates_states)
        eligible_features = sorted({
            str(feature_id)
            for row in raw_rows
            for feature_id, value in (row.get("ede_features") or {}).items()
            if value is not None
        })
        templates = universal_candidate_templates(
            eligible_feature_ids=eligible_features, feature_definitions=definitions)
        target_reports: list[dict[str, Any]] = []
        for spec in specs:
            rows = eligible_target_rows(raw_rows, spec)
            folds = _historical_folds(rows, horizon)
            occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
            inner_summaries = []
            for fold in folds:
                inner = _inner_discovery(
                    fold["train"], horizon=horizon, spec=spec, templates=templates)
                inner_summaries.append({key: value for key, value in inner.items()
                                        if key != "selected"})
                total_tested += int(inner["tested"])
                total_sample += int(inner["sample_gate_passed"])
                total_fdr += int(inner["fdr_passed"])
                by_template = {item.template_id: item for item in templates}
                for selected in inner["selected"]:
                    template = by_template.get(str(selected["template_id"]))
                    if template is None:
                        continue
                    evaluation = _evaluate_rule(template, fold["train"], fold["test"], spec)
                    if evaluation is None or len(evaluation["rows"]) < MIN_OUTER_TEST_RAW:
                        continue
                    occurrences[template.template_id].append({
                        "fold_index": fold["fold_index"],
                        "test_start_ts": fold["test_start_ts"],
                        "test_end_ts": fold["test_end_ts"],
                        "purge_embargo_valid": (
                            fold["train_target_max_ts"] < fold["purge_boundary_ts"]),
                        "evaluation": evaluation,
                    })
            candidates = [
                _aggregate_candidate(template_id, values, spec)
                for template_id, values in sorted(occurrences.items()) if values
            ]
            q_values = benjamini_hochberg([float(item["p_value"]) for item in candidates])
            for item, q_value in zip(candidates, q_values):
                item["q_value"] = float(q_value)
                statistically_qualified = (
                    float(q_value) <= MAX_Q_VALUE
                    and float(item["primary_improvement"]) >= MIN_RELATIVE_IMPROVEMENT
                    and int(item["fold_positive"]) >= MIN_STABLE_FOLDS
                )
                if statistically_qualified and _candidate_uses_rates(item):
                    item["status"] = "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING"
                elif statistically_qualified:
                    item["status"] = "DISCOVERY_SIGNAL"
                    all_discovery_signals.append(item)
                else:
                    item["status"] = "RESEARCH_DIAGNOSTIC"
            status_rank = {
                "DISCOVERY_SIGNAL": 0,
                "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING": 1,
                "RESEARCH_DIAGNOSTIC": 2,
            }
            candidates.sort(key=lambda item: (
                status_rank.get(str(item["status"]), 9),
                -float(item["primary_improvement"]), float(item["q_value"]),
                str(item["candidate_id"])))
            target_reports.append({
                "target_id": spec.target_id,
                "target_family": spec.family,
                "target_kind": spec.kind,
                "raw_rows": len(rows),
                "effective_n": _effective_n(rows) if rows else 0,
                "fold_count": len(folds),
                "templates": len(templates),
                "inner": inner_summaries,
                "candidate_count": len(candidates),
                "discovery_signal_count": sum(
                    item["status"] == "DISCOVERY_SIGNAL" for item in candidates),
                "rates_dependency_pending_count": sum(
                    item["status"] == "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING"
                    for item in candidates),
                "candidates": candidates[:20],
            })
        horizon_reports.append({
            "horizon_minutes": horizon,
            "raw_rows": len(raw_rows),
            "eligible_feature_count": len(eligible_features),
            "template_count": len(templates),
            "targets": target_reports,
        })

    return {
        "contract_version": UNIVERSAL_STRUCTURED_DISCOVERY_VERSION,
        "source_set_sha256": str(source_set_sha256),
        "strategy_agnostic": True,
        "discovery_only": True,
        "prospective_confirmation": False,
        "legacy_directional_ede_changed": False,
        "production_authority": False,
        "auto_promotion": False,
        "gates": {
            "inner_fdr_q_max": MAX_Q_VALUE,
            "minimum_relative_improvement": MIN_RELATIVE_IMPROVEMENT,
            "minimum_positive_outer_folds": MIN_STABLE_FOLDS,
            "rates_daily_dependency_signal_gate": "WITHHELD_UNTIL_CLUSTER_CORRECTION",
            "promotion_effect": "NONE_DISCOVERY_ONLY",
        },
        "hypotheses_tested_inner": total_tested,
        "sample_gate_passed_inner": total_sample,
        "fdr_passed_inner": total_fdr,
        "discovery_signal_count": len(all_discovery_signals),
        "horizons": horizon_reports,
        "verdict": (
            "DISCOVERY_SIGNALS_FOUND_NEED_FROZEN_PROSPECTIVE_CONFIRMATION"
            if all_discovery_signals else "NO_UNIVERSAL_DISCOVERY_SIGNAL_ON_CURRENT_EVIDENCE"
        ),
    }
