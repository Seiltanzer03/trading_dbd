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
