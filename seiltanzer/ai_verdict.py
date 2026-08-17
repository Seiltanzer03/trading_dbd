"""Stable public facade for the quantitative AI verdict v18 + v19 renderer."""
from __future__ import annotations

from . import ai_verdict_v18 as _impl
# Import installs the structured v19 renderer into the v18 render chain while
# preserving the established public request/normalization facade identities.
from . import ai_verdict_v19 as _v19  # noqa: F401

# The public facade contract deliberately remains v18. V19 is a presentation
# integrity extension, not a new decision-authority API.
_impl.render_policy_report.__module__ = _impl.__name__


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_BUILD_SNAPSHOT_V18 = _impl.build_snapshot
_BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = _impl._enforce_snapshot_budget
_REPORT_INTEGRITY_VERSION = "ai-verdict-report-integrity-v1"
_AVAILABILITY_CONTRACT_VERSION = "ai-metric-availability-v1"

_POLICY_REPORT_KEYS = (
    "expected_final_r", "median_final_r", "cvar10_r",
    "expected_final_r_net", "cvar10_r_net",
    "p_final_profit", "p_final_loss",
    "p_giveback_0_25_from_now", "p_giveback_0_50_from_now",
    "p_next_rung_before_stop", "p_stop_before_next_rung",
    "next_rung_r", "expected_event_minutes", "no_event_probability",
    "eligible", "reason",
)
_SCENARIO_REPORT_KEYS = (
    "scenario_count", "next_rung_r", "p_next_rung_before_stop",
    "rung_first_count", "p_stop_before_next_rung", "stop_first_count",
    "p_unresolved_full_horizon", "unresolved_count", "resolved_count",
    "full_horizon_minutes", "mean_event_minutes_given_resolved",
    "p25_resolution_minutes", "p50_resolution_minutes", "p75_resolution_minutes",
    "conditional_median_resolution_minutes", "restricted_mean_resolution_minutes",
    "take_first_probability", "stop_or_be_first_probability",
)
_STABILITY_REPORT_KEYS = (
    "selected_count", "checks", "selected_share", "winner", "status",
    "required_share", "threshold", "stable", "decision_uncertain",
)
_RISK_TRADEOFF_KEYS = (
    "expected_delta_vs_hold_r", "expected_delta_label",
    "cvar_improvement_vs_hold_r", "expected_r_sacrifice",
    "cvar_gain_r", "status", "reason",
)
_ACTIVE_EDGE_KEYS = (
    "contract_version", "available", "weight_fraction", "max_weight_fraction",
    "direction_score", "agreement", "preferred_close_fraction",
    "strict_directional_share", "independent_bucket_n",
    "matched_directional_signal_n", "strict_directional_signal_n",
    "basis", "high_risk_only_cap", "absolute_cap",
    "prospective_calibration_pending", "production_role",
    "hard_risk_override", "may_override_cvar_floor", "may_widen_stop",
    "automatic_execution_source", "reason",
)
_OPTION_BARRIER_KEYS = (
    "available", "p_take", "p_stop", "no_touch", "barrier_ev_r",
    "source", "status", "authority", "independent_vote",
)
_MC_VALIDATION_KEYS = (
    "status", "checks", "winner", "winner_share", "winner_stability",
    "ranking_agreement", "decision_uncertain", "expected_r_ci_width",
    "cvar10_r_ci_width", "effective_paths", "effective_path_count",
)
_AVAILABILITY_ROW_KEYS = (
    "available", "status", "source", "role", "age_sec", "symbol",
    "reason", "quality", "quality_score", "proxy_quality", "is_proxy",
    "proxy", "fallback_tier", "fallback_source",
)


def _report_row(row, keys):
    if not isinstance(row, dict):
        return {}
    return {key: row.get(key) for key in keys if key in row}


def _report_scalar_map(row, *, max_items: int = 24):
    """Keep only compact scalar audit facts; never copy a research workspace."""
    if not isinstance(row, dict):
        return {}
    output = {}
    for key in sorted(row):
        if len(output) >= max_items:
            break
        value = row.get(key)
        if value is None or isinstance(value, (str, int, float, bool)):
            output[key] = value
        elif isinstance(value, dict) and len(value) <= 12:
            nested = {
                sub_key: sub_value for sub_key, sub_value in value.items()
                if sub_value is None or isinstance(sub_value, (str, int, float, bool))
            }
            if nested:
                output[key] = nested
    return output


def _merge_missing(target: dict, preserved: dict) -> None:
    if not isinstance(target, dict) or not isinstance(preserved, dict):
        return
    for key, value in preserved.items():
        if key not in target or target.get(key) in (None, "[bounded]"):
            target[key] = value
        elif isinstance(target.get(key), dict) and isinstance(value, dict):
            _merge_missing(target[key], value)


def _build_metric_availability_contract(snapshot: dict) -> dict:
    """Publish provenance/fallback semantics without inventing market values.

    The contract makes every audited decision input explicitly inspectable. A
    primary feed outage is represented as degraded/fallback/unavailable state,
    never as a numerical zero. Source adapters may populate richer fallback
    fields; this layer preserves them without guessing missing provenance.
    """
    previous = snapshot.get("metric_availability_contract") or {}
    manager = snapshot.get("policy_manager") or {}
    audit = manager.get("input_audit") or {}
    rows = audit.get("rows") or {}
    inputs = {}
    if isinstance(rows, dict):
        for name, row in rows.items():
            if not isinstance(row, dict):
                continue
            compact = _report_row(row, _AVAILABILITY_ROW_KEYS)
            items = row.get("items") or []
            if isinstance(items, list) and items:
                compact["items"] = [
                    _report_row(item, _AVAILABILITY_ROW_KEYS)
                    for item in items[:16] if isinstance(item, dict)
                ]
                compact["item_count"] = len(items)
            inputs[str(name)] = compact

    root = snapshot.get("metric_coverage") or {}
    coverage = root.get("summary") or root
    contract = {
        "contract_version": _AVAILABILITY_CONTRACT_VERSION,
        "goal": "ALL_DECISION_CRITICAL_METRICS_EXPLICITLY_EVALUABLE",
        "missing_is_zero": False,
        "fabrication_allowed": False,
        "fallback_order": [
            "PRIMARY", "FALLBACK_SOURCE", "LAST_GOOD_CACHE", "MATHEMATICAL_PROXY",
        ],
        "fallback_rule": (
            "use the best valid source allowed by the metric contract; preserve "
            "source, age, quality and proxy/fallback status"
        ),
        "primary_unavailable_semantics": "DEGRADED_OR_UNAVAILABLE_NEVER_ZERO",
        "required_provenance_fields": [
            "source", "age_sec", "quality_or_status", "proxy_or_fallback_status",
        ],
        "input_audit_available_count": audit.get("available_count"),
        "input_audit_total_count": audit.get("total_count"),
        "all_required_available": audit.get("all_required_available"),
        "missing_required": list(audit.get("missing_required") or []),
        "degraded_inputs": list(audit.get("degraded_inputs") or []),
        "coverage_available_groups": coverage.get("available_groups") if isinstance(coverage, dict) else None,
        "coverage_total_groups": coverage.get("total_groups") if isinstance(coverage, dict) else None,
        "inputs": inputs,
    }
    if isinstance(previous, dict):
        _merge_missing(contract, previous)
    return contract


def _capture_report_integrity(snapshot: dict) -> dict:
    """Freeze presentation-critical facts before v18 removes debug workspaces."""
    manager = snapshot.get("policy_manager") or {}
    policies = manager.get("policies") or {}
    report = {
        "contract_version": _REPORT_INTEGRITY_VERSION,
        "role": "PRESENTATION_FACT_PRESERVATION_ONLY",
        "decision_authority": False,
        "missing_is_zero": False,
        "policies": {
            name: _report_row(row, _POLICY_REPORT_KEYS)
            for name, row in policies.items() if isinstance(row, dict)
        },
        "scenario_geometry": _report_row(
            manager.get("scenario_geometry") or {}, _SCENARIO_REPORT_KEYS),
        "raw_optimizer_stability": _report_row(
            manager.get("raw_optimizer_stability") or {}, _STABILITY_REPORT_KEYS),
        "stability": _report_row(
            manager.get("stability") or {}, _STABILITY_REPORT_KEYS),
        "risk_tradeoff": _report_row(
            manager.get("risk_tradeoff") or {}, _RISK_TRADEOFF_KEYS),
        "monte_carlo_validation": _report_row(
            manager.get("monte_carlo_validation") or {}, _MC_VALIDATION_KEYS),
        "active_edge_provisional_weight": _report_row(
            manager.get("active_edge_provisional_weight") or {}, _ACTIVE_EDGE_KEYS),
        "option_barrier": _report_row(
            ((manager.get("evidence") or {}).get("option_barrier") or {}),
            _OPTION_BARRIER_KEYS),
    }
    geometry = manager.get("scenario_geometry") or {}
    windows = geometry.get("no_event_windows") or {}
    if isinstance(windows, dict):
        compact_windows = {
            name: _report_scalar_map(row, max_items=8)
            for name, row in windows.items() if isinstance(row, dict)
        }
        if compact_windows:
            report["scenario_geometry"]["no_event_windows"] = compact_windows
    audit = manager.get("input_audit") or {}
    report["input_audit"] = _report_row(
        audit,
        ("available_count", "total_count", "all_required_available",
         "missing_required", "degraded_inputs"),
    )
    mc_quality = snapshot.get("monte_carlo_quality") or {}
    if isinstance(mc_quality, dict):
        report["monte_carlo_quality"] = _report_scalar_map(mc_quality)
    trade_geometry = snapshot.get("trade_geometry") or {}
    if isinstance(trade_geometry, dict):
        report["trade_geometry"] = _report_scalar_map(trade_geometry)
    previous = snapshot.get("report_integrity") or {}
    if isinstance(previous, dict):
        _merge_missing(report, previous)
    return {key: value for key, value in report.items() if value not in ({}, [], None)}


def _restore_report_integrity_views(snapshot: dict, report: dict) -> None:
    """Restore only the tiny views consumed by the renderer and verdict model."""
    manager = snapshot.setdefault("policy_manager", {})
    for key in (
        "scenario_geometry", "raw_optimizer_stability", "stability",
        "risk_tradeoff", "monte_carlo_validation", "active_edge_provisional_weight",
    ):
        preserved = report.get(key) or {}
        if not preserved:
            continue
        current = manager.get(key)
        if not isinstance(current, dict):
            manager[key] = dict(preserved)
        else:
            _merge_missing(current, preserved)

    preserved_policies = report.get("policies") or {}
    policies = manager.setdefault("policies", {})
    for name, preserved in preserved_policies.items():
        current = policies.get(name)
        if not isinstance(current, dict):
            policies[name] = dict(preserved)
        else:
            _merge_missing(current, preserved)

    audit = manager.setdefault("input_audit", {})
    _merge_missing(audit, report.get("input_audit") or {})
    evidence = manager.setdefault("evidence", {})
    barrier = evidence.get("option_barrier")
    if not isinstance(barrier, dict):
        if report.get("option_barrier"):
            evidence["option_barrier"] = dict(report["option_barrier"])
    else:
        _merge_missing(barrier, report.get("option_barrier") or {})
    if report.get("monte_carlo_quality"):
        quality = snapshot.setdefault("monte_carlo_quality", {})
        _merge_missing(quality, report["monte_carlo_quality"])
    if report.get("trade_geometry"):
        geometry = snapshot.setdefault("trade_geometry", {})
        _merge_missing(geometry, report["trade_geometry"])


def _enforce_snapshot_budget_with_report_integrity(snapshot: dict) -> None:
    # Capture both facts and provenance before v18 drops oversized explanatory
    # workspaces. On a second budget pass, merge the previous richer compact
    # contract instead of replacing it with a poorer already-compacted view.
    snapshot["metric_availability_contract"] = _build_metric_availability_contract(snapshot)
    report = _capture_report_integrity(snapshot)
    snapshot["report_integrity"] = report
    _BASE_ENFORCE_SNAPSHOT_BUDGET_V18(snapshot)
    _restore_report_integrity_views(snapshot, report)
    budget = snapshot.setdefault("snapshot_budget", {})
    budget["final_bytes"] = _impl._snapshot_bytes(snapshot)
    if budget["final_bytes"] >= _impl.SNAPSHOT_LIMIT_BYTES:
        # Keep root compact contracts even in an unusually large snapshot;
        # duplicate restored manager views are lower priority.
        for key in (
            "scenario_geometry", "raw_optimizer_stability", "stability",
            "risk_tradeoff", "monte_carlo_validation", "active_edge_provisional_weight",
        ):
            (snapshot.get("policy_manager") or {}).pop(key, None)
        budget["final_bytes"] = _impl._snapshot_bytes(snapshot)
    if budget["final_bytes"] >= _impl.SNAPSHOT_LIMIT_BYTES:
        raise RuntimeError("AI snapshot byte budget exceeded after report-integrity preservation")


# V18 resolves this module global at build time, so the wrapper captures the
# rich deterministic workspace before each byte-budget pass (including the
# second pass after EDE shadow context is attached below).
_impl._enforce_snapshot_budget = _enforce_snapshot_budget_with_report_integrity

# Make the verdict model prefer preserved facts instead of interpreting a
# compacted/missing workspace as a numerical zero.
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
REPORT_INTEGRITY — компактная копия уже рассчитанных deterministic фактов,
сохранённая до byte-compaction. Если подробный workspace удалён/сжат, используй
report_integrity как источник этих же чисел. Отсутствующее значение никогда не
считай нулём: missing/unavailable != 0. Не смешивай execution-MC physical path
probabilities, option risk-neutral Q barrier metrics и EDE research context.
METRIC_AVAILABILITY_CONTRACT задаёт цепочку PRIMARY → FALLBACK_SOURCE →
LAST_GOOD_CACHE → MATHEMATICAL_PROXY. Используй fallback только когда он явно
помечен source/age/quality/proxy status; запрещено придумывать значение или
выдавать proxy за primary. active_edge_provisional_weight — production bounded
soft-ranking только внутри hard-risk/CVaR eligible policies; EDE causal/prospective
shadow сам по себе не имеет production directional authority и не может вызвать
CLOSE/EXIT.
"""
_impl.SYSTEM_PROMPT = SYSTEM_PROMPT


_EDE_MATURITY_LINE_RU = {
    "EARLY_CONTEXT": (
        "Есть ранний положительный conditional-context; это ещё не edge-сигнал "
        "и его вес в production decision score равен нулю."
    ),
    "RESEARCH_SIGNAL": (
        "Есть research signal по conditional edge; он может только слабо "
        "подтверждать или опровергать контекст и не имеет trading authority."
    ),
    "PROVISIONAL_EDGE": (
        "Есть provisional conditional edge; он остаётся shadow evidence и не "
        "может самостоятельно вызвать CLOSE/EXIT."
    ),
    "ROBUST_EDGE": (
        "Conditional edge прошёл robust evidence gates, но отдельного promotion "
        "в production authority не было."
    ),
    "INSUFFICIENT_DATA": (
        "Данных может быть достаточно для рыночного контекста, но conditional "
        "edge ещё не доказан."
    ),
}


def _normalize_ede_maturity_language(context: dict) -> None:
    """Keep early/research/provisional evidence from being described as validated."""
    lines = context.get("context_lines_ru")
    if not isinstance(lines, list):
        return
    maturity = str(context.get("edge_maturity") or "INSUFFICIENT_DATA")
    replacement = _EDE_MATURITY_LINE_RU.get(
        maturity, _EDE_MATURITY_LINE_RU["INSUFFICIENT_DATA"])
    prefixes = (
        "Conditional edge подтверждён",
        "Данных может быть достаточно для контекста",
        "Данных может быть достаточно для рыночного контекста",
        "Есть ранний положительный conditional-context",
        "Есть research signal по conditional edge",
        "Есть provisional conditional edge",
        "Conditional edge прошёл robust evidence gates",
    )
    for index, line in enumerate(lines):
        if isinstance(line, str) and line.startswith(prefixes):
            lines[index] = replacement
            return
    lines.append(replacement)


def _compact_ede_shadow(engine, snapshot: dict) -> dict:
    """Read only worker-materialized bounded evidence; never scan research ledger."""
    try:
        from .edge_discovery.shadow_cache import load_shadow_summary_cache
        cutoff = float(snapshot.get("captured_ts") or 0.0)
        cached = load_shadow_summary_cache(engine, cutoff_ts=cutoff)
        if cached is None:
            raise ValueError("bounded shadow summary unavailable for snapshot cutoff")
        summary = cached["summary"]
    except Exception:
        return {
            "available": False,
            "reason": "SHADOW_SUMMARY_CACHE_UNAVAILABLE",
            "request_time_ledger_scan": False,
            "production_authority": False,
            "auto_promotion": False,
        }
    edge = ((snapshot.get("ede_causal_context") or {}).get("edge") or {})
    candidate_id = str(edge.get("candidate_id") or "")
    selected = (summary.get("candidates") or {}).get(candidate_id) if candidate_id else None
    statuses = {}
    for row in (summary.get("candidates") or {}).values():
        status = str(row.get("status") or "SHADOW_ACTIVE")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "available": bool(summary.get("prediction_count")),
        "summary_cutoff_ts": cached.get("summary_cutoff_ts"),
        "request_time_ledger_scan": False,
        "candidate_count": int(summary.get("candidate_count") or 0),
        "prediction_count": int(summary.get("prediction_count") or 0),
        "resolved_count": int(summary.get("resolved_count") or 0),
        "pending_count": int(summary.get("pending_count") or 0),
        "lifecycle_counts": statuses,
        "selected_candidate": ({
            "candidate_id": candidate_id,
            "status": selected.get("status"),
            "last_25": selected.get("last_25"),
            "last_50": selected.get("last_50"),
            "last_100": selected.get("last_100"),
            "all_prospective": selected.get("all_prospective"),
        } if selected else None),
        "current_candidate_matches": bool(edge.get("applies_to_current_context")),
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }


def build_snapshot(engine) -> dict:
    """V18 snapshot plus compact v1.3 prospective-shadow research evidence."""
    snapshot = _BASE_BUILD_SNAPSHOT_V18(engine)
    shadow = _compact_ede_shadow(engine, snapshot)
    context = snapshot.get("ede_causal_context")
    if isinstance(context, dict):
        _normalize_ede_maturity_language(context)
        context["prospective_shadow"] = shadow
        lines = context.get("context_lines_ru")
        selected = shadow.get("selected_candidate") or {}
        if isinstance(lines, list) and selected:
            n = int((selected.get("all_prospective") or {}).get("n") or 0)
            lines.append(
                f"Prospective shadow {selected.get('status')}: {n} resolved событий; "
                "это research evidence без trading authority.")
    else:
        snapshot["ede_prospective_shadow"] = shadow
    _impl._enforce_snapshot_budget(snapshot)
    return snapshot