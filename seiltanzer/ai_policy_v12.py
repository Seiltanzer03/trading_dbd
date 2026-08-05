"""Policy manager v12: explicit option-implied center diagnostics.

The engine already converts a plausible terminal RND mean into a shrunken and
capped ``drift_R``. That drift is therefore already present in every simulated
path, Expected value and CVaR calculation. This layer makes the relationship
explicit, adds one direction-aware option-distribution observation, and keeps a
rejected raw mean strictly context-only.
"""
from __future__ import annotations

from typing import Any

from . import ai_policy_v11 as _impl


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


def _option_center(cone: dict, inputs: PolicyInputs) -> dict:
    raw_mean = _number(cone.get("market_mean_r"))
    current = float(inputs.r0)
    robust_gap = float(inputs.drift_R)
    rejected_gap = _number(cone.get("forward_drift_rejected"))
    source = str(cone.get("forward_drift_source") or "unavailable")
    raw_gap = raw_mean - current if raw_mean is not None else None
    accepted = bool(
        raw_mean is not None
        and source == "bl_forward_shrunk"
        and rejected_gap is None
    )
    threshold = min(max(0.08 * float(inputs.sigma_R), 0.05), 0.15)
    return {
        "available": raw_mean is not None,
        "raw_mean_r": round(raw_mean, 4) if raw_mean is not None else None,
        "current_r": round(current, 4),
        "raw_gap_r": round(raw_gap, 4) if raw_gap is not None else None,
        "robust_forward_r": round(current + robust_gap, 4),
        "robust_gap_r": round(robust_gap, 4),
        "direction_threshold_r": round(threshold, 4),
        "source": source,
        "raw_mean_accepted": accepted,
        "raw_rejected_gap_r": (
            round(rejected_gap, 4) if rejected_gap is not None else None
        ),
        "optimizer_role": (
            "core_path_input_via_drift_R" if accepted
            else "context_only_rejected" if rejected_gap is not None
            else "neutral_or_unavailable"
        ),
    }


def _append_unique(collection: list[dict], item: dict) -> None:
    metric = item.get("metric")
    if not any(row.get("metric") == metric for row in collection if isinstance(row, dict)):
        collection.append(item)


def _compact_center_path(cone_rnd: dict, limit: int = 11) -> None:
    """Keep enough center-path shape for AI diagnostics without bloating history."""
    path = cone_rnd.get("center_path") or []
    if not isinstance(path, list) or len(path) <= limit:
        return
    last = len(path) - 1
    indexes = sorted({round(i * last / (limit - 1)) for i in range(limit)})
    cone_rnd["center_path"] = [path[index] for index in indexes]
    cone_rnd["center_path_points_total"] = len(path)
    cone_rnd["center_path_points_stored"] = len(indexes)


def build_metric_evidence(engine, tick: dict, ridge: dict, trade: dict,
                          inputs: PolicyInputs, sim: PathSimulation,
                          policy_metrics_map: dict[str, dict]) -> dict:
    evidence = _BASE_BUILD_EVIDENCE(
        engine, tick, ridge, trade, inputs, sim, policy_metrics_map
    )
    cone = tick.get("cone") or {}
    center = _option_center(cone, inputs)
    cone_rnd = evidence.setdefault("cone_rnd", {})
    _compact_center_path(cone_rnd)
    cone_rnd["option_center"] = center

    adverse = list(evidence.get("adverse_confirmations") or [])
    supportive = list(evidence.get("supportive_contradictions") or [])
    context = list(evidence.get("context_observations") or [])
    robust_gap = _number(center.get("robust_gap_r"))
    threshold = _number(center.get("direction_threshold_r")) or 0.05

    if center.get("raw_mean_accepted") and robust_gap is not None:
        item = {
            "metric": "option_center_robust_gap_r",
            "family": "option_distribution",
            "value": round(robust_gap, 4),
            "raw_mean_r": center.get("raw_mean_r"),
            "robust_forward_r": center.get("robust_forward_r"),
            "threshold_r": round(threshold, 4),
            "authority": "accepted_after_shrink_and_cap",
        }
        if robust_gap <= -threshold:
            _append_unique(adverse, item)
        elif robust_gap >= threshold:
            _append_unique(supportive, item)
        else:
            _append_unique(context, {
                **item,
                "context_only": True,
                "reason": "robust option-center gap is inside the noise threshold",
            })
    elif center.get("raw_rejected_gap_r") is not None:
        _append_unique(context, {
            "metric": "option_center_raw_rejected",
            "family": "option_distribution",
            "value": center.get("raw_rejected_gap_r"),
            "raw_mean_r": center.get("raw_mean_r"),
            "context_only": True,
            "authority": "rejected_raw_rnd_mean",
            "reason": "raw BL mean failed the engine plausibility check",
        })

    evidence["adverse_confirmations"] = adverse
    evidence["supportive_contradictions"] = supportive
    evidence["context_observations"] = context

    roles = evidence.setdefault("decision_roles", {})
    core = list(roles.get("core_path_inputs") or [])
    if "robust_option_center_drift" not in core:
        core.append("robust_option_center_drift")
    roles["core_path_inputs"] = core
    context_roles = list(roles.get("context_only") or [])
    if "rejected_raw_rnd_mean" not in context_roles:
        context_roles.append("rejected_raw_rnd_mean")
    roles["context_only"] = context_roles

    # Re-run the established family normaliser after adding the new row. The
    # explicit family tag ensures the new center cannot become a second option
    # vote beside barrier EV, median, mode or skew.
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
    # The report reads the center from evidence.cone_rnd. Do not duplicate it at
    # policy_manager root; verdict snapshots are persisted on every AI review.
    result["version"] = "quant-policy-v12-explicit-option-center"
    return result


def _chain(root):
    seen = set()
    current = root
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "_impl", None)


# Lower analysis layers resolve this name in their own module globals.
for module in _chain(_impl):
    module.build_metric_evidence = build_metric_evidence

globals()["build_metric_evidence"] = build_metric_evidence
globals()["analyze_policies"] = analyze_policies
