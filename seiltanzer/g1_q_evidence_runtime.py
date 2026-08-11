"""Phase G.1B.1 prospective Q evidence bring-up and diagnostics.

This layer makes option-native risk-neutral Q capture observable without changing
G.1A admission, fitting a calibrator, publishing physical P, or granting any
production authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import Counter
from copy import deepcopy
from typing import Any

from . import g1_dataset_runtime as _g1
from . import passive_learning as _pl
from .config import INSTRUMENTS
from .measurement_q_runtime import (
    MEASUREMENT_RUNTIME_VERSION,
    adapt_option_q_forecast_f32a,
    finite,
    valid_terminal_cdf,
)
from .option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION

G1B1_STAGE = "G.1B.1"
Q_CAPABILITY_CONTRACT_VERSION = "q-source-capability-v1"
Q_CAPTURE_ATTEMPT_CONTRACT_VERSION = "q-capture-attempt-v1"
Q_CAPTURE_POLICY_VERSION = "q-native-expiry-cadence-v1"
Q_EVIDENCE_CONTRACT_VERSION = "g1-q-evidence-v1"
Q_SOURCE_SNAPSHOT_MAX_AGE_SEC = 30.0 * 60.0

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_INIT = _ENGINE.__init__
_ORIGINAL_CAPTURE = _ENGINE.capture_observation

_BLOCKERS = {
    "NO_Q_SOURCE_CONFIGURED",
    "Q_PROVIDER_UNAVAILABLE",
    "Q_SOURCE_NO_DATA",
    "Q_SOURCE_STALE",
    "TARGET_PRICE_UNAVAILABLE",
    "TARGET_PRICE_STALE",
    "SOURCE_SPOT_UNAVAILABLE",
    "OPTION_CHAIN_UNAVAILABLE",
    "OPTION_CHAIN_STALE",
    "EXPIRY_UNAVAILABLE",
    "EXPIRY_INVALID",
    "INSUFFICIENT_STRIKES",
    "INVALID_OPTION_QUOTES",
    "IV_UNAVAILABLE",
    "CDF_BUILD_FAILED",
    "CDF_INVALID",
    "CDF_NON_MONOTONE",
    "CDF_MASS_INVALID",
    "PROXY_RELATION_UNKNOWN",
    "PROXY_TRANSFORM_UNKNOWN",
    "INVERSE_TRANSFORM_FAILED",
    "TIME_CONTRACT_INVALID",
    "CAPTURE_PERSIST_FAILED",
    "SOURCE_CONTRACT_ERROR",
}

_DISTRIBUTION_BLOCKERS = {
    "INSUFFICIENT_STRIKES", "INVALID_OPTION_QUOTES", "IV_UNAVAILABLE",
    "CDF_BUILD_FAILED", "CDF_INVALID", "CDF_NON_MONOTONE", "CDF_MASS_INVALID",
}
_EXPIRY_BLOCKERS = {"EXPIRY_UNAVAILABLE", "EXPIRY_INVALID", "TIME_CONTRACT_INVALID"}
_PROXY_BLOCKERS = {"PROXY_RELATION_UNKNOWN", "PROXY_TRANSFORM_UNKNOWN"}
_RESOLUTION_REASONS = {
    "TERMINAL_NOT_CLEAN", "TERMINAL_NOT_AUTHORITATIVE", "TERMINAL_LOOKAHEAD",
    "TERMINAL_TOO_OLD", "INVALID_TERMINAL_PRICE", "INVALID_TIME_CONTRACT",
}


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value)) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _capability_for(instrument: str) -> dict:
    inst = INSTRUMENTS[instrument]
    source = inst.options_proxy
    transform = str(inst.proxy_transform or "direct").lower()
    if source is None:
        relation = "NONE"
    elif str(source) == str(instrument):
        relation = "NATIVE"
    elif transform == "inverse":
        relation = "INVERSE_PROXY"
    else:
        relation = "DIRECT_PROXY"
    return {
        "capability_contract_version": Q_CAPABILITY_CONTRACT_VERSION,
        "target_instrument": instrument,
        "q_source_instrument": source,
        "relation": relation,
        "proxy_transform": transform if source is not None else None,
        "provider": "yfinance_options" if source is not None else None,
        "configured": source is not None,
        "proxy_experimental": bool(inst.proxy_experimental),
        "option_q_contract_version": OPTION_Q_CONTRACT_VERSION,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
    }


def _ensure_q_tables(self: _ENGINE) -> None:
    with self._lock, self._conn:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1_q_capture_attempts (
                attempt_id TEXT PRIMARY KEY,
                attempt_ts REAL NOT NULL,
                attempt_origin TEXT NOT NULL,
                target_instrument TEXT NOT NULL,
                q_source_instrument TEXT,
                relation TEXT NOT NULL,
                proxy_transform TEXT,
                provider TEXT,
                requested_expiry_ts REAL,
                source_available INTEGER NOT NULL,
                source_fresh INTEGER NOT NULL,
                target_price_available INTEGER NOT NULL,
                source_price_available INTEGER NOT NULL,
                chain_available INTEGER NOT NULL,
                distribution_built INTEGER NOT NULL,
                distribution_valid INTEGER NOT NULL,
                observation_created INTEGER NOT NULL,
                created_observation_id TEXT,
                blocker_code TEXT,
                latency_ms REAL,
                capability_contract_version TEXT NOT NULL,
                attempt_contract_version TEXT NOT NULL,
                capture_policy_version TEXT NOT NULL,
                option_q_contract_version TEXT NOT NULL,
                expiry_clock_version TEXT NOT NULL,
                measurement_runtime_version TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_q_attempt_instrument_ts "
            "ON g1_q_capture_attempts(target_instrument,attempt_ts)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_q_attempt_created "
            "ON g1_q_capture_attempts(observation_created,attempt_origin,attempt_ts)"
        )
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_q_capture_attempt_immutable_update
            BEFORE UPDATE ON g1_q_capture_attempts
            BEGIN SELECT RAISE(ABORT,'immutable G1 Q capture attempt'); END""")
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1_q_capture_attempt_immutable_delete
            BEFORE DELETE ON g1_q_capture_attempts
            BEGIN SELECT RAISE(ABORT,'immutable G1 Q capture attempt'); END""")


def _origin(self: _ENGINE, trigger_reason: str, observation_origin: str | None) -> str:
    if trigger_reason == "test":
        return "test"
    if bool(getattr(self, "_f32a_background_capture", False)):
        return "background_collector"
    return "manual" if observation_origin in {None, "background_collector"} else str(observation_origin)


def _source_age(provenance: dict) -> float | None:
    options = provenance.get("options") if isinstance(provenance, dict) else None
    return finite((options or {}).get("age_sec"))


def _density_shape(metrics: dict | None) -> tuple[list | None, list | None]:
    density = metrics.get("density") if isinstance(metrics, dict) else None
    if not isinstance(density, dict):
        return None, None
    strikes = density.get("strikes")
    q = density.get("q")
    return strikes if isinstance(strikes, list) else None, q if isinstance(q, list) else None


def _classify_pre_capture(
    *, instrument: str, captured_ts: float, market_price: float,
    features: dict, provenance: dict,
) -> tuple[str | None, dict]:
    capability = _capability_for(instrument)
    metrics = ((features.get("option_derivatives") or {}).get("data")
               if isinstance(features, dict) else None)
    options_meta = provenance.get("options") if isinstance(provenance, dict) else {}
    price_meta = provenance.get("price") if isinstance(provenance, dict) else {}
    source_age = _source_age(provenance)
    source = capability["q_source_instrument"]
    transform = capability["proxy_transform"]
    target_price = finite(market_price)
    source_spot = finite((metrics or {}).get("proxy_spot"))
    if source_spot is None:
        source_spot = finite((metrics or {}).get("spot"))
    expiry = finite((metrics or {}).get("expiry_ts_utc"))
    strikes, q = _density_shape(metrics)

    detail = {
        "capability": capability,
        "source_snapshot_age_sec": source_age,
        "price_kind": (price_meta or {}).get("kind"),
        "price_age_sec": finite((price_meta or {}).get("age_sec")),
        "option_source": (options_meta or {}).get("source"),
        "option_kind": (options_meta or {}).get("kind"),
        "option_quality": finite((options_meta or {}).get("quality")),
        "expiry_ts_utc": expiry,
        "density_points": len(strikes) if strikes is not None else 0,
    }

    if source is None:
        return "NO_Q_SOURCE_CONFIGURED", detail
    if transform not in {"direct", "inverse"}:
        return "PROXY_TRANSFORM_UNKNOWN", detail
    if capability["relation"] == "NONE":
        return "PROXY_RELATION_UNKNOWN", detail
    if target_price is None or target_price <= 0:
        return "TARGET_PRICE_UNAVAILABLE", detail
    price_age = finite((price_meta or {}).get("age_sec"))
    if price_age is not None and price_age > 60.0:
        detail["target_price_warning"] = "stale_for_g1a_admission"
    if not isinstance(metrics, dict):
        if not (options_meta or {}).get("source"):
            return "Q_PROVIDER_UNAVAILABLE", detail
        return "OPTION_CHAIN_UNAVAILABLE", detail
    if source_age is not None and source_age > Q_SOURCE_SNAPSHOT_MAX_AGE_SEC:
        return "OPTION_CHAIN_STALE", detail
    if source_spot is None or source_spot <= 0:
        return "SOURCE_SPOT_UNAVAILABLE", detail
    if expiry is None:
        return "EXPIRY_UNAVAILABLE", detail
    if expiry <= float(captured_ts):
        return "EXPIRY_INVALID", detail
    if strikes is None or q is None:
        return "CDF_BUILD_FAILED", detail
    if len(strikes) < 5 or len(strikes) != len(q):
        return "INSUFFICIENT_STRIKES", detail
    try:
        xs = [float(x) for x in strikes]
        ys = [float(y) for y in q]
    except (TypeError, ValueError):
        return "CDF_INVALID", detail
    if not all(math.isfinite(x) for x in xs + ys) or any(y < 0 for y in ys):
        return "CDF_INVALID", detail
    if any(xs[i + 1] <= xs[i] for i in range(len(xs) - 1)):
        return "CDF_INVALID", detail
    return None, detail


def _validate_created_q(
    self: _ENGINE, ids: list[str], instrument: str, captured_ts: float,
    market_price: float, features: dict,
) -> tuple[bool, str | None, str | None, dict]:
    native_ids = [str(item) for item in ids if str(item).endswith("-native-expiry")]
    if not native_ids:
        pre_blocker, detail = _classify_pre_capture(
            instrument=instrument, captured_ts=captured_ts, market_price=market_price,
            features=features, provenance={},
        )
        return False, None, pre_blocker or "CAPTURE_PERSIST_FAILED", detail

    native_id = native_ids[-1]
    with self._lock:
        row = self._conn.execute(
            "SELECT target_ts,forecast_json,evidence_eligible,observation_origin "
            "FROM passive_market_observations WHERE observation_id=?",
            (native_id,),
        ).fetchone()
    if row is None:
        return False, native_id, "CAPTURE_PERSIST_FAILED", {}
    row = dict(row)
    forecast = _loads(row.get("forecast_json"), {})
    detail = {
        "native_target_ts": finite(row.get("target_ts")),
        "stored_evidence_eligible": bool(row.get("evidence_eligible")),
        "stored_observation_origin": row.get("observation_origin"),
        "probability_measure": forecast.get("probability_measure"),
        "horizon_kind": forecast.get("horizon_kind"),
        "horizon_alignment_method": forecast.get("horizon_alignment_method"),
        "q_source_contract": forecast.get("q_source_contract"),
        "q_source_instrument": forecast.get("q_source_instrument") or forecast.get("proxy_symbol"),
        "q_target_instrument": forecast.get("q_target_instrument"),
        "proxy_transform": forecast.get("proxy_transform"),
        "source_expiry_ts_utc": finite(forecast.get("source_expiry_ts_utc")),
        "calendar_ttm_seconds": finite(forecast.get("calendar_ttm_seconds")),
    }
    if forecast.get("measurement_runtime_contract") != MEASUREMENT_RUNTIME_VERSION:
        return False, native_id, "SOURCE_CONTRACT_ERROR", detail
    if forecast.get("horizon_kind") != "option_native_expiry":
        return False, native_id, "TIME_CONTRACT_INVALID", detail
    if forecast.get("probability_measure") != "risk_neutral_Q_terminal":
        method = str(forecast.get("horizon_alignment_method") or "")
        blocker = "CDF_INVALID" if "cdf" in method else "CDF_BUILD_FAILED"
        return False, native_id, blocker, detail
    if forecast.get("q_source_contract") != OPTION_Q_CONTRACT_VERSION:
        return False, native_id, "SOURCE_CONTRACT_ERROR", detail
    if forecast.get("expiry_clock_version") != EXPIRY_CLOCK_VERSION:
        return False, native_id, "TIME_CONTRACT_INVALID", detail
    transform = str(forecast.get("proxy_transform") or "").lower()
    if transform not in {"direct", "inverse"}:
        return False, native_id, "PROXY_TRANSFORM_UNKNOWN", detail
    if not valid_terminal_cdf(forecast.get("terminal_q_cdf")):
        return False, native_id, "CDF_INVALID", detail
    target = finite(row.get("target_ts"))
    expiry = finite(forecast.get("source_expiry_ts_utc"))
    ttm = finite(forecast.get("calendar_ttm_seconds"))
    made = finite(forecast.get("forecast_made_at"))
    if target is None or expiry is None or abs(target - expiry) > 1.0:
        return False, native_id, "TIME_CONTRACT_INVALID", detail
    if made is None or ttm is None or abs(ttm - (target - made)) > 1.0:
        return False, native_id, "TIME_CONTRACT_INVALID", detail
    return True, native_id, None, detail


def _record_attempt(self: _ENGINE, payload: dict) -> None:
    _ensure_q_tables(self)
    blocker = payload.get("blocker_code")
    if blocker is not None and blocker not in _BLOCKERS:
        blocker = "SOURCE_CONTRACT_ERROR"
    with self._lock, self._conn:
        self._conn.execute(
            "INSERT INTO g1_q_capture_attempts("
            "attempt_id,attempt_ts,attempt_origin,target_instrument,q_source_instrument,relation,"
            "proxy_transform,provider,requested_expiry_ts,source_available,source_fresh,"
            "target_price_available,source_price_available,chain_available,distribution_built,"
            "distribution_valid,observation_created,created_observation_id,blocker_code,latency_ms,"
            "capability_contract_version,attempt_contract_version,capture_policy_version,"
            "option_q_contract_version,expiry_clock_version,measurement_runtime_version,detail_json,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                payload["attempt_id"], payload["attempt_ts"], payload["attempt_origin"],
                payload["target_instrument"], payload.get("q_source_instrument"), payload["relation"],
                payload.get("proxy_transform"), payload.get("provider"), payload.get("requested_expiry_ts"),
                int(bool(payload.get("source_available"))), int(bool(payload.get("source_fresh"))),
                int(bool(payload.get("target_price_available"))), int(bool(payload.get("source_price_available"))),
                int(bool(payload.get("chain_available"))), int(bool(payload.get("distribution_built"))),
                int(bool(payload.get("distribution_valid"))), int(bool(payload.get("observation_created"))),
                payload.get("created_observation_id"), blocker, payload.get("latency_ms"),
                Q_CAPABILITY_CONTRACT_VERSION, Q_CAPTURE_ATTEMPT_CONTRACT_VERSION,
                Q_CAPTURE_POLICY_VERSION, OPTION_Q_CONTRACT_VERSION, EXPIRY_CLOCK_VERSION,
                MEASUREMENT_RUNTIME_VERSION, _json(payload.get("detail") or {}), time.time(),
            ),
        )


def capture_observation_g1b1(
    self: _ENGINE,
    *,
    instrument: str,
    captured_ts: float,
    market_price: float,
    features: dict,
    forecast: dict,
    provenance: dict,
    trigger_reason: str = "cadence",
    evidence_eligible: bool = True,
    observation_origin: str | None = None,
) -> list[str]:
    started = time.monotonic()
    attempt_ts = time.time()
    origin = _origin(self, trigger_reason, observation_origin)
    capability = _capability_for(instrument)
    frozen_features = deepcopy(features) if isinstance(features, dict) else {}
    frozen_provenance = deepcopy(provenance) if isinstance(provenance, dict) else {}
    pre_blocker, pre_detail = _classify_pre_capture(
        instrument=instrument, captured_ts=float(captured_ts), market_price=float(market_price),
        features=frozen_features, provenance=frozen_provenance,
    )

    ids: list[str] = []
    error: Exception | None = None
    try:
        ids = _ORIGINAL_CAPTURE(
            self, instrument=instrument, captured_ts=captured_ts, market_price=market_price,
            features=features, forecast=forecast, provenance=provenance,
            trigger_reason=trigger_reason, evidence_eligible=evidence_eligible,
            observation_origin=observation_origin,
        )
        return ids
    except Exception as exc:
        error = exc
        raise
    finally:
        try:
            success = False
            native_id = None
            post_blocker = None
            post_detail: dict[str, Any] = {}
            if error is None:
                success, native_id, post_blocker, post_detail = _validate_created_q(
                    self, ids, instrument, attempt_ts, market_price, frozen_features,
                )
            blocker = None if success else (pre_blocker or post_blocker or "CAPTURE_PERSIST_FAILED")
            if error is not None:
                blocker = "CAPTURE_PERSIST_FAILED"
                post_detail["capture_exception"] = f"{type(error).__name__}: {str(error)[:180]}"
            metrics = ((frozen_features.get("option_derivatives") or {}).get("data")
                       if isinstance(frozen_features, dict) else None)
            options_meta = frozen_provenance.get("options") or {}
            source_age = _source_age(frozen_provenance)
            source_spot = finite((metrics or {}).get("proxy_spot"))
            if source_spot is None:
                source_spot = finite((metrics or {}).get("spot"))
            expiry = finite((metrics or {}).get("expiry_ts_utc"))
            strikes, q_values = _density_shape(metrics)
            source_available = bool(capability["configured"] and isinstance(metrics, dict))
            source_fresh = bool(
                source_available and source_age is not None
                and source_age <= Q_SOURCE_SNAPSHOT_MAX_AGE_SEC
            )
            provider = options_meta.get("source") or capability.get("provider")
            detail = {**pre_detail, **post_detail, "created_ids": list(ids)}
            if origin != "background_collector":
                detail["excluded_from_production_q_telemetry"] = True
            _record_attempt(self, {
                "attempt_id": "q-attempt-" + uuid.uuid4().hex,
                "attempt_ts": attempt_ts,
                "attempt_origin": origin,
                "target_instrument": instrument,
                "q_source_instrument": capability.get("q_source_instrument"),
                "relation": capability["relation"],
                "proxy_transform": capability.get("proxy_transform"),
                "provider": provider,
                "requested_expiry_ts": expiry,
                "source_available": source_available,
                "source_fresh": source_fresh,
                "target_price_available": finite(market_price) is not None and float(market_price) > 0,
                "source_price_available": source_spot is not None and source_spot > 0,
                "chain_available": isinstance(metrics, dict),
                "distribution_built": strikes is not None and q_values is not None,
                "distribution_valid": success,
                "observation_created": success,
                "created_observation_id": native_id,
                "blocker_code": blocker,
                "latency_ms": round((time.monotonic() - started) * 1000.0, 3),
                "detail": detail,
            })
        except Exception as ledger_exc:
            self._g1b1_last_ledger_error = f"{type(ledger_exc).__name__}: {str(ledger_exc)[:180]}"


def _background_attempts(self: _ENGINE) -> list[dict]:
    _ensure_q_tables(self)
    with self._lock:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM g1_q_capture_attempts WHERE attempt_origin='background_collector' "
            "ORDER BY attempt_ts,attempt_id"
        ).fetchall()]


def _q_membership_rows(self: _ENGINE) -> list[dict]:
    try:
        self._g1_sync_membership(limit=5000)
    except Exception:
        pass
    with self._lock:
        return [dict(row) for row in self._conn.execute(
            "SELECT p.observation_id,p.anchor_group_id,p.captured_ts,p.target_ts,p.instrument,"
            "g.base_cohort_id,g.dependency_group_id,g.q_to_p_eligible,g.primary_reason,g.q_exclusion_reasons_json "
            "FROM g1_q_capture_attempts a JOIN passive_market_observations p "
            "ON p.observation_id=a.created_observation_id "
            "LEFT JOIN g1_dataset_membership g ON g.observation_id=p.observation_id "
            "AND g.dataset_contract_version=? "
            "WHERE a.attempt_origin='background_collector' AND a.observation_created=1",
            (_g1.G1_DATASET_CONTRACT_VERSION,),
        ).fetchall()]


def _evidence_status(effective_n: int, first_ts: float | None, last_ts: float | None) -> str:
    span = 0.0 if first_ts is None or last_ts is None else max(0.0, (last_ts - first_ts) / 86400.0)
    if effective_n < 30:
        return "INSUFFICIENT"
    if effective_n < 100 or span < 7:
        return "EARLY"
    if effective_n < 300 or span < 30:
        return "PROVISIONAL"
    return "SUPPORTED"


def _error_counters(attempts: list[dict], memberships: list[dict], pit_mismatch: int) -> dict:
    blockers = [str(row.get("blocker_code")) for row in attempts if row.get("blocker_code")]
    resolution_n = 0
    pit_missing = 0
    for row in memberships:
        reasons = set(_loads(row.get("q_exclusion_reasons_json"), []))
        if reasons & _RESOLUTION_REASONS:
            resolution_n += 1
        if "PIT_MISSING" in reasons:
            pit_missing += 1
    return {
        "q_capture_attempt_error_n": len(blockers),
        "q_source_contract_error_n": sum(code == "SOURCE_CONTRACT_ERROR" for code in blockers),
        "q_distribution_error_n": sum(code in _DISTRIBUTION_BLOCKERS for code in blockers),
        "q_expiry_contract_error_n": sum(code in _EXPIRY_BLOCKERS for code in blockers),
        "q_proxy_mapping_error_n": sum(code in _PROXY_BLOCKERS for code in blockers),
        "q_inverse_transform_error_n": sum(code == "INVERSE_TRANSFORM_FAILED" for code in blockers),
        "q_persistence_error_n": sum(code == "CAPTURE_PERSIST_FAILED" for code in blockers),
        "q_resolution_contract_error_n": resolution_n,
        "q_pit_contract_error_n": pit_missing + int(pit_mismatch or 0),
    }


def g1_q_status(self: _ENGINE) -> dict:
    attempts = _background_attempts(self)
    memberships = _q_membership_rows(self)
    captured_ids = {
        str(row["created_observation_id"]) for row in attempts
        if int(row.get("observation_created") or 0) == 1 and row.get("created_observation_id")
    }
    with self._lock:
        state_rows = [dict(row) for row in self._conn.execute(
            "SELECT observation_id,resolution_status,resolved_ts FROM passive_market_observations "
            "WHERE observation_id IN (SELECT created_observation_id FROM g1_q_capture_attempts "
            "WHERE attempt_origin='background_collector' AND observation_created=1)"
        ).fetchall()]
    resolved_ids = {str(row["observation_id"]) for row in state_rows if row.get("resolution_status") == "resolved"}
    eligible_rows = [row for row in memberships if int(row.get("q_to_p_eligible") or 0) == 1]
    effective_q_n = self._g1_effective_n(eligible_rows, aggregate=True) if eligible_rows else 0
    first_ts = min((float(row["captured_ts"]) for row in eligible_rows), default=None)
    last_ts = max((float(row["captured_ts"]) for row in eligible_rows), default=None)
    relation_counts = Counter(str(row.get("relation") or "UNKNOWN") for row in attempts if int(row.get("observation_created") or 0) == 1)
    provider_counts = Counter(str(row.get("provider") or "UNKNOWN") for row in attempts if int(row.get("observation_created") or 0) == 1)
    blockers = Counter(str(row["blocker_code"]) for row in attempts if row.get("blocker_code"))
    latest = attempts[-1] if attempts else None
    successes = [row for row in attempts if int(row.get("observation_created") or 0) == 1]
    latest_success = successes[-1] if successes else None
    try:
        baseline = self.g1_baseline_status()
        q_baseline = baseline.get("terminal_q_identity") or {}
        q_metrics_n = int(q_baseline.get("metrics_eligible_n") or 0)
        pit_mismatch = int(q_baseline.get("pit_contract_mismatch_n") or 0)
    except Exception:
        q_metrics_n = 0
        pit_mismatch = 0
    configured_n = sum(1 for code in INSTRUMENTS if _capability_for(code)["configured"])
    source_available_seen = any(int(row.get("source_available") or 0) == 1 for row in attempts)
    runtime_validated = bool(successes)
    return {
        "g1_stage": G1B1_STAGE,
        "q_evidence_contract_version": Q_EVIDENCE_CONTRACT_VERSION,
        "capability_contract_version": Q_CAPABILITY_CONTRACT_VERSION,
        "capture_attempt_contract_version": Q_CAPTURE_ATTEMPT_CONTRACT_VERSION,
        "capture_policy_version": Q_CAPTURE_POLICY_VERSION,
        "dataset_contract_version": _g1.G1_DATASET_CONTRACT_VERSION,
        "option_q_contract_version": OPTION_Q_CONTRACT_VERSION,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "measurement_runtime_version": MEASUREMENT_RUNTIME_VERSION,
        "generated_ts": time.time(),
        "prospective_only": True,
        "configured_instrument_n": configured_n,
        "total_instrument_n": len(INSTRUMENTS),
        "capture_attempt_n": len(attempts),
        "successful_q_capture_n": len(successes),
        "unresolved_q_capture_n": len(captured_ids - resolved_ids),
        "resolved_q_observation_n": len(captured_ids & resolved_ids),
        "q_to_p_eligible_n": len(eligible_rows),
        "g1b_q_metrics_eligible_n": q_metrics_n,
        "unique_q_anchor_n": len({str(row.get("dependency_group_id")) for row in eligible_rows}),
        "effective_q_n": effective_q_n,
        "relation_counts": dict(sorted(relation_counts.items())),
        "provider_counts": dict(sorted(provider_counts.items())),
        "top_blockers": dict(blockers.most_common(12)),
        "last_attempt_ts": finite((latest or {}).get("attempt_ts")),
        "last_successful_q_capture_ts": finite((latest_success or {}).get("attempt_ts")),
        "evidence_status": _evidence_status(effective_q_n, first_ts, last_ts),
        "implemented": True,
        "configured": configured_n > 0,
        "provider_available": source_available_seen,
        "runtime_validated": runtime_validated,
        "data_available": bool(successes),
        "prospective_capture_observed": bool(attempts),
        "resolved_evidence_available": bool(captured_ids & resolved_ids),
        "measurement_ready": bool(eligible_rows),
        **_error_counters(attempts, memberships, pit_mismatch),
        "ledger_error": getattr(self, "_g1b1_last_ledger_error", None),
        "authority": "research_only",
        "production_authority": False,
        "calibrator_fitted": False,
        "calibrator_registry_writes": False,
        "g1_training_allowed": False,
        "physical_probability_published": False,
        "promotion_allowed": False,
        "production_replacement_allowed": False,
        "sample_count_auto_promotion": False,
    }


def g1_q_instruments(self: _ENGINE) -> dict:
    attempts = _background_attempts(self)
    memberships = _q_membership_rows(self)
    membership_by_obs = {str(row.get("observation_id")): row for row in memberships}
    grouped: dict[str, list[dict]] = {code: [] for code in INSTRUMENTS}
    for row in attempts:
        grouped.setdefault(str(row["target_instrument"]), []).append(row)
    items = []
    for code in INSTRUMENTS:
        capability = _capability_for(code)
        rows = grouped.get(code) or []
        successes = [row for row in rows if int(row.get("observation_created") or 0) == 1]
        resolved = 0
        eligible = 0
        for attempt in successes:
            obs_id = str(attempt.get("created_observation_id") or "")
            member = membership_by_obs.get(obs_id)
            with self._lock:
                state = self._conn.execute(
                    "SELECT resolution_status FROM passive_market_observations WHERE observation_id=?",
                    (obs_id,),
                ).fetchone()
            resolved += int(state is not None and state[0] == "resolved")
            if member is not None:
                eligible += int(member.get("q_to_p_eligible") or 0)
        last = rows[-1] if rows else None
        last_success = successes[-1] if successes else None
        items.append({
            **capability,
            "runtime_source_available": bool(last and int(last.get("source_available") or 0)),
            "runtime_chain_available": bool(last and int(last.get("chain_available") or 0)),
            "runtime_distribution_valid": bool(last and int(last.get("distribution_valid") or 0)),
            "last_attempt_ts": finite((last or {}).get("attempt_ts")),
            "last_success_ts": finite((last_success or {}).get("attempt_ts")),
            "capture_attempt_n": len(rows),
            "captured_n": len(successes),
            "resolved_n": resolved,
            "q_to_p_eligible_n": eligible,
            "primary_blocker": (last or {}).get("blocker_code"),
            "provider_runtime": (last or {}).get("provider"),
        })
    return {
        "g1_stage": G1B1_STAGE,
        "q_evidence_contract_version": Q_EVIDENCE_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "items": items,
        "authority": "research_only",
        "promotion_allowed": False,
    }


def g1_q_blockers(self: _ENGINE) -> dict:
    attempts = _background_attempts(self)
    counts = Counter(str(row["blocker_code"]) for row in attempts if row.get("blocker_code"))
    latest: dict[str, dict] = {}
    for row in attempts:
        code = row.get("blocker_code")
        if code:
            latest[str(code)] = row
    items = []
    for code, n in counts.most_common():
        row = latest[code]
        items.append({
            "blocker_code": code,
            "count": n,
            "last_seen_ts": finite(row.get("attempt_ts")),
            "last_instrument": row.get("target_instrument"),
            "last_provider": row.get("provider"),
        })
    return {
        "g1_stage": G1B1_STAGE,
        "q_evidence_contract_version": Q_EVIDENCE_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "total_blocked_n": sum(counts.values()),
        "items": items,
        "authority": "research_only",
    }


def g1_q_attempts(self: _ENGINE, limit: int = 100, instrument: str | None = None) -> dict:
    _ensure_q_tables(self)
    limit = max(1, min(500, int(limit)))
    args: list[Any] = []
    clause = "WHERE attempt_origin='background_collector'"
    if instrument is not None:
        if instrument not in INSTRUMENTS:
            raise KeyError(f"unsupported instrument: {instrument}")
        clause += " AND target_instrument=?"
        args.append(instrument)
    args.append(limit)
    with self._lock:
        rows = [dict(row) for row in self._conn.execute(
            "SELECT attempt_id,attempt_ts,target_instrument,q_source_instrument,relation,"
            "proxy_transform,provider,requested_expiry_ts,source_available,source_fresh,"
            "target_price_available,source_price_available,chain_available,distribution_built,"
            "distribution_valid,observation_created,created_observation_id,blocker_code,latency_ms,"
            "detail_json FROM g1_q_capture_attempts " + clause +
            " ORDER BY attempt_ts DESC,attempt_id DESC LIMIT ?",
            tuple(args),
        ).fetchall()]
    for row in rows:
        row["detail"] = _loads(row.pop("detail_json"), {})
    return {
        "g1_stage": G1B1_STAGE,
        "q_evidence_contract_version": Q_EVIDENCE_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "instrument": instrument,
        "items": rows,
        "authority": "research_only",
    }


def init_g1b1(self: _ENGINE, *args, **kwargs) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    try:
        _ensure_q_tables(self)
    except Exception as exc:
        self._g1b1_init_error = f"{type(exc).__name__}: {str(exc)[:180]}"


def install_g1_q_evidence_runtime() -> None:
    if getattr(_ENGINE, "_g1_q_evidence_runtime", None) == Q_EVIDENCE_CONTRACT_VERSION:
        return
    _ENGINE.__init__ = init_g1b1
    _ENGINE.capture_observation = capture_observation_g1b1
    _ENGINE._g1_q_ensure_tables = _ensure_q_tables
    _ENGINE._g1_q_capability = staticmethod(_capability_for)
    _ENGINE.g1_q_status = g1_q_status
    _ENGINE.g1_q_instruments = g1_q_instruments
    _ENGINE.g1_q_blockers = g1_q_blockers
    _ENGINE.g1_q_attempts = g1_q_attempts
    _ENGINE._g1_q_evidence_runtime = Q_EVIDENCE_CONTRACT_VERSION
