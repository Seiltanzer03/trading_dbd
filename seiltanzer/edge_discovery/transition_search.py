"""Bounded EDE v1.3.4 regime-transition research on already frozen T0 state.

The V3 collector already stores macro phase-space and wavelet transition metrics.
This module exposes those existing immutable values to a separate, bounded EDE
sub-audit. It does not create a second collector, reconstruct history, change
EDGE_MATURITY of the canonical search, or grant shadow/production authority.

The canonical `regime.wavelet_phase` ID was aligned in PR #100 to the already
materialized numeric `phase_stability`. This sub-audit reuses that canonical ID
instead of duplicating the same value under a second feature name.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any, Iterable

from .discovery import discover_horizon
from .filters import CandidateTemplate, ConditionTemplate, FittedCondition, FittedRule, condition_matches

TRANSITION_CONTRACT_VERSION = "g1s-ede-regime-transition-v1.3.4"
TRANSITION_HORIZONS = (15, 30, 60)
MAX_TRANSITION_TEMPLATES = 120
MAX_TRANSITION_CONDITIONS = 2

# These are not newly invented market values. Every path below already exists
# inside g1s_evidence_v3 frozen at T0. Canonical regime.wavelet_phase is kept in
# the existing registry and is therefore intentionally absent from this map.
TRANSITION_FEATURES: dict[str, dict[str, Any]] = {
    "regime.macro_boundary_distance": {
        "block": "macro", "path": ("boundary_distance",), "datatype": "float",
        "dependency": "macro_transition",
    },
    "regime.macro_transition_velocity": {
        "block": "macro", "path": ("transition_velocity",), "datatype": "float",
        "dependency": "macro_transition",
    },
    "regime.macro_transition_acceleration": {
        "block": "macro", "path": ("transition_acceleration",), "datatype": "float",
        "dependency": "macro_transition",
    },
    "regime.macro_trend_score": {
        "block": "macro", "path": ("x",), "datatype": "float",
        "dependency": "macro_transition",
    },
    "regime.macro_vol_score": {
        "block": "macro", "path": ("y",), "datatype": "float",
        "dependency": "macro_transition",
    },
    "regime.macro_stress_score": {
        "block": "macro", "path": ("z",), "datatype": "float",
        "dependency": "macro_transition",
    },
    "regime.wavelet_spectral_concentration": {
        "block": "wavelet", "path": ("spectral_concentration",), "datatype": "float",
        "dependency": "wavelet_transition",
    },
    "regime.wavelet_persistence": {
        "block": "wavelet", "path": ("persistence",), "datatype": "float",
        "dependency": "wavelet_transition",
    },
    "regime.wavelet_ridge_velocity": {
        "block": "wavelet", "path": ("ridge_velocity_log_per_hour",), "datatype": "float",
        "dependency": "wavelet_transition",
    },
    "regime.wavelet_power_slope": {
        "block": "wavelet", "path": ("ridge_power_slope_log_per_hour",), "datatype": "float",
        "dependency": "wavelet_transition",
    },
    "regime.wavelet_dominant_period_hours": {
        "block": "wavelet", "path": ("dominant_period_hours",), "datatype": "float",
        "dependency": "wavelet_transition",
    },
    "regime.wavelet_energy_transfer_rate": {
        "block": "wavelet", "path": ("energy_transfer", "rate_pp_per_30m"), "datatype": "float",
        "dependency": "wavelet_transition",
    },
    "regime.wavelet_cycle_shift": {
        "block": "wavelet", "path": ("cycle_shift",), "datatype": "category",
        "dependency": "wavelet_transition",
    },
}

QUINTILE_FEATURES = {
    "regime.macro_boundary_distance",
    "regime.macro_transition_velocity",
    "regime.macro_transition_acceleration",
    "regime.wavelet_spectral_concentration",
    "regime.wavelet_ridge_velocity",
}
QUINTILE_STATES = ("Q0_20", "Q20_40", "Q40_60", "Q60_80", "Q80_100")
CYCLE_SHIFT_STATES = ("STABLE", "BIFURCATED", "LONG → SHORT", "SHORT → LONG", "DIFFUSE")
CROSS_STATES = ("SAME", "OPPOSITE")
TREND_STATES = ("TREND_UP", "TREND_DOWN", "CHOP")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nested(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _quality_ok(block: dict[str, Any], captured_ts: float) -> tuple[bool, dict[str, Any]]:
    quality = block.get("quality") if isinstance(block, dict) else None
    quality = quality if isinstance(quality, dict) else {}
    source_ts = _finite(quality.get("source_ts"))
    available = bool(block.get("available")) and bool(quality.get("available", True))
    stale = bool(quality.get("stale", False))
    future = bool(source_ts is not None and source_ts > captured_ts + 1e-6)
    return available and not stale and not future, {
        "available": available,
        "stale": stale,
        "source_ts": source_ts,
        "source_age_minutes": quality.get("source_age_minutes"),
        "source_quality": quality.get("source_quality"),
        "future_points_used": future,
    }


def augment_rows_from_frozen_v3(runtime: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach transition metrics from each row's original immutable T0 JSON."""
    observation_ids = {str(row["observation_id"]) for row in rows}
    if not observation_ids:
        return _coverage_report(rows)
    with runtime._lock:
        source_rows = runtime._conn.execute(
            "SELECT observation_id,captured_ts,frozen_features_json FROM g1s_observations "
            "WHERE horizon_minutes IN (15,30,60) ORDER BY observation_id"
        ).fetchall()
    frozen_by_id = {
        str(source["observation_id"]): (float(source["captured_ts"]), _loads(source["frozen_features_json"]))
        for source in source_rows if str(source["observation_id"]) in observation_ids
    }
    for row in rows:
        observation_id = str(row["observation_id"])
        source = frozen_by_id.get(observation_id)
        if source is None:
            continue
        source_t0, frozen = source
        t0 = float(row["captured_ts"])
        if abs(source_t0 - t0) > 1e-6:
            continue
        v3 = frozen.get("g1s_evidence_v3") if isinstance(frozen, dict) else None
        v3 = v3 if isinstance(v3, dict) else {}
        for feature_id, definition in TRANSITION_FEATURES.items():
            block = v3.get(str(definition["block"]))
            block = block if isinstance(block, dict) else {}
            valid, meta = _quality_ok(block, t0)
            value = _nested(block, tuple(definition["path"])) if valid else None
            if definition["datatype"] == "float":
                value = _finite(value)
            elif value is not None:
                value = str(value)
            if value is None:
                continue
            row.setdefault("ede_features", {})[feature_id] = value
            row.setdefault("feature_values", {})[feature_id] = {
                "feature_id": feature_id,
                "value": value,
                "t0": t0,
                "asof": meta.get("source_ts"),
                "available": True,
                "stale": False,
                "training_eligible": True,
                "dependency_group": definition["dependency"],
                "provenance": "FROZEN_T0_V3_EXISTING_FIELD",
                "future_points_used": False,
                "source_quality": meta.get("source_quality"),
            }
    return _coverage_report(rows)


def _coverage_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[str, dict[str, int]] = {
        str(h): {feature_id: 0 for feature_id in TRANSITION_FEATURES}
        for h in TRANSITION_HORIZONS
    }
    for row in rows:
        h = str(int(row["horizon_minutes"]))
        if h not in by_horizon:
            continue
        ede = row.get("ede_features") or {}
        for feature_id in TRANSITION_FEATURES:
            if ede.get(feature_id) is not None:
                by_horizon[h][feature_id] += 1
    return {
        "contract_version": TRANSITION_CONTRACT_VERSION,
        "feature_count": len(TRANSITION_FEATURES),
        "features": TRANSITION_FEATURES,
        "coverage_by_horizon": by_horizon,
        "existing_collector_fields_only": True,
        "synthetic_history_used": False,
        "retrospective_reconstruction_used": False,
        "canonical_wavelet_phase_reused": "regime.wavelet_phase",
    }


def _relative(feature_id: str) -> tuple[ConditionTemplate, ...]:
    return (
        ConditionTemplate(feature_id, "train_relative", "ABOVE_MEDIAN"),
        ConditionTemplate(feature_id, "train_relative", "BELOW_MEDIAN"),
    )


def _numeric_templates(feature_id: str) -> list[CandidateTemplate]:
    if feature_id in QUINTILE_FEATURES:
        return [CandidateTemplate((ConditionTemplate(feature_id, "train_quantile", state),))
                for state in QUINTILE_STATES]
    return [CandidateTemplate((condition,)) for condition in _relative(feature_id)]


def transition_templates(available_features: Iterable[str]) -> tuple[CandidateTemplate, ...]:
    available = set(available_features)
    singles: list[CandidateTemplate] = []
    for feature_id, definition in TRANSITION_FEATURES.items():
        if feature_id not in available:
            continue
        if definition["datatype"] == "category":
            singles.extend(CandidateTemplate((ConditionTemplate(
                feature_id, "categorical", state),)) for state in CYCLE_SHIFT_STATES)
        else:
            singles.extend(_numeric_templates(feature_id))

    pairs: list[CandidateTemplate] = []
    numeric_pairs = (
        ("regime.macro_boundary_distance", "regime.macro_transition_velocity"),
        ("regime.macro_transition_velocity", "regime.macro_transition_acceleration"),
        ("regime.macro_boundary_distance", "regime.wavelet_phase"),
        ("regime.macro_transition_velocity", "regime.wavelet_ridge_velocity"),
        ("regime.macro_stress_score", "regime.wavelet_spectral_concentration"),
        ("regime.wavelet_phase", "regime.wavelet_ridge_velocity"),
        ("regime.wavelet_phase", "regime.wavelet_spectral_concentration"),
        ("regime.wavelet_ridge_velocity", "regime.wavelet_power_slope"),
    )
    for left_id, right_id in numeric_pairs:
        if {left_id, right_id} <= available:
            for left in _relative(left_id):
                for right in _relative(right_id):
                    pairs.append(CandidateTemplate((left, right)))

    external_pairs = (
        ("regime.macro_transition_velocity", "option_dynamics.gex_velocity", "numeric"),
        ("regime.macro_boundary_distance", "option.iv_rv_ratio", "numeric"),
        ("regime.macro_stress_score", "option.iv_rv_ratio", "numeric"),
        ("regime.wavelet_phase", "option_dynamics.iv_velocity", "numeric"),
        ("regime.wavelet_ridge_velocity", "option_dynamics.gex_velocity", "numeric"),
        ("regime.macro_transition_velocity", "cross.confirmation", "cross"),
        ("regime.wavelet_cycle_shift", "cross.confirmation", "cycle_cross"),
        ("regime.macro_boundary_distance", "regime.trend", "trend"),
    )
    for left_id, right_id, kind in external_pairs:
        if {left_id, right_id} - available:
            continue
        if kind == "numeric":
            left_conditions = _relative(left_id)
            right_conditions = _relative(right_id)
        elif kind == "cross":
            left_conditions = _relative(left_id)
            right_conditions = tuple(ConditionTemplate(right_id, "categorical", state) for state in CROSS_STATES)
        elif kind == "trend":
            left_conditions = _relative(left_id)
            right_conditions = tuple(ConditionTemplate(right_id, "categorical", state) for state in TREND_STATES)
        else:
            left_conditions = tuple(ConditionTemplate(left_id, "categorical", state) for state in CYCLE_SHIFT_STATES)
            right_conditions = tuple(ConditionTemplate(right_id, "categorical", state) for state in CROSS_STATES)
        for left in left_conditions:
            for right in right_conditions:
                pairs.append(CandidateTemplate((left, right)))

    unique = {item.template_id: item for item in singles + pairs}
    selected = [unique[key] for key in sorted(unique)]
    if len(selected) > MAX_TRANSITION_TEMPLATES:
        raise RuntimeError(
            f"transition search budget exceeded: {len(selected)} > {MAX_TRANSITION_TEMPLATES}"
        )
    if any(item.complexity > MAX_TRANSITION_CONDITIONS for item in selected):
        raise RuntimeError("transition search depth exceeded")
    return tuple(selected)


def _deserialize_rule(payload: dict[str, Any]) -> FittedRule | None:
    try:
        return FittedRule(str(payload["template_id"]), tuple(FittedCondition(
            feature_id=str(item["feature_id"]), kind=str(item["kind"]),
            state=str(item["state"]), lower=item.get("lower"), upper=item.get("upper"),
            train_cutoff_ts=item.get("train_cutoff_ts"),
        ) for item in payload.get("conditions") or []))
    except (KeyError, TypeError, ValueError):
        return None


def _selected_rows(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold, rule_payload in zip(candidate.get("folds") or [], candidate.get("thresholds") or []):
        if fold.get("inner_selection_source") != "PRIMARY_FDR_PASS":
            continue
        rule = _deserialize_rule(rule_payload)
        if rule is None:
            continue
        start = float(fold.get("outer_test_start_ts") or -math.inf)
        end = float(fold.get("outer_test_end_ts") or math.inf)
        for row in rows:
            t0 = float(row["captured_ts"])
            if not (start - 1e-6 <= t0 <= end + 1e-6):
                continue
            if all(condition_matches(row, condition) for condition in rule.conditions):
                output[str(row["observation_id"])] = row
    return sorted(output.values(), key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))


def _candidate_summary(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _selected_rows(candidate, rows)
    counts = Counter(str(row["instrument"]) for row in selected)
    total = len(selected)
    comparison = candidate.get("global_ret5_comparison") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "horizon_minutes": candidate.get("horizon_minutes"),
        "template": candidate.get("template"),
        "complexity": candidate.get("complexity"),
        "raw_n": candidate.get("raw_n"),
        "effective_n": candidate.get("effective_n"),
        "coverage": candidate.get("coverage"),
        "delta_brier": comparison.get("brier_delta"),
        "delta_logloss": comparison.get("logloss_delta"),
        "q_value": candidate.get("q_value"),
        "folds_positive": candidate.get("folds_positive"),
        "folds_evaluated": candidate.get("folds_evaluated"),
        "edge_maturity": candidate.get("edge_maturity"),
        "asset_counts": dict(sorted(counts.items())),
        "asset_concentration": (max(counts.values(), default=0) / max(1, total)),
        "primary_outer_rows_reconstructed": total,
        "shadow_eligible": False,
        "production_authority": False,
    }


def run_transition_search(rows: list[dict[str, Any]], *, source_set_sha256: str) -> dict[str, Any]:
    available = {
        feature_id for feature_id in TRANSITION_FEATURES
        if any((row.get("ede_features") or {}).get(feature_id) is not None for row in rows)
    }
    # Existing canonical fields may participate only in explicitly predeclared interactions.
    for feature_id in (
        "regime.wavelet_phase", "option_dynamics.gex_velocity",
        "option_dynamics.iv_velocity", "option.iv_rv_ratio",
        "cross.confirmation", "regime.trend",
    ):
        if any((row.get("ede_features") or {}).get(feature_id) is not None for row in rows):
            available.add(feature_id)
    templates = transition_templates(available)
    by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        h = int(row["horizon_minutes"])
        if h in TRANSITION_HORIZONS and row.get("outcome_available"):
            by_horizon[h].append(row)

    horizon_reports: list[dict[str, Any]] = []
    compact: list[dict[str, Any]] = []
    maturity = Counter()
    hypotheses = sample_passed = fdr_passed = stable = 0
    for horizon in TRANSITION_HORIZONS:
        horizon_rows = sorted(by_horizon[horizon], key=lambda row: (
            float(row["captured_ts"]), str(row["instrument"])))
        result = discover_horizon([], horizon, templates, rows_override=horizon_rows)
        summaries = [_candidate_summary(candidate, horizon_rows) for candidate in result.get("candidates") or []]
        summaries.sort(key=lambda item: (
            -(float(item.get("delta_brier") or -1e9) + float(item.get("delta_logloss") or -1e9)),
            str(item.get("candidate_id"))))
        horizon_reports.append({**result, "transition_candidate_summaries": summaries})
        compact.extend(summaries)
        hypotheses += int(result.get("hypotheses_tested") or 0)
        sample_passed += int(result.get("inner_sample_gate_passed") or 0)
        fdr_passed += int(result.get("inner_fdr_passed") or 0)
        stable += int(result.get("stability_gate_passed") or 0)
        for candidate in result.get("candidates") or []:
            maturity[str(candidate.get("edge_maturity") or "INSUFFICIENT_DATA")] += 1

    compact.sort(key=lambda item: (
        -(float(item.get("delta_brier") or -1e9) + float(item.get("delta_logloss") or -1e9)),
        str(item.get("candidate_id"))))
    primary = [item for item in compact if item.get("edge_maturity") not in (None, "INSUFFICIENT_DATA")]
    verdict = (
        "TRANSITION_RESEARCH_SIGNAL_FOUND_REQUIRES_CANONICAL_PROMOTION_REVIEW"
        if primary else
        "NO_VALIDATED_REGIME_TRANSITION_EDGE_YET"
        if sample_passed else
        "INSUFFICIENT_REGIME_TRANSITION_DATA"
    )
    return {
        "contract_version": TRANSITION_CONTRACT_VERSION,
        "source_set_sha256": source_set_sha256,
        "primary_baseline": "GLOBAL_RET5_PERSISTENCE",
        "horizons": horizon_reports,
        "search_budget": {
            "transition_feature_count": len(TRANSITION_FEATURES),
            "available_feature_count_including_predeclared_interaction_fields": len(available),
            "candidate_templates": len(templates),
            "max_templates": MAX_TRANSITION_TEMPLATES,
            "max_conditions": MAX_TRANSITION_CONDITIONS,
            "expansion_requires_explicit_review": True,
        },
        "hypotheses_tested": hypotheses,
        "sample_gate_passed": sample_passed,
        "fdr_passed": fdr_passed,
        "stability_gate_passed": stable,
        "edge_maturity_counts": dict(sorted(maturity.items())),
        "top_20": compact[:20],
        "validated_or_research_signal_candidates": primary[:20],
        "verdict": verdict,
        "shadow_activation": "DISABLED_PENDING_CANONICAL_FEATURE_REGISTRY_REVIEW",
        "diagnostic_only_until_review": True,
        "production_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }
