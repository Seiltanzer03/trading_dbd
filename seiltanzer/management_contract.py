"""Small decision-critical contracts shared by full and compact snapshots."""
from __future__ import annotations

_LEVELS = {"высокая": 2, "high": 2, "средняя": 1, "medium": 1, "низкая": 0, "low": 0}
_NAMES = {2: "высокая", 1: "средняя", 0: "низкая"}


def decision_reliability(snapshot: dict) -> dict:
    """Use the most restrictive published level; missing never means trusted."""
    manager = snapshot.get("policy_manager") or {}
    coverage = snapshot.get("metric_coverage") or manager.get("metric_coverage") or {}
    candidates = [manager.get("decision_reliability"),
        ((manager.get("evidence") or {}).get("data_quality") or {}).get("reliability"),
        (coverage.get("summary") or coverage).get("reliability"),
        (snapshot.get("data_quality") or {}).get("reliability"),
        (manager.get("gate") or {}).get("data_reliability"),
        (snapshot.get("report_integrity") or {}).get("decision_reliability")]
    rows = []
    for item in candidates:
        row = item if isinstance(item, dict) else {"level": item}
        level = str(row.get("level") or "").lower()
        if level in _LEVELS:
            rows.append((_LEVELS[level], row))
    if not rows:
        return {"level": "UNAVAILABLE", "available": False,
                "reasons": ["Уровень надёжности снимка отсутствует; активный допуск запрещён"]}
    rank = min(x[0] for x in rows)
    reasons = []
    for _, row in rows:
        for reason in (row.get("reasons") if isinstance(row.get("reasons"), list) else []):
            if isinstance(reason, str) and reason not in reasons:
                reasons.append(reason[:256])
    return {"level": _NAMES[rank], "available": True, "reasons": reasons[:8]}



def calculation_audit(snapshot: dict) -> dict:
    manager = snapshot.get("policy_manager") or {}
    keys = ("expected_final_r_net", "cvar10_r_net", "gross_expected_final_r",
            "execution_cost_r", "outcomes_include_execution_costs")
    rows = {name: {key: row[key] for key in keys if key in row}
            for name, row in (manager.get("policies") or {}).items() if isinstance(row, dict)}
    available = all(row.get("outcomes_include_execution_costs") is True
                    for row in rows.values()) and len(rows) == 5
    return {"version": "management-calculation-audit-v1",
        "status": "AVAILABLE" if available else "UNAVAILABLE",
        "data_reliability": decision_reliability(snapshot),
        "model_current_r": (manager.get("inputs") or {}).get("r0"),
        "execution_cost_model": manager.get("execution_cost_model") or {},
        "policies": rows}
