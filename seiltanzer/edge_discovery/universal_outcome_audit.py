"""Summary helpers for strategy-agnostic universal outcome coverage."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .universal_outcomes import UNIVERSAL_OUTCOME_CONTRACT_VERSION


AUDIT_VERSION = "g1s-universal-outcome-coverage-audit-v1"


def summarize_universal_outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[int, dict[str, Any]] = {}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["horizon_minutes"])].append(row)
    for horizon, items in sorted(grouped.items()):
        resolved = [item for item in items if item.get("outcome_available")]
        available = [
            item for item in resolved
            if isinstance(item.get("universal_outcome"), dict)
            and item["universal_outcome"].get("available")
        ]
        complete = [item for item in available if item["universal_outcome"].get("path_complete")]
        clean_barrier = Counter()
        all_barrier = Counter()
        for item in available:
            for barrier_id, label in (item["universal_outcome"].get("barriers") or {}).items():
                all_barrier[f"{barrier_id}:{label.get('label')}"] += 1
                if label.get("clean_label"):
                    clean_barrier[f"{barrier_id}:{label.get('label')}"] += 1
        by_horizon[horizon] = {
            "rows": len(items),
            "resolved_rows": len(resolved),
            "universal_outcome_available": len(available),
            "complete_ohlc_outcomes": len(complete),
            "available_pct_of_resolved": 100.0*len(available)/max(1, len(resolved)),
            "complete_pct_of_available": 100.0*len(complete)/max(1, len(available)),
            "all_barrier_labels": dict(sorted(all_barrier.items())),
            "clean_barrier_labels": dict(sorted(clean_barrier.items())),
        }
    reasons = Counter(
        str(row.get("universal_outcome_reason") or "AVAILABLE") for row in rows
    )
    return {
        "audit_version": AUDIT_VERSION,
        "outcome_contract_version": UNIVERSAL_OUTCOME_CONTRACT_VERSION,
        "rows": len(rows),
        "by_horizon": {str(key): value for key, value in by_horizon.items()},
        "availability_reasons": dict(sorted(reasons.items())),
        "strategy_agnostic": True,
        "production_authority": False,
        "auto_promotion": False,
    }
