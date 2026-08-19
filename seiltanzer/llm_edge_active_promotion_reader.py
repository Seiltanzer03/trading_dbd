"""Read-only, fail-closed request-path reader for prospectively validated LLM promotions."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


CONTRACT_VERSION = "llm-edge-active-promotion-reader-v1"


def active_promotions_readonly(engine: Any) -> list[dict[str, Any]]:
    """Read durable promotions without DDL, writes, refits, or history scans.

    The worker-side prospective evaluator owns schema creation and promotion
    writes. If the table is not installed yet, is temporarily locked, or has a
    malformed row, the trading request path fails closed and exposes no LLM edge.
    """
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return []
    try:
        with runtime._lock:
            rows = runtime._conn.execute(
                """SELECT candidate_id,payload_json,promoted_ts,promotion_sha256
                   FROM llm_edge_active_promotions
                   ORDER BY promoted_ts,candidate_id"""
            ).fetchall()
    except (sqlite3.Error, AttributeError, TypeError):
        return []

    output: list[dict[str, Any]] = []
    for raw in rows:
        try:
            payload = json.loads(str(raw["payload_json"]))
            if (
                not isinstance(payload, dict)
                or not bool(payload.get("prospective_validated"))
                or not bool(payload.get("eligible_for_active_edge"))
                or str(payload.get("promotion_basis") or "")
                   != "VALIDATED_LIVE_PROSPECTIVE_OOS"
            ):
                continue
            payload["promoted_ts"] = float(raw["promoted_ts"])
            payload["promotion_sha256"] = str(raw["promotion_sha256"])
            output.append(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return output
