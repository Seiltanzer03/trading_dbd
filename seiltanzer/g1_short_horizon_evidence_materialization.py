"""Bounded presentation cache for expensive G.1S evidence reports.

Immutable ledgers remain authoritative. Full-history OOS, ablation, trade and
economic scans run only on the low-priority research worker, never because a
browser requested an endpoint. HTTP reads the latest frozen JSON snapshot and
exposes BUILDING when a snapshot does not exist yet.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from .g1_short_horizon_runtime import ShortHorizonRuntime


EVIDENCE_MATERIALIZATION_VERSION = "g1s-evidence-materialization-v1"
EVIDENCE_MIN_REFRESH_SEC = 5 * 60.0
EVIDENCE_MAX_STALE_SEC = 20 * 60.0
REPORT_NAMES = (
    "probability_oos", "continuous_oos", "calibration_oos",
    "ablation", "trade_relevance", "final_report",
)
_SOURCE_TABLES = (
    "g1s_resolutions", "g1s_shadow_predictions", "g1s_return_predictions",
    "g1s_calibrated_predictions", "g1s_trade_links", "g1m_local_outcomes",
    "g1_q_capture_attempts", "passive_market_observations",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _ensure_table(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_evidence_materializations(
                report_name TEXT PRIMARY KEY,
                source_signature TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                generated_ts REAL NOT NULL,
                duration_ms REAL NOT NULL,
                contract_version TEXT NOT NULL
            )""")


def _table_max_rowid(runtime: ShortHorizonRuntime, table: str) -> int:
    try:
        with runtime._lock:
            row = runtime._conn.execute(
                f"SELECT COALESCE(MAX(rowid),0) FROM {table}").fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0


def _source_signature(runtime: ShortHorizonRuntime) -> str:
    return "|".join(f"{table}:{_table_max_rowid(runtime, table)}" for table in _SOURCE_TABLES)


def _read(runtime: ShortHorizonRuntime, name: str) -> dict[str, Any] | None:
    _ensure_table(runtime)
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT * FROM g1s_evidence_materializations WHERE report_name=?", (str(name),)
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["payload_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    payload["materialization"] = {
        "contract_version": str(row["contract_version"]),
        "report_name": str(row["report_name"]),
        "source_signature": str(row["source_signature"]),
        "generated_ts": float(row["generated_ts"]),
        "age_sec": max(0.0, time.time()-float(row["generated_ts"])),
        "duration_ms": float(row["duration_ms"]),
        "request_time_full_history_scan": False,
    }
    return payload


def _building(name: str) -> dict[str, Any]:
    return {
        "status": "BUILDING",
        "evidence_status": "INSUFFICIENT",
        "report_name": name,
        "materialized": False,
        "materialization_contract_version": EVIDENCE_MATERIALIZATION_VERSION,
        "request_time_full_history_scan": False,
        "production_authority": False,
        "edge_claim_allowed": False,
    }


def materialized_report(runtime: ShortHorizonRuntime, name: str) -> dict[str, Any]:
    if name not in REPORT_NAMES:
        raise ValueError(f"unknown G1S materialized report: {name}")
    return _read(runtime, name) or _building(name)


def _writers(runtime: ShortHorizonRuntime) -> tuple[tuple[str, Callable[[], dict[str, Any]]], ...]:
    writers: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("probability_oos", runtime.prospective_oos),
        ("continuous_oos", runtime.continuous_oos),
        ("calibration_oos", runtime.calibration_oos),
        ("ablation", runtime.ablation),
        ("trade_relevance", runtime.trade_relevance),
    ]
    if hasattr(runtime, "final_report"):
        writers.append(("final_report", runtime.final_report))
    return tuple(writers)


def materialize_evidence_reports(
    runtime: ShortHorizonRuntime, *, force: bool = False,
) -> dict[str, Any]:
    """Refresh expensive snapshots from the research worker only."""
    _ensure_table(runtime)
    now = time.time()
    signature = _source_signature(runtime)
    with runtime._lock:
        existing = {
            str(row["report_name"]): dict(row)
            for row in runtime._conn.execute(
                "SELECT report_name,source_signature,generated_ts FROM g1s_evidence_materializations"
            ).fetchall()
        }
    names = [name for name, _ in _writers(runtime)]
    if not force and names and all(name in existing for name in names):
        oldest_age = max(0.0, now-min(float(existing[name]["generated_ts"]) for name in names))
        signatures_current = all(str(existing[name]["source_signature"]) == signature for name in names)
        if signatures_current and oldest_age < EVIDENCE_MAX_STALE_SEC:
            return {"refreshed": False, "reason": "SOURCE_UNCHANGED", "age_sec": oldest_age}
        if oldest_age < EVIDENCE_MIN_REFRESH_SEC:
            return {"refreshed": False, "reason": "REFRESH_INTERVAL", "age_sec": oldest_age}

    results: dict[str, Any] = {}
    for name, fn in _writers(runtime):
        started = time.time()
        payload = fn()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{name} did not return object")
        duration_ms = (time.time()-started)*1000.0
        with runtime._lock, runtime._conn:
            runtime._conn.execute("""
                INSERT INTO g1s_evidence_materializations(
                    report_name,source_signature,payload_json,generated_ts,duration_ms,contract_version)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(report_name) DO UPDATE SET
                    source_signature=excluded.source_signature,
                    payload_json=excluded.payload_json,
                    generated_ts=excluded.generated_ts,
                    duration_ms=excluded.duration_ms,
                    contract_version=excluded.contract_version
            """, (name, signature, _json(payload), time.time(), duration_ms,
                  EVIDENCE_MATERIALIZATION_VERSION))
        results[name] = {"duration_ms": duration_ms}
    return {"refreshed": True, "source_signature": signature, "reports": results}


def materialization_status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    _ensure_table(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT report_name,source_signature,generated_ts,duration_ms "
            "FROM g1s_evidence_materializations ORDER BY report_name").fetchall()
    now = time.time()
    return {
        "contract_version": EVIDENCE_MATERIALIZATION_VERSION,
        "min_refresh_sec": EVIDENCE_MIN_REFRESH_SEC,
        "max_stale_sec": EVIDENCE_MAX_STALE_SEC,
        "request_time_full_history_scan": False,
        "reports": [{
            "report_name": str(row["report_name"]),
            "generated_ts": float(row["generated_ts"]),
            "age_sec": max(0.0, now-float(row["generated_ts"])),
            "duration_ms": float(row["duration_ms"]),
            "source_signature": str(row["source_signature"]),
        } for row in rows],
        "production_authority": False,
    }


def install_g1_short_horizon_evidence_materialization() -> None:
    if getattr(ShortHorizonRuntime, "_evidence_materialization_version", None) == EVIDENCE_MATERIALIZATION_VERSION:
        return
    previous_init = ShortHorizonRuntime.__init__

    def init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _ensure_table(self)

    ShortHorizonRuntime.__init__ = init
    ShortHorizonRuntime.materialize_evidence_reports = materialize_evidence_reports
    ShortHorizonRuntime.materialized_evidence_report = materialized_report
    ShortHorizonRuntime.evidence_materialization_status = materialization_status
    ShortHorizonRuntime._evidence_materialization_version = EVIDENCE_MATERIALIZATION_VERSION
