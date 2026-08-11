"""Phase G.1A prospective dataset contract and immutable research cuts.

This layer deliberately does *not* fit calibrators. It converts resolved F.3.2a
market observations into deterministic research membership records, cohort
identities, conservative dependency/effective-N accounting, and immutable
cutoff-safe dataset manifests for later G.1B-G.1D consumers.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from typing import Any, Iterable

from . import passive_learning as _pl
from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION, finite, valid_terminal_cdf
from .option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION

G1_DATASET_CONTRACT_VERSION = "g1-prospective-dataset-v1"
G1_COHORT_CONTRACT_VERSION = "g1-cohort-v1"
G1_HORIZON_BUCKET_CONTRACT_VERSION = "g1-horizon-buckets-v1"
G1_EFFECTIVE_N_CONTRACT_VERSION = "effective-n-nonoverlap-v1"
G1_CUT_MANIFEST_VERSION = "g1-dataset-cut-manifest-v1"
G1_STAGE = "G.1A"
TERMINAL_MAX_AGE_SEC = 65.0

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_INIT = _ENGINE.__init__
_ORIGINAL_RESOLVE_DUE = _ENGINE.resolve_due

_EXCLUSION_PRIORITY = (
    "SOURCE_MUTATED",
    "WRONG_SOURCE_SCHEMA",
    "WRONG_MEASUREMENT_RUNTIME",
    "NOT_BACKGROUND_COLLECTOR",
    "RETROSPECTIVE_REPLAY",
    "EVIDENCE_INELIGIBLE",
    "UNRESOLVED",
    "INVALID_TIME_CONTRACT",
    "NON_DIRECT_T0_PRICE",
    "INVALID_T0_PRICE",
    "TERMINAL_NOT_CLEAN",
    "TERMINAL_NOT_AUTHORITATIVE",
    "TERMINAL_LOOKAHEAD",
    "TERMINAL_TOO_OLD",
    "INVALID_TERMINAL_PRICE",
    "Q_SEMANTIC_UNAVAILABLE",
    "Q_DISTRIBUTION_UNAVAILABLE",
    "INVALID_FROZEN_Q_CDF",
    "PIT_MISSING",
    "HORIZON_SEMANTIC_MISMATCH",
    "EXPIRY_CONTRACT_MISMATCH",
    "PROXY_RELATION_UNKNOWN",
    "PROXY_TRANSFORM_UNKNOWN",
    "DEMO_DATA",
    "SYNTHETIC_DATA",
    "CONTRACT_ERROR",
)

_ERROR_TYPES = {
    "dataset_contract_error_n": "DATASET_CONTRACT",
    "dataset_membership_error_n": "DATASET_MEMBERSHIP",
    "dataset_cut_error_n": "DATASET_CUT",
    "source_mutation_error_n": "SOURCE_MUTATED",
    "cohort_contract_error_n": "COHORT_CONTRACT",
    "dependency_contract_error_n": "DEPENDENCY_CONTRACT",
    "q_eligibility_error_n": "Q_ELIGIBILITY",
}


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _source_record_payload(row: dict) -> dict:
    """Canonical post-resolution research record used for mutation detection."""
    return {
        "observation_id": row.get("observation_id"),
        "anchor_group_id": row.get("anchor_group_id"),
        "captured_ts": finite(row.get("captured_ts")),
        "target_ts": finite(row.get("target_ts")),
        "resolved_ts": finite(row.get("resolved_ts")),
        "instrument": row.get("instrument"),
        "horizon_minutes": row.get("horizon_minutes"),
        "trigger_reason": row.get("trigger_reason"),
        "market_price": finite(row.get("market_price")),
        "price_source": row.get("price_source"),
        "price_age_sec": finite(row.get("price_age_sec")),
        "price_quality": finite(row.get("price_quality")),
        "price_kind": row.get("price_kind"),
        "option_source": row.get("option_source"),
        "option_age_sec": finite(row.get("option_age_sec")),
        "option_quality": finite(row.get("option_quality")),
        "option_kind": row.get("option_kind"),
        "market_regime": row.get("market_regime"),
        "session": row.get("session"),
        "feature_contract_version": row.get("feature_contract_version"),
        "forecast_model_version": row.get("forecast_model_version"),
        "calibrator_version": row.get("calibrator_version"),
        "scenario_version": row.get("scenario_version"),
        "features": _loads(row.get("features_json"), {}),
        "forecast": _loads(row.get("forecast_json"), {}),
        "evidence_eligible": int(row.get("evidence_eligible") or 0),
        "resolution_status": row.get("resolution_status"),
        "outcome": _loads(row.get("outcome_json"), None),
        "calendar_elapsed": finite(row.get("calendar_elapsed")),
        "trading_elapsed": finite(row.get("trading_elapsed")),
        "market_open_fraction": finite(row.get("market_open_fraction")),
        "retrospective_replay": int(row.get("retrospective_replay") or 0),
        "observation_origin": row.get("observation_origin"),
    }


def _source_record_sha256(row: dict) -> str:
    return _sha256(_source_record_payload(row))


def _horizon_bucket(row: dict, forecast: dict) -> str:
    kind = str(forecast.get("horizon_kind") or "fixed_trading_time")
    if kind != "option_native_expiry":
        minutes = int(row.get("horizon_minutes") or 0)
        return f"{minutes}m"
    captured = finite(row.get("captured_ts"))
    target = finite(row.get("target_ts"))
    if captured is None or target is None or target <= captured:
        return "INVALID_DTE"
    dte = (target - captured) / 86400.0
    if dte < 1.0:
        return "LT_1D"
    if dte < 3.0:
        return "1D_3D"
    if dte < 7.0:
        return "3D_7D"
    if dte < 14.0:
        return "7D_14D"
    if dte < 30.0:
        return "14D_30D"
    return "GE_30D"


def _forecast_family(forecast: dict) -> str:
    if (
        forecast.get("horizon_kind") == "option_native_expiry"
        and forecast.get("probability_measure") == "risk_neutral_Q_terminal"
        and forecast.get("q_terminal_distribution_available") is True
    ):
        return "TERMINAL_Q_DISTRIBUTION"
    return "FIXED_HORIZON_MARKET_FORECAST"


def _cohort_contract(row: dict, forecast: dict) -> dict:
    source = forecast.get("q_source_instrument") or forecast.get("proxy_symbol")
    target = forecast.get("q_target_instrument") or row.get("instrument")
    relation = None
    if source and target:
        relation = "self" if str(source) == str(target) else "proxy"
    family = _forecast_family(forecast)
    return {
        "cohort_contract_version": G1_COHORT_CONTRACT_VERSION,
        "horizon_bucket_contract_version": G1_HORIZON_BUCKET_CONTRACT_VERSION,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "measurement_runtime_version": forecast.get("measurement_runtime_contract"),
        "instrument": row.get("instrument"),
        "forecast_family": family,
        "horizon_kind": forecast.get("horizon_kind") or "fixed_trading_time",
        "horizon_bucket": _horizon_bucket(row, forecast),
        "probability_measure": forecast.get("probability_measure") or "unavailable",
        "q_source_instrument": source,
        "q_target_instrument": target,
        "q_relation": relation,
        "proxy_transform": forecast.get("proxy_transform"),
        "q_source_contract": forecast.get("q_source_contract"),
        "option_q_contract_version": OPTION_Q_CONTRACT_VERSION,
        "expiry_clock_version": forecast.get("expiry_clock_version"),
    }


def _ordered_reasons(reasons: Iterable[str]) -> list[str]:
    unique = set(reasons)
    ordered = [name for name in _EXCLUSION_PRIORITY if name in unique]
    ordered.extend(sorted(unique - set(ordered)))
    return ordered


def _evaluate_row(row: dict) -> dict:
    """Pure eligibility function: frozen row + static versioned contracts only."""
    forecast = _loads(row.get("forecast_json"), {})
    outcome = _loads(row.get("outcome_json"), {})
    features = _loads(row.get("features_json"), {})
    terminal = outcome.get("terminal") if isinstance(outcome, dict) else None
    terminal = terminal if isinstance(terminal, dict) else {}

    base_reasons: list[str] = []
    if row.get("feature_contract_version") != _pl.PASSIVE_SCHEMA_VERSION:
        base_reasons.append("WRONG_SOURCE_SCHEMA")
    runtime = forecast.get("measurement_runtime_contract") or features.get(
        "measurement_runtime_contract"
    )
    if runtime != MEASUREMENT_RUNTIME_VERSION:
        base_reasons.append("WRONG_MEASUREMENT_RUNTIME")
    if row.get("observation_origin") != "background_collector":
        base_reasons.append("NOT_BACKGROUND_COLLECTOR")
    if int(row.get("retrospective_replay") or 0) != 0:
        base_reasons.append("RETROSPECTIVE_REPLAY")
    if int(row.get("evidence_eligible") or 0) != 1:
        base_reasons.append("EVIDENCE_INELIGIBLE")
    if row.get("resolution_status") != "resolved":
        base_reasons.append("UNRESOLVED")

    captured = finite(row.get("captured_ts"))
    target = finite(row.get("target_ts"))
    if captured is None or target is None or target <= captured:
        base_reasons.append("INVALID_TIME_CONTRACT")
    if str(row.get("price_kind") or "").lower() != "direct":
        base_reasons.append("NON_DIRECT_T0_PRICE")
    market_price = finite(row.get("market_price"))
    if market_price is None or market_price <= 0:
        base_reasons.append("INVALID_T0_PRICE")

    if terminal.get("clean_label") is not True:
        base_reasons.append("TERMINAL_NOT_CLEAN")
    if terminal.get("terminal_authoritative") is not True:
        base_reasons.append("TERMINAL_NOT_AUTHORITATIVE")
    if terminal.get("terminal_lookahead_used") is not False:
        base_reasons.append("TERMINAL_LOOKAHEAD")
    terminal_price = finite(terminal.get("terminal_price"))
    terminal_ts = finite(terminal.get("terminal_price_ts"))
    terminal_age = finite(terminal.get("terminal_age_to_target_sec"))
    if terminal_price is None or terminal_price <= 0:
        base_reasons.append("INVALID_TERMINAL_PRICE")
    if target is None or terminal_ts is None or terminal_ts > target + 1e-6:
        base_reasons.append("INVALID_TIME_CONTRACT")
    if terminal_age is None or terminal_age < -1e-6 or terminal_age > TERMINAL_MAX_AGE_SEC:
        base_reasons.append("TERMINAL_TOO_OLD")
    future_log_return = finite(outcome.get("future_log_return")) if isinstance(outcome, dict) else None
    if future_log_return is None:
        base_reasons.append("CONTRACT_ERROR")

    base_reasons = _ordered_reasons(base_reasons)
    forecast_eval_eligible = not base_reasons

    q_reasons = list(base_reasons)
    if forecast.get("probability_measure") != "risk_neutral_Q_terminal":
        q_reasons.append("Q_SEMANTIC_UNAVAILABLE")
    if forecast.get("q_terminal_distribution_available") is not True:
        q_reasons.append("Q_DISTRIBUTION_UNAVAILABLE")
    if not valid_terminal_cdf(forecast.get("terminal_q_cdf")):
        q_reasons.append("INVALID_FROZEN_Q_CDF")
    pit = finite(terminal.get("terminal_pit_q"))
    if pit is None or pit < -1e-9 or pit > 1.0 + 1e-9:
        q_reasons.append("PIT_MISSING")
    if forecast.get("horizon_kind") != "option_native_expiry":
        q_reasons.append("HORIZON_SEMANTIC_MISMATCH")
    expiry = finite(forecast.get("source_expiry_ts_utc"))
    if target is None or expiry is None or abs(target - expiry) > 1.0:
        q_reasons.append("EXPIRY_CONTRACT_MISMATCH")
    calendar_ttm = finite(forecast.get("calendar_ttm_seconds"))
    if (
        captured is None or target is None or calendar_ttm is None
        or abs(calendar_ttm - (target - captured)) > 1.0
    ):
        q_reasons.append("EXPIRY_CONTRACT_MISMATCH")
    source = forecast.get("q_source_instrument") or forecast.get("proxy_symbol")
    q_target = forecast.get("q_target_instrument")
    if not source or not q_target or str(q_target) != str(row.get("instrument")):
        q_reasons.append("PROXY_RELATION_UNKNOWN")
    transform = str(forecast.get("proxy_transform") or "").lower()
    if transform not in {"direct", "inverse"}:
        q_reasons.append("PROXY_TRANSFORM_UNKNOWN")
    if forecast.get("q_source_contract") != OPTION_Q_CONTRACT_VERSION:
        q_reasons.append("CONTRACT_ERROR")
    if forecast.get("expiry_clock_version") != EXPIRY_CLOCK_VERSION:
        q_reasons.append("EXPIRY_CONTRACT_MISMATCH")

    q_reasons = _ordered_reasons(q_reasons)
    q_to_p_eligible = forecast_eval_eligible and not q_reasons
    cohort = _cohort_contract(row, forecast)
    cohort_id = _sha256(cohort)
    all_reasons = base_reasons if base_reasons else ([] if q_to_p_eligible else q_reasons)
    primary = all_reasons[0] if all_reasons else None
    return {
        "forecast_eval_eligible": forecast_eval_eligible,
        "q_to_p_eligible": q_to_p_eligible,
        "terminal_q_eligible": q_to_p_eligible,
        "first_touch_q_eligible": False,
        "forecast_family": cohort["forecast_family"],
        "base_cohort_id": cohort_id,
        "base_cohort": cohort,
        "regime_stratum": row.get("market_regime") or "UNCLASSIFIED",
        "session_stratum": row.get("session") or "UNKNOWN",
        "dependency_group_id": row.get("anchor_group_id") or row.get("observation_id"),
        "base_exclusion_reasons": base_reasons,
        "q_exclusion_reasons": q_reasons if not q_to_p_eligible else [],
        "all_reasons": all_reasons,
        "primary_reason": primary,
    }


def _ensure_g1_tables(self: _ENGINE) -> None:
    with self._lock, self._conn:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1_dataset_membership (
                observation_id TEXT NOT NULL,
                dataset_contract_version TEXT NOT NULL,
                evaluated_ts REAL NOT NULL,
                source_record_sha256 TEXT NOT NULL,
                forecast_eval_eligible INTEGER NOT NULL,
                q_to_p_eligible INTEGER NOT NULL,
                terminal_q_eligible INTEGER NOT NULL,
                first_touch_q_eligible INTEGER NOT NULL,
                forecast_family TEXT NOT NULL,
                base_cohort_id TEXT NOT NULL,
                base_cohort_json TEXT NOT NULL,
                regime_stratum TEXT NOT NULL,
                session_stratum TEXT NOT NULL,
                dependency_group_id TEXT NOT NULL,
                exclusion_reasons_json TEXT NOT NULL,
                q_exclusion_reasons_json TEXT NOT NULL,
                primary_reason TEXT,
                source_feature_contract_version TEXT,
                measurement_runtime_contract TEXT,
                forecast_model_version TEXT,
                option_q_contract_version TEXT,
                variance_clock_version TEXT,
                expiry_clock_version TEXT,
                created_ts REAL NOT NULL,
                PRIMARY KEY(observation_id, dataset_contract_version)
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_membership_eligible "
            "ON g1_dataset_membership(dataset_contract_version,forecast_eval_eligible,q_to_p_eligible)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_membership_cohort "
            "ON g1_dataset_membership(dataset_contract_version,base_cohort_id)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1_contract_errors (
                error_id TEXT PRIMARY KEY,
                error_type TEXT NOT NULL,
                observation_id TEXT,
                dataset_contract_version TEXT NOT NULL,
                expected_sha256 TEXT,
                actual_sha256 TEXT,
                detail TEXT,
                created_ts REAL NOT NULL
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_errors_type "
            "ON g1_contract_errors(dataset_contract_version,error_type)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1_dataset_cuts (
                cut_id TEXT PRIMARY KEY,
                dataset_contract_version TEXT NOT NULL,
                cutoff_ts REAL NOT NULL,
                created_ts REAL NOT NULL,
                manifest_sha256 TEXT NOT NULL UNIQUE,
                manifest_json TEXT NOT NULL,
                raw_task_membership_n INTEGER NOT NULL,
                unique_observation_n INTEGER NOT NULL,
                unique_anchor_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                cohort_count INTEGER NOT NULL,
                q_eligible_n INTEGER NOT NULL,
                status TEXT NOT NULL
            )""")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1_dataset_cut_members (
                cut_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                source_record_sha256 TEXT NOT NULL,
                forecast_family TEXT NOT NULL,
                cohort_id TEXT NOT NULL,
                dependency_group_id TEXT NOT NULL,
                forecast_eval_eligible INTEGER NOT NULL,
                q_to_p_eligible INTEGER NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY(cut_id, observation_id),
                FOREIGN KEY(cut_id) REFERENCES g1_dataset_cuts(cut_id)
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_cut_members_cut "
            "ON g1_dataset_cut_members(cut_id,cohort_id)"
        )
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_membership_immutable_update
            BEFORE UPDATE ON g1_dataset_membership
            BEGIN SELECT RAISE(ABORT,'immutable G1 dataset membership'); END""")
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_membership_immutable_delete
            BEFORE DELETE ON g1_dataset_membership
            BEGIN SELECT RAISE(ABORT,'immutable G1 dataset membership'); END""")
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_cut_immutable_update
            BEFORE UPDATE ON g1_dataset_cuts
            BEGIN SELECT RAISE(ABORT,'immutable G1 dataset cut'); END""")
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_cut_immutable_delete
            BEFORE DELETE ON g1_dataset_cuts
            BEGIN SELECT RAISE(ABORT,'immutable G1 dataset cut'); END""")
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_cut_member_immutable_update
            BEFORE UPDATE ON g1_dataset_cut_members
            BEGIN SELECT RAISE(ABORT,'immutable G1 dataset cut member'); END""")
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_cut_member_immutable_delete
            BEFORE DELETE ON g1_dataset_cut_members
            BEGIN SELECT RAISE(ABORT,'immutable G1 dataset cut member'); END""")


def _record_error(
    self: _ENGINE,
    error_type: str,
    *,
    observation_id: str | None = None,
    expected_sha256: str | None = None,
    actual_sha256: str | None = None,
    detail: str | None = None,
) -> None:
    key = {
        "type": error_type,
        "observation_id": observation_id,
        "contract": G1_DATASET_CONTRACT_VERSION,
        "expected": expected_sha256,
        "actual": actual_sha256,
        "detail": detail,
    }
    error_id = _sha256(key)
    with self._lock, self._conn:
        self._conn.execute(
            "INSERT OR IGNORE INTO g1_contract_errors("
            "error_id,error_type,observation_id,dataset_contract_version,"
            "expected_sha256,actual_sha256,detail,created_ts) VALUES(?,?,?,?,?,?,?,?)",
            (
                error_id, error_type, observation_id, G1_DATASET_CONTRACT_VERSION,
                expected_sha256, actual_sha256, detail, time.time(),
            ),
        )


def _sync_membership(self: _ENGINE, limit: int = 500) -> dict:
    _ensure_g1_tables(self)
    limit = max(1, min(5000, int(limit)))
    with self._lock:
        rows = [dict(r) for r in self._conn.execute(
            "SELECT p.* FROM passive_market_observations p "
            "LEFT JOIN g1_dataset_membership g ON g.observation_id=p.observation_id "
            "AND g.dataset_contract_version=? "
            "WHERE p.resolution_status!='pending' AND g.observation_id IS NULL "
            "ORDER BY p.resolved_ts,p.captured_ts LIMIT ?",
            (G1_DATASET_CONTRACT_VERSION, limit),
        ).fetchall()]
    inserted = 0
    for row in rows:
        try:
            decision = _evaluate_row(row)
            forecast = _loads(row.get("forecast_json"), {})
            source_hash = _source_record_sha256(row)
            now = time.time()
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO g1_dataset_membership("
                    "observation_id,dataset_contract_version,evaluated_ts,source_record_sha256,"
                    "forecast_eval_eligible,q_to_p_eligible,terminal_q_eligible,first_touch_q_eligible,"
                    "forecast_family,base_cohort_id,base_cohort_json,regime_stratum,session_stratum,"
                    "dependency_group_id,exclusion_reasons_json,q_exclusion_reasons_json,primary_reason,"
                    "source_feature_contract_version,measurement_runtime_contract,forecast_model_version,"
                    "option_q_contract_version,variance_clock_version,expiry_clock_version,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["observation_id"], G1_DATASET_CONTRACT_VERSION, now, source_hash,
                        int(decision["forecast_eval_eligible"]), int(decision["q_to_p_eligible"]),
                        int(decision["terminal_q_eligible"]), int(decision["first_touch_q_eligible"]),
                        decision["forecast_family"], decision["base_cohort_id"],
                        _json(decision["base_cohort"]), decision["regime_stratum"],
                        decision["session_stratum"], str(decision["dependency_group_id"]),
                        _json(decision["all_reasons"]), _json(decision["q_exclusion_reasons"]),
                        decision["primary_reason"], row.get("feature_contract_version"),
                        forecast.get("measurement_runtime_contract"), row.get("forecast_model_version"),
                        forecast.get("q_source_contract"), forecast.get("variance_clock_version"),
                        forecast.get("expiry_clock_version"), now,
                    ),
                )
            inserted += int(cursor.rowcount > 0)
        except Exception as exc:
            _record_error(
                self, "DATASET_MEMBERSHIP", observation_id=row.get("observation_id"),
                detail=f"{type(exc).__name__}: {str(exc)[:180]}",
            )
    mutation_n = _detect_source_mutations(self, limit=limit)
    return {"inserted": inserted, "source_mutation_n": mutation_n}


def _detect_source_mutations(self: _ENGINE, limit: int = 5000) -> int:
    _ensure_g1_tables(self)
    with self._lock:
        rows = [dict(r) for r in self._conn.execute(
            "SELECT p.*,g.source_record_sha256 AS expected_source_sha256 "
            "FROM g1_dataset_membership g JOIN passive_market_observations p "
            "ON p.observation_id=g.observation_id "
            "WHERE g.dataset_contract_version=? ORDER BY p.captured_ts LIMIT ?",
            (G1_DATASET_CONTRACT_VERSION, max(1, min(20000, int(limit)))),
        ).fetchall()]
    mismatches = 0
    for row in rows:
        actual = _source_record_sha256(row)
        expected = str(row.get("expected_source_sha256") or "")
        if actual != expected:
            mismatches += 1
            _record_error(
                self, "SOURCE_MUTATED", observation_id=row.get("observation_id"),
                expected_sha256=expected, actual_sha256=actual,
                detail="resolved source record changed after G1A admission",
            )
    return mismatches


def _mutated_observation_ids(self: _ENGINE) -> set[str]:
    with self._lock:
        return {str(r[0]) for r in self._conn.execute(
            "SELECT DISTINCT observation_id FROM g1_contract_errors "
            "WHERE dataset_contract_version=? AND error_type='SOURCE_MUTATED' "
            "AND observation_id IS NOT NULL",
            (G1_DATASET_CONTRACT_VERSION,),
        ).fetchall()}


def _eligible_rows(self: _ENGINE, *, cutoff_ts: float | None = None) -> list[dict]:
    _sync_membership(self, limit=1000)
    args: list[Any] = [G1_DATASET_CONTRACT_VERSION]
    cutoff_clause = ""
    if cutoff_ts is not None:
        cutoff_clause = " AND p.captured_ts<=? AND p.resolved_ts<=?"
        args.extend([float(cutoff_ts), float(cutoff_ts)])
    with self._lock:
        rows = [dict(r) for r in self._conn.execute(
            "SELECT p.observation_id,p.anchor_group_id,p.captured_ts,p.target_ts,p.resolved_ts,"
            "p.instrument,p.horizon_minutes,p.market_regime,p.session,p.forecast_json,"
            "g.source_record_sha256,g.forecast_eval_eligible,g.q_to_p_eligible,"
            "g.terminal_q_eligible,g.first_touch_q_eligible,g.forecast_family,g.base_cohort_id,"
            "g.base_cohort_json,g.regime_stratum,g.session_stratum,g.dependency_group_id "
            "FROM g1_dataset_membership g JOIN passive_market_observations p "
            "ON p.observation_id=g.observation_id "
            "WHERE g.dataset_contract_version=? AND g.forecast_eval_eligible=1"
            + cutoff_clause + " ORDER BY p.captured_ts,p.observation_id",
            tuple(args),
        ).fetchall()]
    mutated = _mutated_observation_ids(self)
    return [row for row in rows if str(row["observation_id"]) not in mutated]


def _anchor_intervals(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("instrument")), str(row.get("dependency_group_id")))].append(row)
    out = []
    for (instrument, dependency_group_id), members in grouped.items():
        starts = [float(r["captured_ts"]) for r in members]
        ends = [float(r["target_ts"]) for r in members]
        out.append({
            "instrument": instrument,
            "dependency_group_id": dependency_group_id,
            "captured_ts": min(starts),
            "target_ts": max(ends),
            "member_n": len(members),
        })
    return out


def _effective_n_nonoverlap(rows: list[dict], *, aggregate: bool = False) -> int:
    """Conservative deterministic interval scheduling.

    Cohort mode deduplicates anchors within each cohort. Aggregate mode additionally
    collapses all horizons/families from the same instrument+T0 dependency group so
    seven horizons cannot inflate system-level evidence.
    """
    if not rows:
        return 0
    if aggregate:
        buckets = {"__aggregate__": rows}
    else:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            buckets[str(row.get("base_cohort_id"))].append(row)
    total = 0
    for members in buckets.values():
        by_instrument: dict[str, list[dict]] = defaultdict(list)
        for interval in _anchor_intervals(members):
            by_instrument[interval["instrument"]].append(interval)
        for intervals in by_instrument.values():
            last_end = -math.inf
            for interval in sorted(
                intervals,
                key=lambda x: (float(x["captured_ts"]), float(x["target_ts"]), x["dependency_group_id"]),
            ):
                if float(interval["captured_ts"]) >= last_end - 1e-9:
                    total += 1
                    last_end = float(interval["target_ts"])
    return total


def _evidence_status(effective_n: int, span_days: float) -> str:
    if effective_n < 30:
        return "INSUFFICIENT"
    if effective_n < 100 or span_days < 7:
        return "EARLY"
    if effective_n < 300 or span_days < 30:
        return "PROVISIONAL"
    return "SUPPORTED"


def _error_counters(self: _ENGINE) -> dict:
    _ensure_g1_tables(self)
    with self._lock:
        counts = {str(r["error_type"]): int(r["n"]) for r in self._conn.execute(
            "SELECT error_type,COUNT(*) n FROM g1_contract_errors "
            "WHERE dataset_contract_version=? GROUP BY error_type",
            (G1_DATASET_CONTRACT_VERSION,),
        ).fetchall()}
    return {name: counts.get(error_type, 0) for name, error_type in _ERROR_TYPES.items()}


def g1_dataset_status(self: _ENGINE) -> dict:
    _sync_membership(self, limit=1000)
    rows = _eligible_rows(self)
    with self._lock:
        raw_source_n = int(self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations"
        ).fetchone()[0])
        resolved_source_n = int(self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations WHERE resolution_status='resolved'"
        ).fetchone()[0])
        current_source_n = int(self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations WHERE feature_contract_version=?",
            (_pl.PASSIVE_SCHEMA_VERSION,),
        ).fetchone()[0])
        membership_n = int(self._conn.execute(
            "SELECT COUNT(*) FROM g1_dataset_membership WHERE dataset_contract_version=?",
            (G1_DATASET_CONTRACT_VERSION,),
        ).fetchone()[0])
        q_n = int(self._conn.execute(
            "SELECT COUNT(*) FROM g1_dataset_membership g "
            "WHERE g.dataset_contract_version=? AND g.q_to_p_eligible=1 "
            "AND NOT EXISTS(SELECT 1 FROM g1_contract_errors e WHERE "
            "e.dataset_contract_version=g.dataset_contract_version "
            "AND e.observation_id=g.observation_id AND e.error_type='SOURCE_MUTATED')",
            (G1_DATASET_CONTRACT_VERSION,),
        ).fetchone()[0])
    unique_obs = len({str(r["observation_id"]) for r in rows})
    unique_anchor = len({str(r["dependency_group_id"]) for r in rows})
    effective = _effective_n_nonoverlap(rows, aggregate=True)
    raw_task_membership_n = len(rows) + q_n
    first_ts = min((float(r["captured_ts"]) for r in rows), default=None)
    last_ts = max((float(r["captured_ts"]) for r in rows), default=None)
    span_days = ((last_ts - first_ts) / 86400.0) if first_ts is not None and last_ts is not None else 0.0
    cohort_ids = {str(r["base_cohort_id"]) for r in rows}
    q_cohort_ids = {str(r["base_cohort_id"]) for r in rows if int(r.get("q_to_p_eligible") or 0) == 1}
    errors = _error_counters(self)
    contract_error_total = sum(errors.values())
    return {
        "g1_stage": G1_STAGE,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "source_contract_version": _pl.PASSIVE_SCHEMA_VERSION,
        "measurement_runtime_version": MEASUREMENT_RUNTIME_VERSION,
        "cohort_contract_version": G1_COHORT_CONTRACT_VERSION,
        "horizon_bucket_contract_version": G1_HORIZON_BUCKET_CONTRACT_VERSION,
        "effective_n_contract_version": G1_EFFECTIVE_N_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "data_cutoff_ts": time.time(),
        "prospective_only": True,
        "retrospective_replay_allowed": False,
        "raw_source_n": raw_source_n,
        "resolved_source_n": resolved_source_n,
        "current_source_n": current_source_n,
        "evaluated_membership_n": membership_n,
        "raw_task_membership_n": raw_task_membership_n,
        "forecast_eval_eligible_n": len(rows),
        "q_to_p_eligible_n": q_n,
        "unique_observation_n": unique_obs,
        "unique_anchor_n": unique_anchor,
        "effective_n": effective,
        "dependency_ratio": round(effective / len(rows), 8) if rows else None,
        "anchor_dependency_ratio": round(unique_anchor / len(rows), 8) if rows else None,
        "first_eligible_ts": first_ts,
        "last_eligible_ts": last_ts,
        "eligible_time_span_days": round(span_days, 8),
        "cohort_count": len(cohort_ids),
        "cohorts_with_q_n": len(q_cohort_ids),
        "evidence_status": _evidence_status(effective, span_days),
        "evidence_status_scope": "dataset_size_and_coverage_only",
        "dataset_contract_implemented": True,
        "dataset_contract_runtime_validated": bool(membership_n > 0 and contract_error_total == 0),
        "dataset_measurement_ready": bool(len(rows) > 0),
        "dataset_q_samples_available": bool(q_n > 0),
        **errors,
        "authority": "research_only",
        "production_authority": False,
        "g1_training_allowed": False,
        "physical_probability_published": False,
        "promotion_allowed": False,
        "production_replacement_allowed": False,
        "sample_count_auto_promotion": False,
    }


def g1_dataset_cohorts(self: _ENGINE) -> dict:
    rows = _eligible_rows(self)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["base_cohort_id"])].append(row)
    items = []
    for cohort_id, members in sorted(grouped.items()):
        first_ts = min(float(r["captured_ts"]) for r in members)
        last_ts = max(float(r["captured_ts"]) for r in members)
        span_days = max(0.0, (last_ts - first_ts) / 86400.0)
        effective = _effective_n_nonoverlap(members, aggregate=False)
        regime_counts = Counter(str(r.get("regime_stratum") or "UNCLASSIFIED") for r in members)
        session_counts = Counter(str(r.get("session_stratum") or "UNKNOWN") for r in members)
        items.append({
            "cohort_id": cohort_id,
            "base_cohort": _loads(members[0]["base_cohort_json"], {}),
            "raw_n": len(members),
            "unique_anchor_n": len({str(r["dependency_group_id"]) for r in members}),
            "effective_n": effective,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "span_days": round(span_days, 8),
            "forecast_eval_eligible_n": len(members),
            "q_to_p_eligible_n": sum(int(r.get("q_to_p_eligible") or 0) for r in members),
            "regime_counts": dict(sorted(regime_counts.items())),
            "session_counts": dict(sorted(session_counts.items())),
            "evidence_status": _evidence_status(effective, span_days),
            "evidence_status_scope": "dataset_size_and_coverage_only",
        })
    return {
        "g1_stage": G1_STAGE,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "effective_n_contract_version": G1_EFFECTIVE_N_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "data_cutoff_ts": time.time(),
        "items": items,
        "authority": "research_only",
        "promotion_allowed": False,
    }


def g1_dataset_exclusions(self: _ENGINE) -> dict:
    _sync_membership(self, limit=1000)
    with self._lock:
        rows = [dict(r) for r in self._conn.execute(
            "SELECT p.instrument,p.horizon_minutes,g.forecast_family,g.exclusion_reasons_json,"
            "g.q_exclusion_reasons_json,g.primary_reason,g.forecast_eval_eligible,g.q_to_p_eligible "
            "FROM g1_dataset_membership g JOIN passive_market_observations p "
            "ON p.observation_id=g.observation_id WHERE g.dataset_contract_version=?",
            (G1_DATASET_CONTRACT_VERSION,),
        ).fetchall()]
        pending_n = int(self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations WHERE resolution_status='pending'"
        ).fetchone()[0])
    reason_counts: Counter[str] = Counter()
    q_reason_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    by_instrument: Counter[str] = Counter()
    by_horizon: Counter[str] = Counter()
    by_family: Counter[str] = Counter()
    excluded = 0
    for row in rows:
        if int(row.get("q_to_p_eligible") or 0) != 1:
            q_reason_counts.update(str(x) for x in _loads(row.get("q_exclusion_reasons_json"), []))
        if int(row.get("forecast_eval_eligible") or 0) == 1:
            continue
        excluded += 1
        reasons = _loads(row.get("exclusion_reasons_json"), [])
        reason_counts.update(str(x) for x in reasons)
        primary = row.get("primary_reason")
        if primary:
            primary_counts[str(primary)] += 1
        by_instrument[str(row.get("instrument"))] += 1
        by_horizon[str(row.get("horizon_minutes"))] += 1
        by_family[str(row.get("forecast_family"))] += 1
    if pending_n:
        reason_counts["UNRESOLVED"] += pending_n
        primary_counts["UNRESOLVED"] += pending_n
        excluded += pending_n
    return {
        "g1_stage": G1_STAGE,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "data_cutoff_ts": time.time(),
        "total_excluded_n": excluded,
        "reason_counts": dict(sorted(reason_counts.items())),
        "q_reason_counts": dict(sorted(q_reason_counts.items())),
        "primary_reason_counts": dict(sorted(primary_counts.items())),
        "by_instrument": dict(sorted(by_instrument.items())),
        "by_horizon": dict(sorted(by_horizon.items())),
        "by_forecast_family": dict(sorted(by_family.items())),
        "authority": "research_only",
        "promotion_allowed": False,
    }


def create_g1_dataset_cut(
    self: _ENGINE,
    cutoff_ts: float | None = None,
    *,
    _fail_after_members: int | None = None,
) -> dict:
    """Freeze an atomic, cutoff-safe, deterministic G.1A dataset manifest."""
    _ensure_g1_tables(self)
    cutoff = float(cutoff_ts if cutoff_ts is not None else time.time())
    if not math.isfinite(cutoff) or cutoff <= 0:
        raise ValueError("invalid cutoff_ts")
    _sync_membership(self, limit=5000)
    candidates = _eligible_rows(self, cutoff_ts=cutoff)
    verified = []
    for row in candidates:
        with self._lock:
            source = self._conn.execute(
                "SELECT * FROM passive_market_observations WHERE observation_id=?",
                (row["observation_id"],),
            ).fetchone()
        if source is None:
            continue
        source_dict = dict(source)
        actual = _source_record_sha256(source_dict)
        expected = str(row["source_record_sha256"])
        if actual != expected:
            _record_error(
                self, "SOURCE_MUTATED", observation_id=row["observation_id"],
                expected_sha256=expected, actual_sha256=actual,
                detail="source hash mismatch during dataset cut",
            )
            continue
        item = dict(row)
        item["verified_source_sha256"] = actual
        verified.append(item)

    effective = _effective_n_nonoverlap(verified, aggregate=True)
    members = [
        {
            "observation_id": str(r["observation_id"]),
            "source_record_sha256": str(r["verified_source_sha256"]),
            "forecast_family": str(r["forecast_family"]),
            "cohort_id": str(r["base_cohort_id"]),
            "dependency_group_id": str(r["dependency_group_id"]),
            "forecast_eval_eligible": True,
            "q_to_p_eligible": bool(r["q_to_p_eligible"]),
            "role": "UNASSIGNED",
        }
        for r in sorted(verified, key=lambda x: str(x["observation_id"]))
    ]
    unique_anchor_n = len({m["dependency_group_id"] for m in members})
    q_n = sum(1 for m in members if m["q_to_p_eligible"])
    manifest = {
        "manifest_version": G1_CUT_MANIFEST_VERSION,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "effective_n_contract_version": G1_EFFECTIVE_N_CONTRACT_VERSION,
        "cutoff_ts": cutoff,
        "members": members,
    }
    manifest_sha = _sha256(manifest)
    cut_id = f"g1cut-{manifest_sha[:24]}"
    raw_task_n = len(members) + q_n
    cohort_count = len({m["cohort_id"] for m in members})
    try:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT cut_id,dataset_contract_version,cutoff_ts,created_ts,manifest_sha256,"
                "raw_task_membership_n,unique_observation_n,unique_anchor_n,effective_n,cohort_count,"
                "q_eligible_n,status FROM g1_dataset_cuts WHERE manifest_sha256=?", (manifest_sha,)
            ).fetchone()
            if existing is not None:
                self._conn.rollback()
                return dict(existing)
            self._conn.execute(
                "INSERT INTO g1_dataset_cuts("
                "cut_id,dataset_contract_version,cutoff_ts,created_ts,manifest_sha256,manifest_json,"
                "raw_task_membership_n,unique_observation_n,unique_anchor_n,effective_n,cohort_count,"
                "q_eligible_n,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cut_id, G1_DATASET_CONTRACT_VERSION, cutoff, time.time(), manifest_sha,
                    _json(manifest), raw_task_n, len(members), unique_anchor_n, effective,
                    cohort_count, q_n, "FROZEN",
                ),
            )
            for index, member in enumerate(members, start=1):
                self._conn.execute(
                    "INSERT INTO g1_dataset_cut_members("
                    "cut_id,observation_id,source_record_sha256,forecast_family,cohort_id,"
                    "dependency_group_id,forecast_eval_eligible,q_to_p_eligible,role) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        cut_id, member["observation_id"], member["source_record_sha256"],
                        member["forecast_family"], member["cohort_id"], member["dependency_group_id"],
                        1, int(member["q_to_p_eligible"]), "UNASSIGNED",
                    ),
                )
                if _fail_after_members is not None and index >= int(_fail_after_members):
                    raise RuntimeError("injected dataset cut failure")
            self._conn.commit()
    except Exception as exc:
        with self._lock:
            self._conn.rollback()
        _record_error(self, "DATASET_CUT", detail=f"{type(exc).__name__}: {str(exc)[:180]}")
        raise
    return {
        "cut_id": cut_id,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "cutoff_ts": cutoff,
        "manifest_sha256": manifest_sha,
        "raw_task_membership_n": raw_task_n,
        "unique_observation_n": len(members),
        "unique_anchor_n": unique_anchor_n,
        "effective_n": effective,
        "cohort_count": cohort_count,
        "q_eligible_n": q_n,
        "status": "FROZEN",
    }


def g1_dataset_cuts(self: _ENGINE, limit: int = 20) -> dict:
    _ensure_g1_tables(self)
    with self._lock:
        rows = [dict(r) for r in self._conn.execute(
            "SELECT cut_id,dataset_contract_version,cutoff_ts,created_ts,manifest_sha256,"
            "raw_task_membership_n,unique_observation_n,unique_anchor_n,effective_n,cohort_count,"
            "q_eligible_n,status FROM g1_dataset_cuts ORDER BY created_ts DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        ).fetchall()]
    return {
        "g1_stage": G1_STAGE,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "data_cutoff_ts": time.time(),
        "items": rows,
        "authority": "research_only",
        "promotion_allowed": False,
    }


def init_g1a(self: _ENGINE, *args, **kwargs) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    try:
        _ensure_g1_tables(self)
    except Exception as exc:
        self._g1a_init_error = f"{type(exc).__name__}: {str(exc)[:180]}"


def resolve_due_g1a(self: _ENGINE, *args, **kwargs) -> dict:
    result = _ORIGINAL_RESOLVE_DUE(self, *args, **kwargs)
    try:
        _sync_membership(self, limit=200)
    except Exception as exc:
        try:
            _record_error(self, "DATASET_MEMBERSHIP", detail=f"{type(exc).__name__}: {str(exc)[:180]}")
        except Exception:
            pass
    return result


def install_g1_dataset_runtime() -> None:
    if getattr(_ENGINE, "_g1_dataset_runtime", None) == G1_DATASET_CONTRACT_VERSION:
        return
    _ENGINE.__init__ = init_g1a
    _ENGINE.resolve_due = resolve_due_g1a
    _ENGINE._g1_ensure_tables = _ensure_g1_tables
    _ENGINE._g1_sync_membership = _sync_membership
    _ENGINE._g1_evaluate_row = staticmethod(_evaluate_row)
    _ENGINE._g1_effective_n = staticmethod(_effective_n_nonoverlap)
    _ENGINE.g1_dataset_status = g1_dataset_status
    _ENGINE.g1_dataset_cohorts = g1_dataset_cohorts
    _ENGINE.g1_dataset_exclusions = g1_dataset_exclusions
    _ENGINE.create_g1_dataset_cut = create_g1_dataset_cut
    _ENGINE.g1_dataset_cuts = g1_dataset_cuts
    _ENGINE._g1_dataset_runtime = G1_DATASET_CONTRACT_VERSION
