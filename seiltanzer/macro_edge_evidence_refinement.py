"""Release-aware macro features for EDE without weakening causal gates.

This refinement connects already-frozen macro context to the canonical EDE feature
space and makes a macro release, rather than every repeated market T0 carrying
that release, the statistical dependence unit for macro-conditioned candidates.

It deliberately does *not* invent historical releases.  A value is admitted only
when it is present in the immutable T0 context and its own available_at is no
later than T0.  Historical official-archive bootstrap can therefore be added on
top of the same contract without inflating effective_n.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np


MACRO_EDGE_REFINEMENT_VERSION = "macro-ede-release-independence-v1"

MACRO_FEATURE_FAMILY: dict[str, str] = {
    "macro.cpi_headline_mom_pct": "CPI",
    "macro.cpi_core_mom_pct": "CPI",
    "macro.cpi_headline_yoy_pct": "CPI",
    "macro.cpi_core_yoy_pct": "CPI",
    "macro.cpi_headline_mom_change_pp": "CPI",
    "macro.cpi_core_mom_change_pp": "CPI",
    "macro.nfp_payroll_change_k": "NFP",
    "macro.nfp_previous_payroll_change_k": "NFP",
    "macro.nfp_unemployment_rate_pct": "NFP",
    "macro.nfp_unemployment_change_pp": "NFP",
    "macro.nfp_wage_mom_pct": "NFP",
    "macro.nfp_wage_yoy_pct": "NFP",
    "macro.ism_manufacturing_pmi": "ISM_MANUFACTURING",
    "macro.ism_manufacturing_pmi_change_pp": "ISM_MANUFACTURING",
    "macro.ism_services_pmi": "ISM_SERVICES",
    "macro.ism_services_pmi_change_pp": "ISM_SERVICES",
    "macro.fomc_policy_tone": "FOMC_STATEMENT",
    "macro.fomc_policy_shift": "FOMC_STATEMENT",
    "macro.fomc_inflation_concern": "FOMC_STATEMENT",
    "macro.fomc_growth_concern": "FOMC_STATEMENT",
    "macro.fomc_forward_guidance_shift": "FOMC_STATEMENT",
    "macro.fomc_uncertainty": "FOMC_STATEMENT",
}

FOMC_SEMANTIC_FEATURES = {
    "policy_tone": "macro.fomc_policy_tone",
    "policy_shift": "macro.fomc_policy_shift",
    "inflation_concern": "macro.fomc_inflation_concern",
    "growth_concern": "macro.fomc_growth_concern",
    "forward_guidance_shift": "macro.fomc_forward_guidance_shift",
    "uncertainty": "macro.fomc_uncertainty",
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _macro_context(frozen: dict[str, Any]) -> dict[str, Any]:
    value = frozen.get("macro_context_v1") if isinstance(frozen, dict) else None
    return value if isinstance(value, dict) else {}


def macro_feature_records(*, frozen: dict[str, Any], instrument: str,
                          t0: float, horizon: int) -> tuple[
                              dict[str, Any], dict[str, dict[str, Any]]]:
    """Materialize causal macro FeatureValue records plus release provenance."""
    from .edge_discovery.feature_view import feature_value

    context = _macro_context(frozen)
    records: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}

    numeric = context.get("numeric_macro")
    numeric = numeric if isinstance(numeric, dict) else {}
    vector = numeric.get("candidate_vector")
    vector = vector if isinstance(vector, dict) else {}
    releases = numeric.get("releases")
    releases = releases if isinstance(releases, dict) else {}

    for feature_id, raw in vector.items():
        family = MACRO_FEATURE_FAMILY.get(str(feature_id))
        value = _finite(raw)
        release = releases.get(family) if family else None
        release = release if isinstance(release, dict) else {}
        available_at = _finite(release.get("available_at"))
        release_id = str(release.get("release_id") or "")
        if family is None or value is None or available_at is None or not release_id:
            continue
        if available_at > float(t0) + 1e-6 or release.get("status") != "VALID":
            continue
        record = feature_value(
            instrument=instrument, t0=t0, horizon=horizon,
            feature_id=str(feature_id), value=value, asof=available_at,
            historical_available=False, live_available=True,
            training_eligible=True, dependency_group=f"macro_release:{family}",
        )
        if not record.training_eligible:
            continue
        records[str(feature_id)] = record
        provenance[str(feature_id)] = {
            "provenance": "FROZEN_OFFICIAL_MACRO_T0",
            "release_id": release_id,
            "release_family": family,
            "release_period": release.get("period"),
            "available_at": available_at,
            "official_source_verified": bool(release.get("official_source_verified", True)),
            "future_points_used": False,
        }

    fomc = context.get("fomc")
    fomc = fomc if isinstance(fomc, dict) else {}
    semantic = fomc.get("semantic")
    semantic = semantic if isinstance(semantic, dict) else {}
    available_at = _finite(fomc.get("available_at"))
    release_id = str(fomc.get("document_id") or "")
    if (fomc.get("available") is True and available_at is not None
            and available_at <= float(t0) + 1e-6 and release_id
            and fomc.get("official_source_verified") is True):
        for source_name, feature_id in FOMC_SEMANTIC_FEATURES.items():
            value = _finite(semantic.get(source_name))
            if value is None:
                continue
            record = feature_value(
                instrument=instrument, t0=t0, horizon=horizon,
                feature_id=feature_id, value=value, asof=available_at,
                historical_available=False, live_available=True,
                training_eligible=True,
                dependency_group="macro_release:FOMC_STATEMENT",
            )
            if not record.training_eligible:
                continue
            records[feature_id] = record
            provenance[feature_id] = {
                "provenance": "FROZEN_OFFICIAL_FOMC_T0",
                "release_id": release_id,
                "release_family": "FOMC_STATEMENT",
                "release_period": fomc.get("published_at"),
                "available_at": available_at,
                "official_source_verified": True,
                "future_points_used": False,
            }
    return records, provenance


def _macro_feature_definitions(registry) -> tuple[Any, ...]:
    definitions = []
    for feature_id, family in MACRO_FEATURE_FAMILY.items():
        definitions.append(registry._f(
            feature_id,
            "MACRO_NUMERIC" if family != "FOMC_STATEMENT" else "MACRO_FOMC",
            "macro_t0_context.macro_context_v1",
            frequency="official macro release",
            asof="official release available_at <= T0",
            historical="UNAVAILABLE",
            live="AVAILABLE",
            dependency=f"macro_release:{family}",
            notes=(
                "release-level dependence; repeated market T0 rows carrying one release "
                "must not increase effective_n"
            ),
        ))
    return tuple(definitions)


def _release_ids_for_rule(row: dict[str, Any], rule: Any) -> tuple[str, ...]:
    ids: list[str] = []
    feature_values = row.get("feature_values") or {}
    for condition in getattr(rule, "conditions", ()):
        feature_id = str(getattr(condition, "feature_id", ""))
        if feature_id not in MACRO_FEATURE_FAMILY:
            continue
        record = feature_values.get(feature_id) or {}
        release_id = str(record.get("release_id") or "")
        if release_id:
            ids.append(release_id)
    return tuple(sorted(set(ids)))


def release_dependency_rows(rows: list[dict[str, Any]], rule: Any) -> list[dict[str, Any]]:
    """Clone only macro-conditioned rows with an explicit release dependence unit."""
    macro_conditions = [
        condition for condition in getattr(rule, "conditions", ())
        if str(getattr(condition, "feature_id", "")) in MACRO_FEATURE_FAMILY
    ]
    if not macro_conditions:
        return rows
    output: list[dict[str, Any]] = []
    for row in rows:
        release_ids = _release_ids_for_rule(row, rule)
        if not release_ids:
            output.append(row)
            continue
        copy = dict(row)
        copy["_ede_dependency_unit_id"] = "macro-release|" + "|".join(release_ids)
        copy["_ede_dependency_unit_kind"] = "OFFICIAL_MACRO_RELEASE_ID"
        output.append(copy)
    return output


def release_aware_weights(rows: list[dict[str, Any]]) -> tuple[np.ndarray, int]:
    """Preserve default dependency weights unless a macro rule supplied release IDs."""
    from .g1_short_horizon_historical_wf import _dependency_key

    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        explicit = str(row.get("_ede_dependency_unit_id") or "")
        groups[explicit or _dependency_key(row)].append(index)
    out = np.zeros(len(rows), dtype=float)
    for members in groups.values():
        per = 1.0 / len(members)
        for index in members:
            out[index] = per
    return out, len(groups)


def _paired_group_loss_deltas_release_aware(
        rows: list[dict[str, Any]], model: np.ndarray,
        baseline: np.ndarray) -> np.ndarray:
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in rows])
    model = np.clip(np.asarray(model, dtype=float), 1e-6, 1-1e-6)
    baseline = np.clip(np.asarray(baseline, dtype=float), 1e-6, 1-1e-6)
    if len(rows) != len(model) or len(rows) != len(baseline):
        raise ValueError("paired loss inputs must have identical lengths")
    model_loss = (model-y)**2 + (-(y*np.log(model)+(1-y)*np.log(1-model)))
    baseline_loss = ((baseline-y)**2
                     + (-(y*np.log(baseline)+(1-y)*np.log(1-baseline))))
    groups: dict[str, list[float]] = defaultdict(list)
    for index, row in enumerate(rows):
        explicit = str(row.get("_ede_dependency_unit_id") or "")
        key = explicit or f"captured-ts|{float(row['captured_ts']):.6f}"
        groups[key].append(float(baseline_loss[index]-model_loss[index]))
    return np.asarray([float(np.mean(items)) for items in groups.values()], dtype=float)


def _install_discovery_release_dependency(discovery, scoring) -> None:
    if getattr(discovery, "_macro_release_dependency_refinement", None) == MACRO_EDGE_REFINEMENT_VERSION:
        return

    discovery._weights = release_aware_weights
    scoring._weights = release_aware_weights
    scoring._paired_group_loss_deltas = _paired_group_loss_deltas_release_aware

    def evaluate_inner(template, train, validation):
        rule = discovery.fit_rule(template, train)
        if rule is None:
            return None
        selected_train = [row for row, keep in zip(train, discovery.rule_mask(train, rule)) if keep]
        selected_validation = [
            row for row, keep in zip(validation, discovery.rule_mask(validation, rule)) if keep]
        selected_train = release_dependency_rows(selected_train, rule)
        selected_validation = release_dependency_rows(selected_validation, rule)
        if not discovery._inner_sample_allowed(selected_train, selected_validation):
            return None
        predictions = discovery._predictions(
            train, selected_validation, conditional_train=selected_train)
        conditional = discovery.metrics(
            selected_validation, predictions["conditional_ret5_persistence"])
        global_ret5 = discovery.metrics(
            selected_validation, predictions["global_ret5_persistence"])
        sanity = {
            name: discovery.metrics(selected_validation, predictions[name])
            for name in ("constant_0_5", "causal_base_rate", "ret15_momentum")
        }
        improvement = discovery.relative_improvement(conditional, global_ret5)
        p_value = discovery.paired_loss_pvalue(
            selected_validation, predictions["conditional_ret5_persistence"],
            predictions["global_ret5_persistence"])
        score = 0.5*(improvement["brier"]+improvement["logloss"])
        score -= 0.0015*(template.complexity-1)
        return {
            "template_id": template.template_id, "complexity": template.complexity,
            "rule": rule, "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
            "conditional_ret5": conditional, "global_ret5": global_ret5,
            "global_ret5_comparison": discovery._global_ret5_comparison(
                conditional, global_ret5),
            "sanity_baselines": sanity, "improvement": improvement,
            "p_value": p_value, "inner_score": score,
            "dependency_unit": (
                "OFFICIAL_MACRO_RELEASE_ID" if any(
                    c.feature_id in MACRO_FEATURE_FAMILY for c in rule.conditions)
                else "DEFAULT_MARKET_OVERLAP_BUCKET"
            ),
        }

    def outer_evaluation(item, template, train, test):
        rule = discovery.fit_rule(template, train)
        if rule is None:
            return None
        funnel: list[dict[str, Any]] = []
        for depth in range(0, len(rule.conditions)+1):
            prefix = discovery.FittedRule(rule.template_id, rule.conditions[:depth])
            selected_train = (train if depth == 0 else [
                row for row, keep in zip(train, discovery.rule_mask(train, prefix)) if keep])
            selected_test = (test if depth == 0 else [
                row for row, keep in zip(test, discovery.rule_mask(test, prefix)) if keep])
            selected_train = release_dependency_rows(selected_train, prefix)
            selected_test = release_dependency_rows(selected_test, prefix)
            if len(selected_train) < 100 or len(selected_test) < 20:
                continue
            predictions = discovery._predictions(
                train, selected_test, conditional_train=selected_train)
            model_prediction = predictions["conditional_ret5_persistence"]
            baseline_prediction = predictions["global_ret5_persistence"]
            model = discovery.metrics(selected_test, model_prediction)
            baseline_metrics = discovery.metrics(selected_test, baseline_prediction)
            sanity = {
                name: discovery.metrics(selected_test, predictions[name])
                for name in ("constant_0_5", "causal_base_rate", "ret15_momentum")
            }
            funnel.append({
                "depth": depth, "rows": selected_test,
                "model_prediction": model_prediction,
                "baseline_prediction": baseline_prediction,
                "model": model, "baseline": baseline_metrics,
                "sanity_baselines": sanity,
                "sanity_predictions": {name: predictions[name] for name in sanity},
                "improvement": discovery.relative_improvement(model, baseline_metrics),
            })
        if not funnel or funnel[-1]["depth"] != len(rule.conditions):
            return None
        final = funnel[-1]
        return {
            "rule": rule, "rows": final["rows"],
            "model_prediction": final["model_prediction"],
            "baseline_prediction": final["baseline_prediction"],
            "model": final["model"], "baseline": final["baseline"],
            "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
            "improvement": final["improvement"],
            "global_ret5_comparison": discovery._global_ret5_comparison(
                final["model"], final["baseline"]),
            "sanity_baselines": final["sanity_baselines"],
            "sanity_predictions": final["sanity_predictions"],
            "joint_positive": (
                final["improvement"]["brier"] > 0
                and final["improvement"]["logloss"] > 0),
            "funnel": funnel,
        }

    discovery._evaluate_inner = evaluate_inner
    discovery._outer_evaluation = outer_evaluation
    discovery._macro_release_dependency_refinement = MACRO_EDGE_REFINEMENT_VERSION


def install_macro_edge_evidence_refinement() -> None:
    """Install canonical macro IDs, T0 adapters and release-aware EDE weighting."""
    from .edge_discovery import ai_context, discovery, filters, prospective, registry, scoring

    if getattr(prospective, "_macro_edge_refinement", None) == MACRO_EDGE_REFINEMENT_VERSION:
        return

    existing = {item.feature_id for item in registry.FEATURES}
    additions = tuple(
        item for item in _macro_feature_definitions(registry)
        if item.feature_id not in existing
    )
    extended = tuple(registry.FEATURES) + additions
    registry.FEATURES = extended
    prospective.FEATURES = extended
    filters.FEATURES = extended
    ai_context.FEATURES = extended
    filters.PROSPECTIVE_NUMERIC_FEATURES = tuple(
        item.feature_id for item in extended
        if item.research_scope == "G1S" and item.training_eligibility
        and item.feature_id not in {
            "price.ret_5m", "regime.asset", "regime.asset_family",
            "regime.session_utc", "regime.trend", "regime.volatility",
            "regime.macro", "cross.confirmation",
        }
    )

    previous_feature_values = prospective.ProspectiveFeatureAdapter._feature_values

    def feature_values(self, row: dict[str, Any], *, strict: bool):
        values, rejected, provenance = previous_feature_values(self, row, strict=strict)
        frozen = prospective._loads(row.get("frozen_features_json"))
        macro_values, macro_provenance = macro_feature_records(
            frozen=frozen, instrument=str(row["instrument"]),
            t0=float(row["captured_ts"]), horizon=int(row["horizon_minutes"]),
        )
        values.update(macro_values)
        provenance.update(macro_provenance)
        return values, rejected, provenance

    prospective.ProspectiveFeatureAdapter._feature_values = feature_values

    previous_current_map = ai_context.canonical_current_feature_map

    def current_map(frozen: dict[str, Any], instrument: str):
        values = previous_current_map(frozen, instrument)
        raw = frozen.get("_raw_frozen") if isinstance(frozen, dict) else None
        raw = raw if isinstance(raw, dict) else frozen
        try:
            t0 = float(frozen["observation_t0"])
        except (KeyError, TypeError, ValueError):
            return values
        macro_values, macro_provenance = macro_feature_records(
            frozen=raw if isinstance(raw, dict) else {}, instrument=instrument,
            t0=t0, horizon=0,
        )
        for feature_id, record in macro_values.items():
            serialized = record.as_dict()
            serialized.update(macro_provenance.get(feature_id, {}))
            serialized["available"] = serialized.get("availability") == "AVAILABLE"
            serialized["live_applicability"] = "LIVE_APPLICABLE"
            values[feature_id] = serialized
        return values

    ai_context.canonical_current_feature_map = current_map
    _install_discovery_release_dependency(discovery, scoring)
    prospective._macro_edge_refinement = MACRO_EDGE_REFINEMENT_VERSION
