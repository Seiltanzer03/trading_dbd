"""Policy manager v13: weighted stateful distribution revaluation.

The current option distribution already enters simulated paths through sigma,
drift, skew and term structure.  This layer adds a different kind of evidence:
how that same distribution has changed since trade entry and relative to its
trade-life average.

It is deliberately kept inside the existing ``option_distribution`` family.
That makes the derived signal influential without turning P(take), barrier EV,
median shift and tail migration into several independent votes.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import ai_policy_v12 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_BUILD_EVIDENCE = _impl.build_metric_evidence
_BASE_ANALYZE = _impl.analyze_policies


def _number(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _append_unique(collection: list[dict], item: dict) -> None:
    metric = item.get("metric")
    if not any(row.get("metric") == metric for row in collection if isinstance(row, dict)):
        collection.append(item)


def _compact(revaluation: dict) -> dict:
    """Keep decision-relevant values without bloating persisted AI snapshots."""
    if not isinstance(revaluation, dict) or not revaluation.get("available"):
        return {
            "available": False,
            "reason": revaluation.get("reason") if isinstance(revaluation, dict) else None,
        }
    entry = revaluation.get("entry") or {}
    average = revaluation.get("average") or {}
    current = revaluation.get("current") or {}
    de = revaluation.get("change_from_entry") or {}
    da = revaluation.get("change_from_average") or {}
    score = revaluation.get("score") or {}
    momentum = revaluation.get("momentum") or {}
    source = revaluation.get("source_quality") or current.get("source") or {}
    return {
        "available": True,
        "version": revaluation.get("version"),
        "sample_count": int(revaluation.get("sample_count") or 0),
        "age_sec": revaluation.get("age_sec"),
        "entry": {
            "p_take": entry.get("p_take"),
            "barrier_ev_r": entry.get("barrier_ev_r"),
            "q50_r": entry.get("q50_r"),
            "width_r": entry.get("width_r"),
            "buckets": deepcopy(entry.get("buckets") or {}),
        },
        "average": {
            "p_take": average.get("p_take"),
            "barrier_ev_r": average.get("barrier_ev_r"),
            "q50_r": average.get("q50_r"),
            "width_r": average.get("width_r"),
            "buckets": deepcopy(average.get("buckets") or {}),
        },
        "current": {
            "p_take": current.get("p_take"),
            "barrier_ev_r": current.get("barrier_ev_r"),
            "q50_r": current.get("q50_r"),
            "width_r": current.get("width_r"),
            "buckets": deepcopy(current.get("buckets") or {}),
        },
        "change_from_entry": {
            "p_take": de.get("p_take"),
            "barrier_ev_r": de.get("barrier_ev_r"),
            "q50_r": de.get("q50_r"),
            "width_r": de.get("width_r"),
            "buckets": deepcopy(de.get("buckets") or {}),
        },
        "change_from_average": {
            "p_take": da.get("p_take"),
            "barrier_ev_r": da.get("barrier_ev_r"),
            "q50_r": da.get("q50_r"),
            "width_r": da.get("width_r"),
        },
        "momentum": {
            "p_take_pp_per_min": momentum.get("p_take_pp_per_min"),
            "barrier_ev_r_per_min": momentum.get("barrier_ev_r_per_min"),
            "center_r_per_min": momentum.get("center_r_per_min"),
            "p_take_noise_pp": momentum.get("p_take_noise_pp"),
            "direction_consistency": momentum.get("direction_consistency"),
        },
        "score": {
            "raw": score.get("raw"),
            "weighted": score.get("weighted"),
            "direction": score.get("direction"),
            "confidence_weight": score.get("confidence_weight"),
            "source_weight": score.get("source_weight"),
            "sample_weight": score.get("sample_weight"),
            "noise_weight": score.get("noise_weight"),
        },
        "source_quality": {
            "mode": source.get("mode"),
            "label": source.get("label"),
            "weight": source.get("weight"),
            "chain_age_sec": source.get("chain_age_sec"),
            "experimental_proxy": bool(source.get("experimental_proxy")),
            "context_only": bool(source.get("context_only")),
        },
        "family": "option_distribution",
        "independent_vote": False,
    }


def _evidence_item(compact: dict) -> tuple[dict, str]:
    score = compact.get("score") or {}
    weighted = _number(score.get("weighted")) or 0.0
    confidence = _number(score.get("confidence_weight")) or 0.0
    samples = int(compact.get("sample_count") or 0)
    source = compact.get("source_quality") or {}
    de = compact.get("change_from_entry") or {}

    item = {
        "metric": "distribution_revaluation_weighted",
        "family": "option_distribution",
        "value": round(weighted, 4),
        "raw_score": score.get("raw"),
        "confidence_weight": round(confidence, 3),
        "source_weight": score.get("source_weight"),
        "sample_count": samples,
        "p_take_entry_delta": de.get("p_take"),
        "barrier_ev_entry_delta_r": de.get("barrier_ev_r"),
        "center_entry_delta_r": de.get("q50_r"),
        "width_entry_delta_r": de.get("width_r"),
        "mass_flow_from_entry": deepcopy(de.get("buckets") or {}),
        "source_mode": source.get("mode"),
        "authority": "weighted_derived_same_family",
        "independent_vote": False,
    }

    if source.get("context_only"):
        item.update({
            "context_only": True,
            "reason": "option anchor unavailable; scenario-only revaluation has no decision authority",
        })
        return item, "context"
    if samples < 5:
        item.update({
            "context_only": True,
            "reason": "fewer than five independent time samples since trade entry",
        })
        return item, "context"
    if confidence < 0.30:
        item.update({
            "context_only": True,
            "reason": "source/history/noise weight is too low for directional authority",
        })
        return item, "context"
    if weighted <= -0.18:
        return item, "adverse"
    if weighted >= 0.18:
        return item, "supportive"
    item.update({
        "context_only": True,
        "reason": "weighted revaluation remains inside the materiality threshold ±0.18",
    })
    return item, "context"


def build_metric_evidence(engine, tick: dict, ridge: dict, trade: dict,
                          inputs: PolicyInputs, sim: PathSimulation,
                          policy_metrics_map: dict[str, dict]) -> dict:
    evidence = _BASE_BUILD_EVIDENCE(
        engine, tick, ridge, trade, inputs, sim, policy_metrics_map
    )
    compact = _compact(tick.get("lattice_revaluation") or {})
    evidence["lattice_revaluation"] = compact

    adverse = list(evidence.get("adverse_confirmations") or [])
    supportive = list(evidence.get("supportive_contradictions") or [])
    context = list(evidence.get("context_observations") or [])

    if compact.get("available"):
        item, role = _evidence_item(compact)
        if role == "adverse":
            _append_unique(adverse, item)
        elif role == "supportive":
            _append_unique(supportive, item)
        else:
            _append_unique(context, item)

        uncertainty = list(evidence.get("uncertainty_flags") or [])
        source = compact.get("source_quality") or {}
        source_weight = _number(source.get("weight"))
        if source.get("mode") in {"indicative_mapping", "snapshot_mapping"}:
            flag = {
                "metric": "distribution_revaluation_source_weight",
                "value": source_weight,
                "reason": (
                    "derived revaluation is discounted, not disabled: delayed/proxy "
                    "mapping is informative but less authoritative than fresh mapping"
                ),
            }
            if not any(x.get("metric") == flag["metric"] for x in uncertainty if isinstance(x, dict)):
                uncertainty.append(flag)
        evidence["uncertainty_flags"] = uncertainty

    evidence["adverse_confirmations"] = adverse
    evidence["supportive_contradictions"] = supportive
    evidence["context_observations"] = context

    roles = evidence.setdefault("decision_roles", {})
    confirmation = list(roles.get("confirmation_gate") or [])
    if "weighted_distribution_revaluation" not in confirmation:
        confirmation.append("weighted_distribution_revaluation")
    roles["confirmation_gate"] = confirmation
    context_roles = list(roles.get("context_only") or [])
    if "low_weight_or_immature_revaluation" not in context_roles:
        context_roles.append("low_weight_or_immature_revaluation")
    roles["context_only"] = context_roles

    normalise = getattr(_impl, "_normalise_evidence", None)
    if callable(normalise):
        evidence = normalise(evidence)
    return evidence


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    evidence = result.get("evidence") or {}
    result["lattice_revaluation"] = deepcopy(evidence.get("lattice_revaluation") or {})
    result["version"] = "quant-policy-v13-weighted-distribution-revaluation"
    return result


def _chain(root):
    seen = set()
    current = root
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "_impl", None)


for module in _chain(_impl):
    module.build_metric_evidence = build_metric_evidence
    module.analyze_policies = analyze_policies

globals()["build_metric_evidence"] = build_metric_evidence
globals()["analyze_policies"] = analyze_policies
