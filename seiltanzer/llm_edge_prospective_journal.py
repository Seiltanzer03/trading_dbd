"""Incremental, append-only future-T0 journal for frozen LLM-origin EDE candidates."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from .edge_discovery.filters import FittedCondition
from .edge_discovery.frozen_candidate import predict_structured_frozen
from .edge_discovery.prospective import ProspectiveFeatureAdapter
from .edge_discovery.prospective_confirmation import (
    ProspectiveConfirmationLedger,
    record_registered_prediction,
)
from .edge_discovery.universal_outcome_adapter import (
    ProspectiveUniversalOutcomeAdapter,
    UNIVERSAL_OUTCOME_ADAPTER_VERSION,
)
from .edge_discovery.universal_templates import universal_feature_definitions
from .g1_short_horizon_p2e_segmented_persistence import ASSET_FAMILY_BY_INSTRUMENT, session_utc
from .llm_edge_candidate_lifecycle import active_llm_candidates, registry_for_engine

JOURNAL_CONTRACT_VERSION = "llm-edge-prospective-journal-v1"
CURSOR_NAME = "llm_edge_prospective_t0"
OBSERVATION_BATCH_LIMIT = 250
OUTCOME_BATCH_LIMIT = 250


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def initialize_journal_storage(runtime: Any) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_edge_candidate_opportunities(
                candidate_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                instrument TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                target_ts REAL NOT NULL,
                rule_sha256 TEXT NOT NULL,
                feature_available INTEGER NOT NULL,
                feature_stale INTEGER NOT NULL,
                matched INTEGER,
                reason TEXT NOT NULL,
                prospective_record_id TEXT,
                feature_values_json TEXT NOT NULL,
                created_ts REAL NOT NULL,
                PRIMARY KEY(candidate_id,observation_id)
            )"""
        )
        runtime._conn.execute(
            """CREATE INDEX IF NOT EXISTS ix_llm_edge_opportunities_candidate_t0
               ON llm_edge_candidate_opportunities(candidate_id,captured_ts,observation_id)"""
        )
        runtime._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_edge_candidate_outcomes(
                candidate_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                matched INTEGER NOT NULL,
                outcome_value_json TEXT NOT NULL,
                resolved_ts REAL NOT NULL,
                outcome_adapter_version TEXT NOT NULL,
                created_ts REAL NOT NULL,
                PRIMARY KEY(candidate_id,observation_id)
            )"""
        )
        runtime._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_edge_prospective_cursors(
                cursor_name TEXT PRIMARY KEY,
                last_processed_observation_ts REAL NOT NULL,
                last_processed_observation_id TEXT NOT NULL,
                updated_ts REAL NOT NULL
            )"""
        )
        runtime._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_edge_lifecycle_materialized(
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                payload_json TEXT NOT NULL,
                updated_ts REAL NOT NULL
            )"""
        )
        for table in ("llm_edge_candidate_opportunities", "llm_edge_candidate_outcomes"):
            runtime._conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable llm edge prospective row'); END"""
            )
            runtime._conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable llm edge prospective row'); END"""
            )


def _ledger_path(engine: Any) -> Path:
    override = os.environ.get("SEILTANZER_EDE_PROSPECTIVE_LEDGER", "").strip()
    if override:
        return Path(override)
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research" / "ede_prospective_confirmation.jsonl"


def ledger_for_engine(engine: Any) -> ProspectiveConfirmationLedger:
    path = _ledger_path(engine)
    current = getattr(engine, "_llm_edge_prospective_ledger", None)
    if current is None or Path(getattr(current, "path", "")) != path:
        current = ProspectiveConfirmationLedger(path)
        engine._llm_edge_prospective_ledger = current
    return current


def _cursor(runtime: Any) -> tuple[float, str]:
    with runtime._lock:
        row = runtime._conn.execute(
            """SELECT last_processed_observation_ts,last_processed_observation_id
               FROM llm_edge_prospective_cursors WHERE cursor_name=?""",
            (CURSOR_NAME,),
        ).fetchone()
    return (0.0, "") if row is None else (float(row[0]), str(row[1]))


def _advance_cursor(runtime: Any, captured_ts: float, observation_id: str) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            """INSERT INTO llm_edge_prospective_cursors(
                   cursor_name,last_processed_observation_ts,last_processed_observation_id,updated_ts
               ) VALUES(?,?,?,?)
               ON CONFLICT(cursor_name) DO UPDATE SET
                   last_processed_observation_ts=excluded.last_processed_observation_ts,
                   last_processed_observation_id=excluded.last_processed_observation_id,
                   updated_ts=excluded.updated_ts""",
            (CURSOR_NAME, float(captured_ts), str(observation_id), time.time()),
        )


def _latest(runtime: Any, cutoff: float | None = None) -> tuple[float, str] | None:
    suffix = "" if cutoff is None else "AND captured_ts<=?"
    params: tuple[Any, ...] = () if cutoff is None else (float(cutoff),)
    with runtime._lock:
        row = runtime._conn.execute(
            f"""SELECT captured_ts,observation_id FROM g1s_observations
                WHERE horizon_minutes IN (15,30,60,120,240) {suffix}
                ORDER BY captured_ts DESC,observation_id DESC LIMIT 1""",
            params,
        ).fetchone()
    return None if row is None else (float(row[0]), str(row[1]))


def _align_cursor(runtime: Any, candidates: list[dict[str, Any]]) -> None:
    current = _cursor(runtime)
    if not candidates:
        latest = _latest(runtime)
    else:
        boundary = min(
            float((item.get("validation") or {}).get("oos_start_ts_exclusive") or math.inf)
            for item in candidates
        )
        latest = _latest(runtime, boundary)
    if latest is not None and latest > current:
        _advance_cursor(runtime, *latest)


def _new_observations(runtime: Any, limit: int) -> list[dict[str, Any]]:
    ts, observation_id = _cursor(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            """SELECT observation_id,instrument,captured_ts,target_ts,horizon_minutes,
                      frozen_features_json,created_ts
               FROM g1s_observations
               WHERE horizon_minutes IN (15,30,60,120,240)
                 AND (captured_ts>? OR (captured_ts=? AND observation_id>?))
               ORDER BY captured_ts,observation_id LIMIT ?""",
            (ts, ts, observation_id, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def _conditions(candidate: dict[str, Any]) -> list[FittedCondition]:
    frozen = (candidate.get("validation") or {}).get("frozen_spec") or {}
    raw = (frozen.get("rule") or {}).get("conditions") or frozen.get("conditions") or []
    return [FittedCondition(
        feature_id=str(item["feature_id"]),
        kind=str(item["kind"]),
        state=str(item["state"]),
        lower=item.get("lower"),
        upper=item.get("upper"),
        train_cutoff_ts=item.get("train_cutoff_ts"),
    ) for item in raw]


def _frozen_t0_records(
    runtime: Any, observation: dict[str, Any], feature_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Use existing adapter extractors without historical bar recovery."""
    adapter = ProspectiveFeatureAdapter.__new__(ProspectiveFeatureAdapter)
    adapter.runtime = runtime
    adapter.available_asof = float(observation["captured_ts"])
    adapter.tables = set()
    adapter._causal_bars = {}
    adapter._causal_bar_cache = {}
    values, _rejected, provenance = adapter._feature_values(observation, strict=False)
    records: dict[str, dict[str, Any]] = {}
    for feature_id in feature_ids:
        record = values.get(feature_id)
        if record is not None:
            item = record.as_dict()
            item["provenance"] = (provenance.get(feature_id) or {}).get(
                "provenance", "FROZEN_T0"
            )
            records[feature_id] = item

    if "vol.rv15_over_rv60" in feature_ids:
        left, right = values.get("vol.rv_15m"), values.get("vol.rv_60m")
        good = bool(
            left is not None and right is not None
            and left.availability == right.availability == "AVAILABLE"
            and not left.stale and not right.stale
            and left.value is not None and right.value is not None
            and float(right.value) > 0.0
        )
        records["vol.rv15_over_rv60"] = {
            "feature_id": "vol.rv15_over_rv60",
            "value": float(left.value) / float(right.value) if good else None,
            "availability": "AVAILABLE" if good else "UNAVAILABLE",
            "quality": None,
            "asof": max(float(left.asof), float(right.asof)) if good else None,
            "stale": bool(
                (left is not None and left.stale) or (right is not None and right.stale)
            ),
            "training_eligible": good,
            "provenance": "CAUSAL_DERIVED_FROM_FROZEN_T0",
        }

    t0 = float(observation["captured_ts"])
    instrument = str(observation.get("instrument") or "")
    definitions = {item.feature_id: item for item in universal_feature_definitions()}
    for feature_id, value in {
        "regime.asset": instrument,
        "regime.asset_family": ASSET_FAMILY_BY_INSTRUMENT.get(instrument, "UNKNOWN"),
        "regime.session_utc": session_utc(t0),
    }.items():
        if feature_id in feature_ids:
            definition = definitions.get(feature_id)
            records[feature_id] = {
                "feature_id": feature_id,
                "value": value,
                "availability": "AVAILABLE",
                "quality": 1.0,
                "asof": t0,
                "stale": False,
                "training_eligible": bool(
                    definition is None or definition.training_eligibility
                ),
                "provenance": "CAUSAL_T0_METADATA",
            }
    return records


def _context(
    runtime: Any, candidate: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    t0 = float(observation["captured_ts"])
    required = [item.feature_id for item in _conditions(candidate)]
    records = _frozen_t0_records(runtime, observation, required)
    selected: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    stale: list[str] = []
    future: list[str] = []
    for feature_id in required:
        item = records.get(feature_id)
        if item is None or item.get("availability") != "AVAILABLE" or item.get("value") is None:
            missing.append(feature_id)
            if item and item.get("stale"):
                stale.append(feature_id)
            continue
        asof = item.get("asof")
        if asof is None or float(asof) > t0 + 1e-6:
            future.append(feature_id)
            continue
        if item.get("stale"):
            stale.append(feature_id)
            continue
        selected[feature_id] = {
            "value": item["value"],
            "available": True,
            "availability": "AVAILABLE",
            "stale": False,
            "asof": float(asof),
            "quality": item.get("quality"),
            "provenance": item.get("provenance"),
        }
    available = not (missing or stale or future)
    return {
        "available": available,
        "stale": bool(stale),
        "reason": "AVAILABLE_CONTEXT" if available else "UNAVAILABLE_CONTEXT",
        "missing": sorted(set(missing)),
        "stale_ids": sorted(set(stale)),
        "future_asof_ids": sorted(set(future)),
        "feature_values": selected,
        "ede_features": {key: value["value"] for key, value in selected.items()},
    }


def _insert_opportunity(runtime: Any, values: tuple[Any, ...]) -> bool:
    with runtime._lock, runtime._conn:
        cursor = runtime._conn.execute(
            """INSERT OR IGNORE INTO llm_edge_candidate_opportunities(
                 candidate_id,observation_id,captured_ts,instrument,horizon_minutes,target_ts,
                 rule_sha256,feature_available,feature_stale,matched,reason,
                 prospective_record_id,feature_values_json,created_ts
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
    return bool(cursor.rowcount)


def collect_opportunities(
    engine: Any, *, now: float | None = None, limit: int = OBSERVATION_BATCH_LIMIT,
) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"processed_observations": 0, "reason": "G1S_RUNTIME_UNAVAILABLE"}
    initialize_journal_storage(runtime)
    registry = registry_for_engine(engine)
    candidates = active_llm_candidates(registry)
    _align_cursor(runtime, candidates)
    if not candidates:
        return {
            "processed_observations": 0,
            "active_research_candidates": 0,
            "opportunities_inserted": 0,
            "reason": "NO_FROZEN_RESEARCH_CANDIDATES",
            "production_authority": False,
        }

    ledger = ledger_for_engine(engine)
    current = float(time.time() if now is None else now)
    rows = _new_observations(runtime, limit)
    inserted = matched = unavailable = missed = 0
    for row in rows:
        t0 = float(row["captured_ts"])
        horizon = int(row["horizon_minutes"])
        observation_id = str(row["observation_id"])
        for candidate in candidates:
            validation = candidate.get("validation") or {}
            if horizon != int(candidate.get("horizon_minutes") or 0):
                continue
            if t0 <= float(validation.get("oos_start_ts_exclusive") or math.inf) + 1e-6:
                continue
            frozen = validation.get("frozen_spec") or {}
            try:
                context = _context(runtime, candidate, row)
            except (ValueError, TypeError, KeyError):
                context = {
                    "available": False, "stale": False, "reason": "UNAVAILABLE_CONTEXT",
                    "feature_values": {}, "ede_features": {},
                }
            target_ts = float(row["target_ts"])
            record_id = None
            is_match: bool | None = None
            reason = str(context["reason"])
            if context["available"] and current >= target_ts - 1e-6:
                reason = "MISSED_PREDICTION_WINDOW"
                missed += 1
            elif context["available"]:
                prediction = predict_structured_frozen(
                    frozen,
                    {
                        "instrument": str(row["instrument"]),
                        "captured_ts": t0,
                        "horizon_minutes": horizon,
                        "ede_features": context["ede_features"],
                    },
                )
                is_match = bool(prediction["qualified"])
                reason = "MATCH" if is_match else "NO_MATCH"
                record_id = record_registered_prediction(
                    registry, ledger,
                    candidate_id=str(candidate["candidate_id"]),
                    instrument=str(row["instrument"]),
                    t0=t0,
                    target_ts=target_ts,
                    prediction={
                        "candidate_prediction": prediction["candidate_prediction"],
                        "baseline_prediction": prediction["baseline_prediction"],
                        "target_id": prediction["target_id"],
                        "target_kind": prediction["target_kind"],
                    },
                    qualified=is_match,
                    feature_values=context["feature_values"],
                    recorded_ts=current,
                )
            else:
                unavailable += 1
            if _insert_opportunity(runtime, (
                str(candidate["candidate_id"]), observation_id, t0, str(row["instrument"]),
                horizon, target_ts, str(frozen["rule_sha256"]),
                int(bool(context["available"])), int(bool(context.get("stale"))),
                None if is_match is None else int(is_match), reason, record_id,
                _canonical(context["feature_values"]), current,
            )):
                inserted += 1
                matched += int(is_match is True)
        _advance_cursor(runtime, t0, observation_id)
    return {
        "contract_version": JOURNAL_CONTRACT_VERSION,
        "processed_observations": len(rows),
        "active_research_candidates": len(candidates),
        "opportunities_inserted": inserted,
        "matched_inserted": matched,
        "unavailable_seen": unavailable,
        "missed_prediction_windows": missed,
        "production_authority": False,
        "writes_active_edge_registry": False,
    }


def _pending(runtime: Any, limit: int) -> list[dict[str, Any]]:
    with runtime._lock:
        rows = runtime._conn.execute(
            """SELECT o.*,r.resolved_ts
               FROM llm_edge_candidate_opportunities o
               JOIN g1s_resolutions r ON r.observation_id=o.observation_id
               LEFT JOIN llm_edge_candidate_outcomes x
                 ON x.candidate_id=o.candidate_id AND x.observation_id=o.observation_id
               WHERE x.observation_id IS NULL
                 AND o.feature_available=1 AND o.matched IS NOT NULL
               ORDER BY r.resolved_ts,o.observation_id,o.candidate_id LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def collect_outcomes(
    engine: Any, *, now: float | None = None, limit: int = OUTCOME_BATCH_LIMIT,
) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"outcomes_inserted": 0, "reason": "G1S_RUNTIME_UNAVAILABLE"}
    initialize_journal_storage(runtime)
    pending = _pending(runtime, limit)
    if not pending:
        return {"resolved_seen": 0, "outcomes_inserted": 0, "production_authority": False}

    source_rows = []
    for item in pending:
        with runtime._lock:
            observation = runtime._conn.execute(
                "SELECT * FROM g1s_observations WHERE observation_id=? LIMIT 1",
                (str(item["observation_id"]),),
            ).fetchone()
        if observation is None:
            continue
        row = dict(observation)
        rv = _frozen_t0_records(runtime, row, ["vol.rv_60m"]).get("vol.rv_60m")
        row["ede_features"] = {
            "vol.rv_60m": (
                rv.get("value")
                if rv and rv.get("availability") == "AVAILABLE" and not rv.get("stale")
                else None
            )
        }
        row["outcome_available"] = True
        row["resolved_ts"] = float(item["resolved_ts"])
        source_rows.append(row)

    attached = ProspectiveUniversalOutcomeAdapter(runtime).attach(source_rows)
    outcomes = {str(row["observation_id"]): row.get("universal_outcome") for row in attached}
    ledger = ledger_for_engine(engine)
    registry = registry_for_engine(engine)
    states = {
        str(item["candidate_id"]): registry.current(str(item["candidate_id"])) for item in pending
    }
    created_ts = float(time.time() if now is None else now)
    inserted = 0
    for item in pending:
        outcome = outcomes.get(str(item["observation_id"]))
        if outcome is None:
            continue
        record_id = item.get("prospective_record_id")
        if record_id:
            try:
                ledger.resolve(
                    str(record_id), outcome=outcome, observed_ts=float(item["resolved_ts"])
                )
            except ValueError as exc:
                if "already resolved" not in str(exc):
                    raise
        state = states.get(str(item["candidate_id"])) or {}
        with runtime._lock, runtime._conn:
            cursor = runtime._conn.execute(
                """INSERT OR IGNORE INTO llm_edge_candidate_outcomes(
                     candidate_id,observation_id,target_id,horizon_minutes,matched,
                     outcome_value_json,resolved_ts,outcome_adapter_version,created_ts
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    str(item["candidate_id"]), str(item["observation_id"]),
                    str(state.get("target_id") or ""), int(item["horizon_minutes"]),
                    int(item["matched"]), _canonical(outcome), float(item["resolved_ts"]),
                    str(outcome.get("adapter_version") or UNIVERSAL_OUTCOME_ADAPTER_VERSION),
                    created_ts,
                ),
            )
        inserted += int(bool(cursor.rowcount))
    return {
        "contract_version": JOURNAL_CONTRACT_VERSION,
        "resolved_seen": len(pending),
        "outcomes_inserted": inserted,
        "production_authority": False,
        "writes_active_edge_registry": False,
    }
