"""EDE v1.3 selective-edge search on immutable prospective T0 rows.

Reuses the existing nested walk-forward engine. This module changes only the
predeclared research question set and research-only ranking/interpretation; it
never grants production authority.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable

from seiltanzer.config import INSTRUMENTS
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import ASSET_FAMILIES, SESSIONS

from .ablation import family_ablation
from .discovery import discover_horizon
from .filters import (
    CandidateTemplate,
    ConditionTemplate,
    FittedCondition,
    FittedRule,
    MACRO_REGIMES,
    condition_matches,
)
from .registry import FEATURES

SELECTIVE_CONTRACT_VERSION = "g1s-ede-selective-edge-v1.3"
SELECTIVE_HORIZONS = (15, 30, 60)
MAX_SELECTIVE_TEMPLATES = 320
MAX_CONDITIONS = 3
LOW_PRACTICAL_COVERAGE_PCT = 2.0
MAX_ASSET_CONCENTRATION = 0.85

_FAMILY = {item.feature_id: item.family for item in FEATURES}
_DEPENDENCY = {item.feature_id: item.dependency_family for item in FEATURES}

QUINTILE_PRIORITY = (
    "price.trend_efficiency_60",
    "vol.rv15_over_rv60",
    "option.iv",
    "option.iv_rv_ratio",
    "option.skew",
    "option.gex_net_balance",
    "option.zero_gamma_distance",
    "option_dynamics.iv_velocity",
    "option_dynamics.iv_acceleration",
    "option_dynamics.skew_velocity",
    "option_dynamics.skew_acceleration",
    "option_dynamics.gex_velocity",
    "option_dynamics.gex_acceleration",
    "option_dynamics.vanna_velocity",
    "option_dynamics.vanna_acceleration",
    "option_dynamics.charm_velocity",
    "option_dynamics.charm_acceleration",
    "option_dynamics.zero_gamma_velocity",
    "option_dynamics.zero_gamma_acceleration",
    "cross.correlation_change",
)
QUANTILE_STATES = ("Q0_20", "Q20_40", "Q40_60", "Q60_80", "Q80_100")
TREND_STATES = ("TREND_UP", "TREND_DOWN", "CHOP")
VOL_STATES = ("EXPANDING", "CONTRACTING", "NORMAL")
CROSS_STATES = ("SAME", "OPPOSITE")
CATEGORICAL_STATES: dict[str, tuple[str, ...]] = {
    "regime.asset": tuple(INSTRUMENTS),
    "regime.asset_family": tuple(ASSET_FAMILIES),
    "regime.session_utc": tuple(SESSIONS),
    "regime.trend": TREND_STATES,
    "regime.volatility": VOL_STATES,
    "regime.macro": tuple(MACRO_REGIMES),
    "cross.confirmation": CROSS_STATES,
}


def _single_numeric(feature_id: str, *, quintiles: bool) -> list[CandidateTemplate]:
    states = QUANTILE_STATES if quintiles else ("ABOVE_MEDIAN", "BELOW_MEDIAN")
    kind = "train_quantile" if quintiles else "train_relative"
    return [
        CandidateTemplate((ConditionTemplate(feature_id, kind, state),))
        for state in states
    ]


def _categorical(feature_id: str, states: Iterable[str]) -> list[ConditionTemplate]:
    return [ConditionTemplate(feature_id, "categorical", state) for state in states]


def _relative(feature_id: str) -> list[ConditionTemplate]:
    return [
        ConditionTemplate(feature_id, "train_relative", "ABOVE_MEDIAN"),
        ConditionTemplate(feature_id, "train_relative", "BELOW_MEDIAN"),
    ]


def selective_templates(
    eligible_feature_ids: Iterable[str],
) -> tuple[CandidateTemplate, ...]:
    """Return a bounded, predeclared 1-3 condition selective search space."""
    eligible = set(eligible_feature_ids)
    singles: list[CandidateTemplate] = []
    for definition in FEATURES:
        feature_id = definition.feature_id
        if (
            feature_id not in eligible
            or definition.research_scope != "G1S"
            or not definition.training_eligibility
            or feature_id == "price.ret_5m"
        ):
            continue
        if definition.datatype == "category":
            states = CATEGORICAL_STATES.get(feature_id)
            if states:
                singles.extend(
                    CandidateTemplate((condition,))
                    for condition in _categorical(feature_id, states)
                )
            continue
        singles.extend(
            _single_numeric(feature_id, quintiles=feature_id in QUINTILE_PRIORITY)
        )

    interactions: list[CandidateTemplate] = []
    pairs = (
        ("option.iv", "option_dynamics.iv_velocity"),
        ("option.iv", "option_dynamics.iv_acceleration"),
        ("option.skew", "option_dynamics.skew_velocity"),
        ("option.skew", "option_dynamics.skew_acceleration"),
        ("option.gex_net_balance", "option_dynamics.gex_velocity"),
        ("option.gex_net_balance", "option_dynamics.gex_acceleration"),
        ("option.vanna", "option_dynamics.vanna_velocity"),
        ("option.vanna", "option_dynamics.vanna_acceleration"),
        ("option.charm", "option_dynamics.charm_velocity"),
        ("option.charm", "option_dynamics.charm_acceleration"),
        ("option.zero_gamma_distance", "option_dynamics.zero_gamma_velocity"),
        ("option.zero_gamma_distance", "option_dynamics.zero_gamma_acceleration"),
    )
    for static_id, dynamic_id in pairs:
        if {static_id, dynamic_id} <= eligible:
            for left in _relative(static_id):
                for right in _relative(dynamic_id):
                    interactions.append(CandidateTemplate((left, right)))

    cross = _categorical("cross.confirmation", CROSS_STATES)
    cross_drivers = (
        "option.iv_rv_ratio",
        "option.skew",
        "option_dynamics.iv_velocity",
        "option_dynamics.gex_velocity",
        "option_dynamics.skew_velocity",
        "vol.rv15_over_rv60",
    )
    if "cross.confirmation" in eligible:
        for feature_id in cross_drivers:
            if feature_id not in eligible:
                continue
            for driver in _relative(feature_id):
                for cross_state in cross:
                    interactions.append(CandidateTemplate((driver, cross_state)))

    regime_drivers = (
        "option.iv_rv_ratio",
        "option.gex_net_balance",
        "option.skew",
        "option_dynamics.iv_velocity",
        "option_dynamics.gex_velocity",
        "option_dynamics.vanna_velocity",
        "option_dynamics.charm_velocity",
        "vol.rv15_over_rv60",
    )
    if "regime.trend" in eligible:
        trend = _categorical("regime.trend", TREND_STATES)
        for feature_id in regime_drivers:
            if feature_id not in eligible:
                continue
            for driver in _relative(feature_id):
                for regime in trend:
                    interactions.append(CandidateTemplate((driver, regime)))
    if "regime.volatility" in eligible:
        vol = _categorical("regime.volatility", VOL_STATES)
        for feature_id in regime_drivers[:6]:
            if feature_id not in eligible:
                continue
            for driver in _relative(feature_id):
                for regime in vol:
                    interactions.append(CandidateTemplate((driver, regime)))

    triple_drivers = (
        "option_dynamics.iv_velocity",
        "option_dynamics.gex_velocity",
        "option_dynamics.skew_velocity",
        "option_dynamics.vanna_velocity",
    )
    if {"cross.confirmation", "regime.trend"} <= eligible:
        trend = _categorical("regime.trend", TREND_STATES)
        for feature_id in triple_drivers:
            if feature_id not in eligible:
                continue
            for driver in _relative(feature_id):
                for cross_state in cross:
                    for regime in trend:
                        interactions.append(
                            CandidateTemplate((driver, cross_state, regime))
                        )

    unique: dict[str, CandidateTemplate] = {}
    for item in singles + interactions:
        if item.complexity > MAX_CONDITIONS:
            raise RuntimeError("selective search depth exceeded")
        unique[item.template_id] = item
    single_ids = {item.template_id for item in singles}
    selected = [unique[key] for key in sorted(single_ids)]
    if len(selected) > MAX_SELECTIVE_TEMPLATES:
        raise RuntimeError("single-feature map exceeds selective search budget")
    for key in sorted(unique):
        if key in single_ids:
            continue
        if len(selected) >= MAX_SELECTIVE_TEMPLATES:
            break
        selected.append(unique[key])
    return tuple(selected)


def _deserialize_rule(payload: dict[str, Any]) -> FittedRule | None:
    try:
        return FittedRule(
            str(payload["template_id"]),
            tuple(
                FittedCondition(
                    feature_id=str(item["feature_id"]),
                    kind=str(item["kind"]),
                    state=str(item["state"]),
                    lower=item.get("lower"),
                    upper=item.get("upper"),
                    train_cutoff_ts=item.get("train_cutoff_ts"),
                )
                for item in payload.get("conditions") or []
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _candidate_rows(candidate: dict[str, Any], horizon_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct primary outer-test rows using each fold's own fitted rule."""
    selected: dict[str, dict[str, Any]] = {}
    rules = candidate.get("thresholds") or []
    folds = candidate.get("folds") or []
    for fold, rule_payload in zip(folds, rules):
        if fold.get("inner_selection_source") != "PRIMARY_FDR_PASS":
            continue
        rule = _deserialize_rule(rule_payload)
        if rule is None:
            continue
        start = float(fold.get("outer_test_start_ts") or -math.inf)
        end = float(fold.get("outer_test_end_ts") or math.inf)
        for row in horizon_rows:
            ts = float(row["captured_ts"])
            if ts < start - 1e-6 or ts > end + 1e-6:
                continue
            if all(condition_matches(row, condition) for condition in rule.conditions):
                key = str(row.get("observation_id") or f"{row['instrument']}:{ts}")
                selected[key] = row
    return sorted(
        selected.values(),
        key=lambda row: (float(row["captured_ts"]), str(row["instrument"])),
    )


def _median_gap_seconds(rows: list[dict[str, Any]]) -> float | None:
    times = sorted({float(row["captured_ts"]) for row in rows})
    if len(times) < 2:
        return None
    return float(statistics.median(b - a for a, b in zip(times, times[1:])))


def _economic(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def values(name: str) -> list[float]:
        return [
            float(row[name])
            for row in rows
            if row.get(name) is not None and math.isfinite(float(row[name]))
        ]
    ret = values("terminal_log_return")
    mfe = values("mfe_log_return")
    mae = values("mae_log_return")
    return {
        "future_return_mean": statistics.fmean(ret) if ret else None,
        "future_return_median": statistics.median(ret) if ret else None,
        "mfe_mean": statistics.fmean(mfe) if mfe else None,
        "mae_mean": statistics.fmean(mae) if mae else None,
        "path_asymmetry": (
            statistics.fmean(mfe) + statistics.fmean(mae) if mfe and mae else None
        ),
        "economic_sample_n": len(ret),
    }


def _temporal(candidate: dict[str,Any]) -> dict[str, Any]:
    fold_rows = candidate.get("folds") or []
    deltas = [
        (
            float((fold.get("global_ret5_comparison") or {}).get("brier_delta") or 0.0),
            float((fold.get("global_ret5_comparison") or {}).get("logloss_delta") or 0.0),
        )
        for fold in fold_rows
    ]
    if not deltas:
        return {
            "folds_positive": 0,
            "folds_evaluated": 0,
            "median_delta_brier": None,
            "worst_delta_brier": None,
            "median_delta_logloss": None,
            "worst_delta_logloss": None,
            "edge_decay": None,
        }
    brier = [item[0] for item in deltas]
    logloss = [item[1] for item in deltas]
    midpoint = max(1, len(deltas) // 2)
    early = deltas[:midpoint]
    late = deltas[midpoint:] or deltas[-1:]
    return {
        "folds_positive": int(candidate.get("folds_positive") or 0),
        "folds_evaluated": int(candidate.get("folds_evaluated") or len(deltas)),
        "median_delta_brier": float(statistics.median(brier)),
        "worst_delta_brier": float(min(brier)),
        "median_delta_logloss": float(statistics.median(logloss)),
        "worst_delta_logloss": float(min(logloss)),
        "edge_decay": {
            "brier": float(
                statistics.fmean(item[0] for item in late)
                - statistics.fmean(item[0] for item in early)
            ),
            "logloss": float(
                statistics.fmean(item[1] for item in late)
                - statistics.fmean(item[1] for item in early)
            ),
        },
    }


def _threshold_instability(candidate: dict[str, Any]) -> float:
    by_feature: dict[str, list[float]] = defaultdict(list)
    for rule in candidate.get("thresholds") or []:
        for condition in rule.get("conditions") or []:
            value = condition.get("lower")
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                by_feature[str(condition["feature_id"])].append(numeric)
    penalties = []
    for values in by_feature.values():
        if len(values) < 2:
            continue
        scale = max(abs(statistics.median(values)), 1e-9)
        penalties.append(min(1.0, (max(values) - min(values)) / scale))
    return float(statistics.fmean(penalties)) if penalties else 0.0


def _baseline_failure(candidate: dict[str, Any]) -> bool:
    baseline = candidate.get("global_ret5") or {}
    neutral = (candidate.get("sanity_baselines") or {}).get("constant_0_5") or {}
    try:
        return (
            float(baseline["brier"]) > float(neutral["brier"])
            and float(baseline["logloss"]) > float(neutral["logloss"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _enrich_candidate(candidate: dict[str, Any], horizon_rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected_rows = _candidate_rows(candidate, horizon_rows)
    asset_counts = Counter(str(row["instrument"]) for row in selected_rows)
    total = len(selected_rows)
    concentration = max(asset_counts.values(), default=0) / max(1, total)
    families = sorted({
        _FAMILY.get(str(condition.get("feature_id")), "UNKNOWN")
        for condition in candidate.get("template") or []
    } - {"UNKNOWN"})
    dependencies = [
        _DEPENDENCY.get(str(condition.get("feature_id")), "UNKNOWN")
        for condition in candidate.get("template") or []
    ]
    dependency_redundancy = max(0, len(dependencies) - len(set(dependencies)))
    coverage_pct = 100.0 * float(candidate.get("coverage") or 0.0)
    threshold_instability = _threshold_instability(candidate)
    base_score = float((candidate.get("edge_score") or {}).get("score") or 0.0)
    complexity = int(candidate.get("complexity") or 1)
    rare_penalty = 0.04 if coverage_pct < LOW_PRACTICAL_COVERAGE_PCT else 0.0
    concentration_penalty = max(0.0, concentration - 0.60) * 0.12
    dependency_penalty = 0.03 * dependency_redundancy
    threshold_penalty = min(0.04, 0.02 * threshold_instability)
    complexity_penalty = 0.015 * max(0, complexity - 1)
    research_rank = (
        base_score - rare_penalty - concentration_penalty - dependency_penalty
        - threshold_penalty - complexity_penalty
    )
    return {
        **candidate,
        "selective_contract_version": SELECTIVE_CONTRACT_VERSION,
        "feature_families": families,
        "dependency_families": sorted(set(dependencies) - {"UNKNOWN"}),
        "dependency_redundancy": dependency_redundancy,
        "practical_coverage": {
            "coverage_pct": coverage_pct,
            "signals_per_1000_observations": 1000.0 * total / max(1, len(horizon_rows)),
            "median_time_between_occurrences_sec": _median_gap_seconds(selected_rows),
            "status": (
                "LOW_PRACTICAL_COVERAGE"
                if coverage_pct < LOW_PRACTICAL_COVERAGE_PCT else "ADEQUATE"
            ),
        },
        "asset_distribution": {
            "counts": dict(sorted(asset_counts.items())),
            "instrument_count": len(asset_counts),
            "max_asset_concentration": concentration,
            "status": (
                "ASSET_SPECIFIC_RESEARCH_SIGNAL"
                if concentration > MAX_ASSET_CONCENTRATION else "BROAD_OR_MIXED"
            ),
        },
        "temporal_stability": _temporal(candidate),
        "economic_interpretation": _economic(selected_rows),
        "baseline_failure_regime": _baseline_failure(candidate),
        "research_rank": {
            "score": research_rank,
            "base_edge_score": base_score,
            "penalties": {
                "complexity": complexity_penalty,
                "rare_coverage": rare_penalty,
                "dependency_redundancy": dependency_penalty,
                "threshold_instability": threshold_penalty,
                "instrument_concentration": concentration_penalty,
            },
            "ranking_only_not_edge_claim": True,
        },
    }


def _compact_edge(item: dict[str, Any]) -> dict[str, Any]:
    comparison = item.get("global_ret5_comparison") or {}
    template = item.get("template") or []
    return {
        "candidate_id": item.get("candidate_id"),
        "hypothesis_id": item.get("hypothesis_id"),
        "horizon_minutes": item.get("horizon_minutes"),
        "feature_ids": [str(row.get("feature_id")) for row in template],
        "feature_families": item.get("feature_families") or [],
        "conditions": item.get("conditions") or [],
        "raw_n": item.get("raw_n"),
        "effective_n": item.get("effective_n"),
        "coverage_pct": (item.get("practical_coverage") or {}).get("coverage_pct"),
        "delta_brier": comparison.get("brier_delta"),
        "delta_logloss": comparison.get("logloss_delta"),
        "q_value": item.get("q_value"),
        "folds_positive": item.get("folds_positive"),
        "folds_evaluated": item.get("folds_evaluated"),
        "asset_distribution": item.get("asset_distribution"),
        "temporal_stability": item.get("temporal_stability"),
        "economic_interpretation": item.get("economic_interpretation"),
        "edge_maturity": item.get("edge_maturity"),
        "research_rank": (item.get("research_rank") or {}).get("score"),
    }


def _family_incremental_value(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for horizon in SELECTIVE_HORIZONS:
        row: dict[str, Any] = {}
        horizon_candidates = [
            item for item in candidates if int(item.get("horizon_minutes") or 0) == horizon
        ]
        for family in (
            "PRICE", "VOLATILITY", "OPTIONS", "OPTION_DYNAMICS",
            "CROSS_ASSET", "REGIME",
        ):
            eligible = [
                item for item in horizon_candidates
                if family in set(item.get("feature_families") or [])
            ]
            eligible.sort(
                key=lambda item: -float(
                    (item.get("research_rank") or {}).get("score") or -1e9
                )
            )
            row[family] = _compact_edge(eligible[0]) if eligible else None
        output[str(horizon)] = row
    return output


def _options_sections(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    static: list[dict[str, Any]] = []
    dynamics: list[dict[str, Any]] = []
    interaction: list[dict[str, Any]] = []
    regime: list[dict[str, Any]] = []
    for item in candidates:
        families = set(item.get("feature_families") or [])
        complexity = int(item.get("complexity") or 1)
        if "OPTIONS" in families and complexity == 1:
            static.append(item)
        if "OPTION_DYNAMICS" in families and complexity == 1:
            dynamics.append(item)
        if {"OPTIONS", "OPTION_DYNAMICS"} <= families:
            interaction.append(item)
        if families & {"OPTIONS", "OPTION_DYNAMICS"} and "REGIME" in families:
            regime.append(item)

    def compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = sorted(
            rows,
            key=lambda row: -float((row.get("research_rank") or {}).get("score") or -1e9),
        )[:10]
        return [_compact_edge(item) for item in rows]

    return {
        "static_options": compact(static),
        "option_derivatives": compact(dynamics),
        "static_x_derivatives": compact(interaction),
        "options_x_regime": compact(regime),
    }


def run_selective_search(
    *,
    prospective_rows: list[dict[str, Any]],
    source_set_sha256: str,
    eligible_feature_ids: Iterable[str],
) -> dict[str, Any]:
    """Run the bounded v1.3 search on 15/30/60m only."""
    eligible_feature_ids = set(eligible_feature_ids)
    templates = selective_templates(eligible_feature_ids)
    by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in prospective_rows:
        horizon = int(row["horizon_minutes"])
        if horizon in SELECTIVE_HORIZONS and row.get("outcome_available"):
            by_horizon[horizon].append(row)

    horizon_reports: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    for horizon in SELECTIVE_HORIZONS:
        rows = sorted(
            by_horizon[horizon],
            key=lambda row: (float(row["captured_ts"]), str(row["instrument"])),
        )
        report = discover_horizon([], horizon, templates, rows_override=rows)
        enriched = [_enrich_candidate(item, rows) for item in report["candidates"]]
        enriched.sort(
            key=lambda item: (
                -float((item.get("research_rank") or {}).get("score") or -1e9),
                str(item.get("candidate_id")),
            )
        )
        report = {**report, "candidates": enriched}
        horizon_reports.append(report)
        all_candidates.extend(enriched)

    all_candidates.sort(
        key=lambda item: (
            -float((item.get("research_rank") or {}).get("score") or -1e9),
            str(item.get("candidate_id")),
        )
    )
    robust = [item for item in all_candidates if item.get("edge_maturity") == "ROBUST_EDGE"]
    provisional = [
        item for item in all_candidates if item.get("edge_maturity") == "PROVISIONAL_EDGE"
    ]
    research = [
        item for item in all_candidates if item.get("edge_maturity") == "RESEARCH_SIGNAL"
    ]
    if robust:
        verdict = "ROBUST SELECTIVE EDGE FOUND"
    elif provisional:
        verdict = "PROVISIONAL SELECTIVE EDGE FOUND"
    elif research:
        verdict = "RESEARCH SIGNALS FOUND — NEED PROSPECTIVE VALIDATION"
    elif any(item.get("inner_sample_gate_passed", 0) for item in horizon_reports):
        verdict = "NO MATERIAL SELECTIVE EDGE FOUND"
    else:
        verdict = "INSUFFICIENT DATA"

    complexity_counts = Counter(item.complexity for item in templates)
    report = {
        "contract_version": SELECTIVE_CONTRACT_VERSION,
        "source_set_sha256": source_set_sha256,
        "primary_baseline": "GLOBAL_RET5_PERSISTENCE",
        "horizons": horizon_reports,
        "verdict": verdict,
        "top_20_research_candidates": all_candidates[:20],
        "single_feature_map": [
            _compact_edge(item) for item in all_candidates
            if int(item.get("complexity") or 0) == 1
        ],
        "family_incremental_value": _family_incremental_value(all_candidates),
        "top_15_selective_edges": [
            item for item in all_candidates
            if item.get("edge_maturity") != "INSUFFICIENT_DATA"
        ][:15],
        "top_10_baseline_failure_regimes": [
            item for item in all_candidates if item.get("baseline_failure_regime")
        ][:10],
        "where_it_helps": [item for item in all_candidates if item.get("where_it_helps")][:15],
        "where_it_hurts": [item for item in all_candidates if item.get("where_it_hurts")][:15],
        "options_research": _options_sections(all_candidates),
        "search_budget": {
            "feature_count": len(eligible_feature_ids),
            "candidate_templates": len(templates),
            "condition_count_distribution": {
                str(key): value for key, value in sorted(complexity_counts.items())
            },
            "max_templates": MAX_SELECTIVE_TEMPLATES,
            "max_conditions": MAX_CONDITIONS,
            "expected_max_inner_hypotheses": (
                len(templates) * 4 * len(SELECTIVE_HORIZONS)
            ),
            "expansion_requires_contract_change": True,
        },
        "observations_by_horizon": {
            str(horizon): len(by_horizon[horizon]) for horizon in SELECTIVE_HORIZONS
        },
        "hypotheses_tested": sum(
            int(item.get("inner_hypotheses_tested") or 0) for item in horizon_reports
        ),
        "sample_gate_passed": sum(
            int(item.get("inner_sample_gate_passed") or 0) for item in horizon_reports
        ),
        "fdr_passed": sum(
            int(item.get("inner_fdr_passed") or 0) for item in horizon_reports
        ),
        "stable_candidates": sum(
            int(item.get("stability_gate_passed") or 0) for item in horizon_reports
        ),
        "edge_maturity_counts": {
            maturity: sum(item.get("edge_maturity") == maturity for item in all_candidates)
            for maturity in (
                "INSUFFICIENT_DATA", "EARLY_CONTEXT", "RESEARCH_SIGNAL",
                "PROVISIONAL_EDGE", "ROBUST_EDGE",
            )
        },
        "family_ablation": None,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }
    report["family_ablation"] = family_ablation(report)
    return report
