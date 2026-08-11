"""Independent persisted-cadence Q collector for Phase G.1B.1.

The ordinary passive collector is gated by fixed-horizon cadence/event triggers.
Q evidence must not be hidden behind that gate, so this module gives option-native
Q its own persisted 15-minute attempt cadence. A successful run writes exactly
one native-expiry observation; it never duplicates the seven fixed horizons.
"""
from __future__ import annotations

import hashlib
import math
import time
import uuid
from copy import deepcopy
from typing import Any

from . import g1_q_evidence_runtime as _q
from . import passive_learning as _pl
from .config import INSTRUMENTS

Q_COLLECTOR_CONTRACT_VERSION = "q-independent-collector-v1"
Q_COLLECTOR_CADENCE_SEC = 15 * 60

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_COLLECT = _ENGINE._collect_instrument


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _latest_attempt_ts(self: _ENGINE, instrument: str) -> float | None:
    self._g1_q_ensure_tables()
    with self._lock:
        row = self._conn.execute(
            "SELECT MAX(attempt_ts) FROM g1_q_capture_attempts "
            "WHERE attempt_origin='background_collector' AND target_instrument=?",
            (instrument,),
        ).fetchone()
    return _finite(row[0]) if row and row[0] is not None else None


def _attempt_due(self: _ENGINE, instrument: str, now: float) -> bool:
    latest = _latest_attempt_ts(self, instrument)
    return latest is None or float(now) - latest >= Q_COLLECTOR_CADENCE_SEC


def _record(
    self: _ENGINE,
    *,
    instrument: str,
    attempt_ts: float,
    capability: dict,
    blocker: str | None,
    provenance: dict | None = None,
    option_metrics: dict | None = None,
    observation_id: str | None = None,
    detail: dict | None = None,
    started: float | None = None,
) -> None:
    provenance = provenance or {}
    option_meta = provenance.get("options") or {}
    price_meta = provenance.get("price") or {}
    source_age = _finite(option_meta.get("age_sec"))
    source_spot = _finite((option_metrics or {}).get("proxy_spot"))
    if source_spot is None:
        source_spot = _finite((option_metrics or {}).get("spot"))
    density = (option_metrics or {}).get("density")
    distribution_built = bool(
        isinstance(density, dict)
        and isinstance(density.get("strikes"), list)
        and isinstance(density.get("q"), list)
    )
    self._g1b1_last_collector_contract = Q_COLLECTOR_CONTRACT_VERSION
    _q._record_attempt(self, {
        "attempt_id": "q-attempt-" + uuid.uuid4().hex,
        "attempt_ts": float(attempt_ts),
        "attempt_origin": "background_collector",
        "target_instrument": instrument,
        "q_source_instrument": capability.get("q_source_instrument"),
        "relation": capability.get("relation") or "NONE",
        "proxy_transform": capability.get("proxy_transform"),
        "provider": option_meta.get("source") or capability.get("provider"),
        "requested_expiry_ts": _finite((option_metrics or {}).get("expiry_ts_utc")),
        "source_available": isinstance(option_metrics, dict),
        "source_fresh": bool(
            isinstance(option_metrics, dict)
            and source_age is not None
            and source_age <= _q.Q_SOURCE_SNAPSHOT_MAX_AGE_SEC
        ),
        "target_price_available": _finite(price_meta.get("value")) is not None
        or bool(price_meta.get("source")),
        "source_price_available": source_spot is not None and source_spot > 0.0,
        "chain_available": isinstance(option_metrics, dict),
        "distribution_built": distribution_built,
        "distribution_valid": observation_id is not None,
        "observation_created": observation_id is not None,
        "created_observation_id": observation_id,
        "blocker_code": blocker,
        "latency_ms": (
            round((time.monotonic() - started) * 1000.0, 3)
            if started is not None else None
        ),
        "detail": {
            "q_collector_contract_version": Q_COLLECTOR_CONTRACT_VERSION,
            **(detail or {}),
        },
    })


def _native_features(
    *,
    freeze_ts: float,
    source_ts: float,
    price: float,
    option_metrics: dict,
    session_state: dict,
    regime: str,
) -> dict:
    metrics = deepcopy(option_metrics)
    expiry = _finite(metrics.get("expiry_ts_utc"))
    if expiry is not None and expiry > freeze_ts:
        metrics["t_years"] = (expiry - freeze_ts) / (365.0 * 86400.0)
        metrics["calendar_ttm_source"] = "expiry_ts_utc_minus_capture"
    return {
        "source_observation_ts": source_ts,
        "price_state": {
            "ts": source_ts,
            "price": price,
            "available": True,
        },
        "volatility": {
            "reference_volatility_annual": None,
            "volatility_status": "not_required_for_terminal_q",
        },
        "options": {"available": True, "stale": False},
        "option_distribution": deepcopy(metrics),
        "option_derivatives": {"available": True, "data": deepcopy(metrics)},
        "market_regime": regime,
        "session": session_state.get("session"),
        "session_state": session_state,
        "missing_is_not_zero": True,
        "holiday_calendar_supported": False,
        "measurement_runtime_contract": _q.MEASUREMENT_RUNTIME_VERSION,
        "q_collector_contract_version": Q_COLLECTOR_CONTRACT_VERSION,
    }


def _insert_native_q_only(
    self: _ENGINE,
    *,
    instrument: str,
    freeze_ts: float,
    source_ts: float,
    price: float,
    provenance: dict,
    option_metrics: dict,
    session_state: dict,
    regime: str,
) -> tuple[str | None, str | None, dict]:
    metrics = deepcopy(option_metrics)
    expiry = _finite(metrics.get("expiry_ts_utc"))
    if expiry is None or expiry <= freeze_ts:
        return None, "EXPIRY_INVALID", {}
    trading_ttm_mins = max(
        1,
        int(round(_pl._trading_seconds_between(instrument, freeze_ts, expiry) / 60.0)),
    )
    features = _native_features(
        freeze_ts=freeze_ts,
        source_ts=source_ts,
        price=price,
        option_metrics=metrics,
        session_state=session_state,
        regime=regime,
    )
    forecast = self._forecast(
        price,
        features["volatility"],
        trading_ttm_mins,
        metrics,
        instrument,
        horizon_kind="option_native_expiry",
    )
    forecast = {
        **forecast,
        "horizon_minutes": trading_ttm_mins,
        "producer_t_years_act365": metrics.get("t_years"),
        "forecast_made_at": freeze_ts,
        "q_collector_contract_version": Q_COLLECTOR_CONTRACT_VERSION,
    }
    if forecast.get("probability_measure") != "risk_neutral_Q_terminal":
        return None, "CDF_BUILD_FAILED", {
            "horizon_alignment_method": forecast.get("horizon_alignment_method"),
        }
    if not _q.valid_terminal_cdf(forecast.get("terminal_q_cdf")):
        return None, "CDF_INVALID", {
            "horizon_alignment_method": forecast.get("horizon_alignment_method"),
        }

    # Use the same F.3.2a no-lookahead timestamp walker as normal capture. Future
    # expiry reference fields are explicitly allowed there; future observations are not.
    for path, ts in _pl._walk_timestamps(features):
        if ts > freeze_ts + 1e-6:
            return None, "TIME_CONTRACT_INVALID", {"future_feature_timestamp": path}
    for path, ts in _pl._walk_timestamps(forecast):
        if ts > freeze_ts + 1e-6:
            return None, "TIME_CONTRACT_INVALID", {"future_forecast_timestamp": path}

    price_meta = provenance.get("price") or {}
    option_meta = provenance.get("options") or {}
    quality = _finite(price_meta.get("quality")) or 0.0
    age = _finite(price_meta.get("age_sec"))
    evidence_eligible = bool(
        not self.settings.demo
        and price_meta.get("kind") == "direct"
        and quality >= 0.90
        and age is not None
        and age <= 60.0
    )
    if not evidence_eligible:
        return None, "TARGET_PRICE_LOW_QUALITY", {
            "price_quality": quality,
            "price_age_sec": age,
            "price_kind": price_meta.get("kind"),
        }

    day = int(freeze_ts // 86400)
    with self._lock, self._conn:
        day_n = self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations "
            "WHERE CAST(captured_ts/86400 AS INTEGER)=?",
            (day,),
        ).fetchone()[0]
        if day_n + 1 > self.budget["max_passive_observations_per_day"]:
            return None, "CAPTURE_BUDGET_EXCEEDED", {"day_observation_n": int(day_n)}

        anchor = "q-market-" + hashlib.sha256(
            f"{instrument}|{freeze_ts:.6f}|{price:.10f}|{expiry:.6f}".encode()
        ).hexdigest()[:24]
        observation_id = f"{anchor}-native-expiry"
        self._conn.execute(
            "INSERT INTO passive_market_observations("
            "observation_id,anchor_group_id,captured_ts,target_ts,instrument,"
            "horizon_minutes,trigger_reason,market_price,price_source,"
            "price_age_sec,price_quality,price_kind,option_source,option_age_sec,"
            "option_quality,option_kind,market_regime,session,feature_contract_version,"
            "forecast_model_version,calibrator_version,scenario_version,features_json,"
            "forecast_json,evidence_eligible,resolution_status,observation_origin,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id,
                anchor,
                freeze_ts,
                expiry,
                instrument,
                trading_ttm_mins,
                "cadence",
                price,
                price_meta.get("source"),
                age,
                quality,
                price_meta.get("kind"),
                option_meta.get("source"),
                option_meta.get("age_sec"),
                option_meta.get("quality"),
                option_meta.get("kind"),
                regime,
                session_state.get("session"),
                _pl.PASSIVE_SCHEMA_VERSION,
                _pl.FORECAST_VERSION,
                "identity-only-unpromoted",
                "standardized-geometry-f31-v1",
                _q._json(features),
                _q._json(forecast),
                1,
                "pending",
                "background_collector",
                time.time(),
            ),
        )
    self._last_successful_capture_ts = freeze_ts
    return observation_id, None, {
        "native_target_ts": expiry,
        "horizon_minutes": trading_ttm_mins,
        "probability_measure": forecast.get("probability_measure"),
        "q_source_instrument": forecast.get("q_source_instrument") or forecast.get("proxy_symbol"),
        "q_target_instrument": forecast.get("q_target_instrument"),
        "proxy_transform": forecast.get("proxy_transform"),
    }


def q_collect_instrument(self: _ENGINE, instrument: str, now: float | None = None) -> dict:
    now = float(now or time.time())
    if instrument not in INSTRUMENTS:
        raise ValueError("unsupported instrument")
    if not _attempt_due(self, instrument, now):
        return {"instrument": instrument, "attempted": False, "reason": "cadence_not_due"}

    started = time.monotonic()
    capability = _q._capability_for(instrument)
    if not capability.get("configured"):
        _record(
            self,
            instrument=instrument,
            attempt_ts=now,
            capability=capability,
            blocker="NO_Q_SOURCE_CONFIGURED",
            detail={"collector_reason": "no_configured_option_source"},
            started=started,
        )
        return {"instrument": instrument, "attempted": True, "blocker": "NO_Q_SOURCE_CONFIGURED"}

    session_state = _pl._session_state(instrument, now)
    if not self.settings.demo and not session_state.get("is_open"):
        _record(
            self,
            instrument=instrument,
            attempt_ts=now,
            capability=capability,
            blocker="MARKET_CLOSED",
            detail={"session_state": session_state},
            started=started,
        )
        return {"instrument": instrument, "attempted": True, "blocker": "MARKET_CLOSED"}

    feed = self._feed(instrument)
    try:
        feed.refresh_price()
    except Exception as exc:  # noqa: BLE001
        _record(
            self,
            instrument=instrument,
            attempt_ts=now,
            capability=capability,
            blocker="TARGET_PRICE_UNAVAILABLE",
            detail={"price_exception": f"{type(exc).__name__}: {str(exc)[:180]}"},
            started=started,
        )
        return {"instrument": instrument, "attempted": True, "blocker": "TARGET_PRICE_UNAVAILABLE"}

    state = feed.price or {}
    price = _finite(state.get("value"))
    source_ts = _finite(state.get("ts"))
    if price is None or price <= 0.0 or source_ts is None:
        _record(
            self,
            instrument=instrument,
            attempt_ts=now,
            capability=capability,
            blocker="TARGET_PRICE_UNAVAILABLE",
            detail={"price_source": state.get("source")},
            started=started,
        )
        return {"instrument": instrument, "attempted": True, "blocker": "TARGET_PRICE_UNAVAILABLE"}

    try:
        feed.refresh_proxy_price()
        feed.refresh_chain()
    except Exception as exc:  # noqa: BLE001
        kind = self._source_kind(state.get("source"), self.settings.demo)
        quality = self._quality(state)
        provenance = {
            "price": {
                "source": state.get("source"),
                "value": price,
                "quality": quality,
                "age_sec": max(0.0, now - source_ts),
                "kind": kind,
            },
            "options": {"source": None, "quality": 0.0, "age_sec": None, "kind": "unavailable"},
        }
        _record(
            self,
            instrument=instrument,
            attempt_ts=now,
            capability=capability,
            blocker="Q_PROVIDER_UNAVAILABLE",
            provenance=provenance,
            detail={"provider_exception": f"{type(exc).__name__}: {str(exc)[:180]}"},
            started=started,
        )
        return {"instrument": instrument, "attempted": True, "blocker": "Q_PROVIDER_UNAVAILABLE"}

    freeze_ts = time.time()
    chain = feed.chain or {}
    option_metrics = chain.get("metrics") if isinstance(chain, dict) else None
    kind = self._source_kind(state.get("source"), self.settings.demo)
    quality = self._quality(state)
    option_age = (
        max(0.0, freeze_ts - float(chain.get("ts")))
        if isinstance(chain, dict) and chain.get("ts") is not None else None
    )
    provenance = {
        "price": {
            "source": state.get("source"),
            "value": price,
            "quality": quality,
            "age_sec": max(0.0, freeze_ts - source_ts),
            "kind": kind,
        },
        "options": {
            "source": chain.get("source") if isinstance(chain, dict) else None,
            "quality": self._quality(chain) if option_metrics else 0.0,
            "age_sec": option_age,
            "kind": "proxy" if option_metrics else "unavailable",
        },
    }
    features = {
        "source_observation_ts": source_ts,
        "price_state": {"ts": source_ts, "price": price, "available": True},
        "option_derivatives": {"available": option_metrics is not None, "data": option_metrics},
        "option_distribution": option_metrics if option_metrics else {"available": False},
    }
    blocker, pre_detail = _q._classify_pre_capture(
        instrument=instrument,
        captured_ts=freeze_ts,
        market_price=price,
        features=deepcopy(features),
        provenance=deepcopy(provenance),
    )
    if blocker is not None:
        _record(
            self,
            instrument=instrument,
            attempt_ts=freeze_ts,
            capability=capability,
            blocker=blocker,
            provenance=provenance,
            option_metrics=option_metrics,
            detail=pre_detail,
            started=started,
        )
        return {"instrument": instrument, "attempted": True, "blocker": blocker}

    observation_id, blocker, detail = _insert_native_q_only(
        self,
        instrument=instrument,
        freeze_ts=freeze_ts,
        source_ts=source_ts,
        price=price,
        provenance=provenance,
        option_metrics=option_metrics,
        session_state=session_state,
        regime=(getattr(feed, "market_regime", "UNCLASSIFIED")
                if isinstance(getattr(feed, "market_regime", "UNCLASSIFIED"), str)
                else "UNCLASSIFIED"),
    )
    _record(
        self,
        instrument=instrument,
        attempt_ts=freeze_ts,
        capability=capability,
        blocker=blocker,
        provenance=provenance,
        option_metrics=option_metrics,
        observation_id=observation_id,
        detail=detail,
        started=started,
    )
    return {
        "instrument": instrument,
        "attempted": True,
        "observation_id": observation_id,
        "blocker": blocker,
    }


def collect_instrument_with_independent_q(
    self: _ENGINE, instrument: str, now: float
) -> list[str]:
    created = _ORIGINAL_COLLECT(self, instrument, now)
    # Normal passive capture already writes a G.1B.1 attempt. Avoid a second
    # network call/observation when that happened within this collector cycle.
    if _attempt_due(self, instrument, now):
        try:
            result = q_collect_instrument(self, instrument, now)
            q_obs = result.get("observation_id")
            if q_obs:
                created = list(created) + [str(q_obs)]
            self._g1b1_last_q_collector_error = None
        except Exception as exc:  # noqa: BLE001
            self._g1b1_last_q_collector_error = f"{type(exc).__name__}: {str(exc)[:180]}"
    return created


def _status_with_collector(self: _ENGINE) -> dict:
    status = _q._ENGINE.g1_q_status(self)
    # This function is replaced during installation below; call the captured
    # predecessor through the class marker to avoid recursion.
    return status


def install_g1_q_collector() -> None:
    if getattr(_ENGINE, "_g1_q_collector_runtime", None) == Q_COLLECTOR_CONTRACT_VERSION:
        return
    _q._BLOCKERS.update({"MARKET_CLOSED", "TARGET_PRICE_LOW_QUALITY", "CAPTURE_BUDGET_EXCEEDED"})
    previous_status = _ENGINE.g1_q_status

    def status(self: _ENGINE) -> dict:
        result = previous_status(self)
        result["q_collector_contract_version"] = Q_COLLECTOR_CONTRACT_VERSION
        result["q_collector_cadence_sec"] = Q_COLLECTOR_CADENCE_SEC
        result["q_collector_last_error"] = getattr(self, "_g1b1_last_q_collector_error", None)
        return result

    _ENGINE.g1_q_collect_instrument = q_collect_instrument
    _ENGINE._collect_instrument = collect_instrument_with_independent_q
    _ENGINE.g1_q_status = status
    _ENGINE._g1_q_collector_runtime = Q_COLLECTOR_CONTRACT_VERSION
