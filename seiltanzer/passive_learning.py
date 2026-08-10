"""Continuous prospective market observations, outcomes and Q calibration (Phase F.3 / G.0).

This is deliberately separate from real trades. Synthetic/demo data may exercise the
pipeline but is never eligible research evidence.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
import statistics
import threading
import time
import uuid
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from .config import INSTRUMENTS, Settings
from .data.feeds import MarketData
from .option_q_adapter import (
    OPTION_Q_CONTRACT_VERSION,
    adapt_option_q_forecast,
)
from .variance_clock import (
    VARIANCE_CLOCK_VERSION,
    compute_annual_volatility,
    compute_horizon_sigma,
    get_variance_clock_spec,
)

HORIZONS_MINUTES = (15, 30, 60, 120, 240, 480, 1440)
GEOMETRY_SIGMAS = (.5, 1.0, 1.5, 2.0)
OBSERVATION_CADENCE_SEC = 15 * 60
MAX_GAP_SEC = 5 * 60
PASSIVE_SCHEMA_VERSION = "passive-observation-f3-v1"
FORECAST_VERSION = "passive-forecast-f3-v1"
RESOLVER_VERSION = "passive-resolver-f3-v1"
SESSION_CONTRACT_VERSION = "market-session-f3-v1"
EVENT_TRIGGER_CONTRACT_VERSION = "passive-event-trigger-f3-v1"
EVENT_MIN_SPACING_SEC = 5 * 60
VIRTUAL_HORIZONS_MINUTES = (60, 240)
VIRTUAL_R0_STATES = (-.5, 0.0, .5, 1.0)
VIRTUAL_POLICY_FRACTIONS = {
    "HOLD": 0.0, "CLOSE_10": .10, "CLOSE_25": .25,
    "CLOSE_50": .50, "EXIT": 1.0,
}

# Exchange-local regular sessions. FX/metals use an explicit weekday 24h
# research clock until a holiday calendar is connected.
_SESSION_SPECS = {
    "NAS100": ("America/New_York", ((9*60+30, 16*60),)),
    "SP500": ("America/New_York", ((9*60+30, 16*60),)),
    "US30": ("America/New_York", ((9*60+30, 16*60),)),
    "GER40": ("Europe/Berlin", ((9*60, 17*60+30),)),
    "UK100": ("Europe/London", ((8*60, 16*60+30),)),
    "JPY100": ("Asia/Tokyo", ((9*60, 11*60+30), (12*60+30, 15*60))),
    "XAU": ("UTC", ((0, 24*60),)),
    "XAG": ("UTC", ((0, 24*60),)),
    "EURUSD": ("UTC", ((0, 24*60),)),
    "USDCAD": ("UTC", ((0, 24*60),)),
}


def _session_state(instrument: str, timestamp: float) -> dict:
    zone_name, windows = _SESSION_SPECS[instrument]
    local = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).astimezone(
        ZoneInfo(zone_name))
    minute = local.hour*60 + local.minute + local.second/60
    weekday_open = local.weekday() < 5
    active = weekday_open and any(start <= minute < end for start, end in windows)
    label = "OPEN" if active else (
        "WEEKEND_CLOSED" if not weekday_open else "OUT_OF_SESSION")
    return {
        "contract_version": SESSION_CONTRACT_VERSION,
        "timezone": zone_name, "session": label, "is_open": active,
        "local_date": local.date().isoformat(),
        "holiday_calendar_supported": False,
    }


def _trading_seconds_between(instrument: str, start: float, end: float) -> float:
    if end <= start:
        return 0.0
    cursor = math.floor(start/60)*60.0
    total = 0.0
    while cursor < end:
        left, right = max(start, cursor), min(end, cursor+60.0)
        if right > left and _session_state(instrument, (left+right)/2)["is_open"]:
            total += right-left
        cursor += 60.0
    return total


def _advance_trading_time(instrument: str, start: float,
                          trading_minutes: int) -> float:
    required = float(trading_minutes)*60.0
    cursor, accumulated = float(start), 0.0
    deadline = cursor + 10*86400
    while accumulated < required and cursor < deadline:
        right = min(math.floor(cursor/60)*60+60.0, deadline)
        if _session_state(instrument, (cursor+right)/2)["is_open"]:
            accumulated += right-cursor
        cursor = right
    if accumulated + 1e-6 < required:
        raise ValueError("unable to advance trading horizon")
    return cursor


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _walk_timestamps(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key.endswith("_ts") or key in {"ts", "timestamp", "as_of"}:
                number = _finite(child)
                if number is not None:
                    yield child_path, number
            yield from _walk_timestamps(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_timestamps(child, f"{path}[{index}]")


def _binary_score(probabilities: list[float], outcomes: list[float]) -> dict:
    if not probabilities:
        return {"n": 0, "brier": None, "log_loss": None}
    eps = 1e-12
    brier = sum((p-y)**2 for p, y in zip(probabilities, outcomes))/len(outcomes)
    logloss = -sum(y*math.log(max(eps, min(1-eps, p))) +
                   (1-y)*math.log(max(eps, min(1-eps, 1-p)))
                   for p, y in zip(probabilities, outcomes))/len(outcomes)
    return {"n": len(outcomes), "brier": round(brier, 8),
            "log_loss": round(logloss, 8)}


def _pinball_score(quantiles: list[float], outcomes: list[float],
                   tau: float) -> dict:
    if not quantiles:
        return {"n": 0, "pinball_loss": None}
    loss = sum(max(tau*(y-q), (tau-1.0)*(y-q))
               for q,y in zip(quantiles,outcomes))/len(outcomes)
    return {"n": len(outcomes), "pinball_loss": round(loss, 10)}


class PassiveLearningEngine:
    """Low-priority collector and immutable prospective research store (Phase F.3 / G.0)."""

    def __init__(self, path: str, settings: Settings, cache):
        self.settings, self.cache = settings, cache
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._feeds: dict[str, MarketData] = {}
        self._instrument_cursor = 0
        self._last_step_error: str | None = None
        self._last_step_ts: float | None = None
        self._last_successful_capture_ts: float | None = None
        self._last_successful_resolution_ts: float | None = None
        self._contract_error_counters = defaultdict(int)

        self.budget = {
            "max_instruments_concurrently": 1,
            "max_background_requests": 1,
            "max_sqlite_writes_per_minute": 30,
            "max_passive_observations_per_day": 5000,
            "base_observation_cadence_sec": OBSERVATION_CADENCE_SEC,
        }
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS passive_market_observations (
                    observation_id TEXT PRIMARY KEY,
                    anchor_group_id TEXT NOT NULL,
                    captured_ts REAL NOT NULL,
                    target_ts REAL NOT NULL,
                    instrument TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    market_price REAL NOT NULL,
                    price_source TEXT, price_age_sec REAL, price_quality REAL,
                    price_kind TEXT, option_source TEXT, option_age_sec REAL,
                    option_quality REAL, option_kind TEXT,
                    market_regime TEXT, session TEXT,
                    feature_contract_version TEXT NOT NULL,
                    forecast_model_version TEXT NOT NULL,
                    calibrator_version TEXT NOT NULL,
                    scenario_version TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    forecast_json TEXT NOT NULL,
                    evidence_eligible INTEGER NOT NULL,
                    resolution_status TEXT NOT NULL DEFAULT 'pending',
                    resolved_ts REAL, outcome_json TEXT,
                    calendar_elapsed REAL, trading_elapsed REAL,
                    market_open_fraction REAL,
                    retrospective_replay INTEGER NOT NULL DEFAULT 0,
                    created_ts REAL NOT NULL)""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_passive_pending "
                "ON passive_market_observations(resolution_status,target_ts)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_passive_instrument_capture "
                "ON passive_market_observations(instrument,captured_ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS passive_market_path (
                    instrument TEXT NOT NULL, ts REAL NOT NULL, price REAL NOT NULL,
                    source TEXT, quality REAL, kind TEXT NOT NULL,
                    PRIMARY KEY(instrument,ts))""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS virtual_position_observations (
                    virtual_id TEXT PRIMARY KEY,
                    passive_observation_id TEXT NOT NULL,
                    captured_ts REAL NOT NULL, instrument TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL, direction TEXT NOT NULL,
                    r0 REAL NOT NULL, stop_r REAL NOT NULL, take_r REAL NOT NULL,
                    position_origin TEXT NOT NULL,
                    policy_contract_version TEXT NOT NULL,
                    features_json TEXT NOT NULL, evidence_eligible INTEGER NOT NULL,
                    resolution_status TEXT NOT NULL DEFAULT 'pending',
                    resolved_ts REAL, outcome_json TEXT,
                    created_ts REAL NOT NULL)""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_virtual_pending "
                "ON virtual_position_observations(resolution_status,"
                "passive_observation_id)")
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS virtual_t0_immutable
                BEFORE UPDATE OF passive_observation_id,captured_ts,instrument,
                    horizon_minutes,direction,r0,stop_r,take_r,position_origin,
                    policy_contract_version,features_json,evidence_eligible
                ON virtual_position_observations
                BEGIN
                    SELECT RAISE(ABORT,'immutable virtual T0 state');
                END""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS passive_collector_state (
                    key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
                    updated_ts REAL NOT NULL)""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS research_model_registry (
                    model_id TEXT PRIMARY KEY, model_family TEXT NOT NULL,
                    created_at REAL NOT NULL, feature_contract TEXT NOT NULL,
                    dataset_version TEXT NOT NULL, training_start REAL,
                    training_end REAL, validation_start REAL, validation_end REAL,
                    test_start REAL, test_end REAL, hyperparameters_json TEXT NOT NULL,
                    random_seed INTEGER, metrics_json TEXT NOT NULL,
                    authority TEXT NOT NULL DEFAULT 'research_only',
                    status TEXT NOT NULL DEFAULT 'registered')""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS research_dataset_manifests (
                    dataset_version TEXT PRIMARY KEY, created_at REAL NOT NULL,
                    observation_ids_hash TEXT NOT NULL, date_start REAL,
                    date_end REAL, instruments_json TEXT NOT NULL,
                    horizons_json TEXT NOT NULL, filters_json TEXT NOT NULL,
                    immutable INTEGER NOT NULL DEFAULT 1)""")
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS passive_t0_immutable
                BEFORE UPDATE OF captured_ts,target_ts,instrument,horizon_minutes,
                    market_price,features_json,forecast_json,evidence_eligible,
                    feature_contract_version,forecast_model_version
                ON passive_market_observations
                BEGIN
                    SELECT RAISE(ABORT,'immutable passive T0 forecast');
                END""")

    def _feed(self, instrument: str) -> MarketData:
        feed = self._feeds.get(instrument)
        if feed is None:
            feed = MarketData(self.settings, self.cache)
            feed.set_instrument(instrument)
            self._feeds[instrument] = feed
        return feed

    @staticmethod
    def _source_kind(source: str | None, demo: bool) -> str:
        if demo:
            return "demo"
        source = str(source or "")
        if source.startswith(("TradingView", "Swissquote", "stream")):
            return "direct"
        return "proxy"

    @staticmethod
    def _quality(state: dict) -> float:
        status = state.get("status")
        value = {"live": .98, "delayed": .72, "cached": .55,
                 "demo": 0.0}.get(status, .0)
        if state.get("fresh") is False:
            value *= .5
        return round(value, 4)

    def record_market_point(self, instrument: str, ts: float, price: float,
                            *, source: str = "observed", quality: float = 1.0,
                            kind: str = "direct") -> None:
        if instrument not in INSTRUMENTS:
            raise ValueError("unsupported instrument")
        if not all(math.isfinite(float(x)) for x in (ts, price, quality)):
            raise ValueError("market point must be finite")
        if price <= 0:
            raise ValueError("market price must be positive")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO passive_market_path"
                "(instrument,ts,price,source,quality,kind) VALUES(?,?,?,?,?,?)",
                (instrument, float(ts), float(price), source,
                 max(0., min(1., float(quality))), kind))

    @staticmethod
    def _reference_volatility(feed: MarketData) -> dict:
        """Authoritative adapter reading actual MarketData.daily contract."""
        bars = (feed.daily or {}).get("bars")
        if not bars or not isinstance(bars, dict):
            return {
                "reference_volatility_annual": None,
                "volatility_status": "missing_daily_bars",
                "variance_clock_version": VARIANCE_CLOCK_VERSION,
            }
        closes = bars.get("closes") or bars.get("Close")
        if not closes:
            return {
                "reference_volatility_annual": None,
                "volatility_status": "missing_closes",
                "variance_clock_version": VARIANCE_CLOCK_VERSION,
            }
        return compute_annual_volatility(closes, feed.instrument_code)

    @staticmethod
    def _forecast(price: float, vol_info: dict | None,
                  horizon_minutes: int, option_metrics: dict | None,
                  instrument: str) -> dict:
        annual_vol = (vol_info.get("reference_volatility_annual")
                      if isinstance(vol_info, dict) else _finite(vol_info))

        sigma_res = compute_horizon_sigma(annual_vol, horizon_minutes, instrument)
        sigma_h = sigma_res.get("sigma_h_return")

        q_res = adapt_option_q_forecast(option_metrics, horizon_minutes, sigma_h, instrument)

        gaussian_reference = {}
        if sigma_h is not None and math.isfinite(sigma_h) and sigma_h > 0:
            for name, z in (("q10", -1.2815515655), ("q25", -.6744897502),
                            ("q50", 0.), ("q75", .6744897502), ("q90", 1.2815515655)):
                gaussian_reference[name] = z * sigma_h
        else:
            gaussian_reference = {name: None for name in ("q10", "q25", "q50", "q75", "q90")}

        vol_status = (vol_info.get("volatility_status", "missing_annual_volatility")
                      if isinstance(vol_info, dict)
                      else ("valid" if annual_vol else "missing"))

        return {
            "version": FORECAST_VERSION,
            "authority": "shadow_prediction",
            "probability_measure": q_res["probability_measure"],
            "q_source_contract": q_res["q_source_contract"],
            "physical_probability_published": False,
            "reference_volatility_annual": annual_vol,
            "reference_volatility_units": "log_return_per_sqrt_year",
            "volatility_status": vol_status,
            "variance_clock_version": VARIANCE_CLOCK_VERSION,
            "variance_clock_spec": get_variance_clock_spec(instrument),
            "horizon_minutes": horizon_minutes,
            "horizon_trading_minutes": horizon_minutes,
            "sigma_h_return": sigma_h,
            "horizon_alignment_status": q_res.get("horizon_alignment_status", "unavailable"),
            "horizon_alignment_method": q_res.get("horizon_alignment_method", "none"),
            "horizon_alignment_error": q_res.get("horizon_alignment_error", 0.0),
            "source_expiry_ttm_minutes": q_res.get("source_expiry_ttm_minutes", 0.0),
            "standardized_barriers": q_res.get("standardized_barriers"),
            "quantiles_log_return": q_res.get("quantiles_log_return"),
            "gaussian_reference_quantiles_log_return": gaussian_reference,
            "option_implied_center": q_res.get("mode_price"),
            "option_implied_width": q_res.get("implied_move_frac"),
            "skew": q_res.get("skew"),
        }

    def capture_observation(self, *, instrument: str, captured_ts: float,
                            market_price: float, features: dict, forecast: dict,
                            provenance: dict, trigger_reason: str = "cadence",
                            evidence_eligible: bool = True) -> list[str]:
        if instrument not in INSTRUMENTS:
            raise ValueError("unsupported instrument")
        if not math.isfinite(captured_ts) or not math.isfinite(market_price):
            raise ValueError("capture time and price must be finite")
        for path, ts in _walk_timestamps(features):
            if ts > captured_ts + 1e-6:
                raise ValueError(f"post-capture feature timestamp: {path}")
        for path, ts in _walk_timestamps(forecast):
            if ts > captured_ts + 1e-6:
                raise ValueError(f"post-capture forecast timestamp: {path}")
        if trigger_reason not in {
            "cadence", "volatility_regime_switch", "correlation_break",
            "large_iv_move", "skew_move", "large_price_displacement",
            "gex_geometry_change", "macro_regime_transition", "test"}:
            raise ValueError("unversioned observation trigger")
        now_day = int(captured_ts//86400)
        with self._lock, self._conn:
            day_n = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE CAST(captured_ts/86400 AS INTEGER)=?",
                (now_day,)).fetchone()[0]
            if day_n + len(HORIZONS_MINUTES) > self.budget[
                    "max_passive_observations_per_day"]:
                return []
            anchor = "market-" + hashlib.sha256(
                f"{instrument}|{captured_ts:.6f}|{market_price:.10f}".encode()
            ).hexdigest()[:24]
            ids = []
            for horizon in HORIZONS_MINUTES:
                observation_id = f"{anchor}-{horizon}m"
                target = (
                    captured_ts + horizon*60.
                    if trigger_reason == "test"
                    else _advance_trading_time(instrument, captured_ts, horizon)
                )

                annual = _finite(forecast.get("reference_volatility_annual")) or (features.get("volatility") or {}).get("reference_annual")
                vol_info = features.get("volatility") or {"reference_volatility_annual": annual}
                option_metrics = features.get("option_derivatives", {}).get("data")

                if forecast and forecast.get("version"):
                    frozen_forecast = dict(forecast)
                    if "sigma_h_return" not in frozen_forecast or frozen_forecast["sigma_h_return"] is None:
                        sigma_res = compute_horizon_sigma(annual, horizon, instrument)
                        frozen_forecast["sigma_h_return"] = sigma_res.get("sigma_h_return")
                else:
                    frozen_forecast = self._forecast(market_price, vol_info, horizon, option_metrics, instrument)

                self._conn.execute(
                    "INSERT OR IGNORE INTO passive_market_observations("
                    "observation_id,anchor_group_id,captured_ts,target_ts,instrument,"
                    "horizon_minutes,trigger_reason,market_price,price_source,"
                    "price_age_sec,price_quality,price_kind,option_source,"
                    "option_age_sec,option_quality,option_kind,market_regime,session,"
                    "feature_contract_version,forecast_model_version,"
                    "calibrator_version,scenario_version,features_json,forecast_json,"
                    "evidence_eligible,resolution_status,created_ts)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (observation_id, anchor, captured_ts, target, instrument, horizon,
                     trigger_reason, market_price,
                     (provenance.get("price") or {}).get("source"),
                     (provenance.get("price") or {}).get("age_sec"),
                     (provenance.get("price") or {}).get("quality"),
                     (provenance.get("price") or {}).get("kind"),
                     (provenance.get("options") or {}).get("source"),
                     (provenance.get("options") or {}).get("age_sec"),
                     (provenance.get("options") or {}).get("quality"),
                     (provenance.get("options") or {}).get("kind"),
                     features.get("market_regime"), features.get("session"),
                     PASSIVE_SCHEMA_VERSION, forecast.get("version", FORECAST_VERSION),
                     "identity-only-unpromoted", "standardized-geometry-f3-v1",
                     _json(features), _json({**frozen_forecast,
                        "horizon_minutes": horizon,
                        "forecast_made_at": captured_ts}),
                     int(bool(evidence_eligible)), "pending", time.time()))

                if horizon in VIRTUAL_HORIZONS_MINUTES:
                    for direction in ("long", "short"):
                        for r0 in VIRTUAL_R0_STATES:
                            virtual_id = (
                                f"virtual-{observation_id}-{direction}-"
                                f"{r0:+.1f}r")
                            virtual_features = {
                                "passive_observation_id": observation_id,
                                "position_origin":
                                    "synthetic_position_state_on_real_market_path",
                                "direction": direction, "r0": r0,
                                "stop_r": -1.0, "take_r": 2.5,
                                "policy_set": list(VIRTUAL_POLICY_FRACTIONS),
                                "fraction_semantics":
                                    "fraction_of_current_remaining_position",
                                "future_path_source":
                                    "recorded_real_market_path_only",
                            }
                            self._conn.execute(
                                "INSERT OR IGNORE INTO virtual_position_observations("
                                "virtual_id,passive_observation_id,captured_ts,"
                                "instrument,horizon_minutes,direction,r0,stop_r,"
                                "take_r,position_origin,policy_contract_version,"
                                "features_json,evidence_eligible,resolution_status,"
                                "created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (virtual_id,observation_id,captured_ts,instrument,
                                 horizon,direction,r0,-1.0,2.5,
                                 "synthetic_position_state_on_real_market_path",
                                 "virtual-management-f3-v1",
                                 _json(virtual_features),
                                 int(bool(evidence_eligible)),"pending",time.time()))
                ids.append(observation_id)
            self._last_successful_capture_ts = captured_ts
        return ids

    @staticmethod
    def _event_trigger_reason(*, now: float, last_15m: dict | None = None,
                               last: dict | None = None,
                               price: float) -> str | None:
        """Determines event trigger strictly against authoritative 15m geometry."""
        last_row = last_15m or last
        if not last_row:
            return "cadence"
        age = now - float(last_row["captured_ts"])
        if age >= OBSERVATION_CADENCE_SEC:
            return "cadence"
        if age < EVENT_MIN_SPACING_SEC:
            return None
        forecast = json.loads(last_row.get("forecast_json") or "{}")
        sigma = _finite(forecast.get("sigma_h_return"))
        threshold = max(.001, .75*sigma) if sigma is not None else .003
        previous = _finite(last_row.get("market_price"))
        if previous and abs(math.log(price/previous)) >= threshold:
            return "large_price_displacement"
        return None

    def _collect_instrument(self, instrument: str, now: float) -> list[str]:
        feed = self._feed(instrument)
        feed.refresh_price()
        state = feed.price or {}
        price = _finite(state.get("value"))
        ts = _finite(state.get("ts")) or now
        if price is None or price <= 0:
            return []
        session_state = _session_state(instrument, now)
        if not self.settings.demo and not session_state["is_open"]:
            return []
        kind = self._source_kind(state.get("source"), self.settings.demo)
        quality = self._quality(state)
        self.record_market_point(instrument, ts, price, source=state.get("source") or "",
                                 quality=quality, kind=kind)

        with self._lock:
            source_row = self._conn.execute(
                "SELECT captured_ts,market_price,forecast_json "
                "FROM passive_market_observations WHERE instrument=? AND horizon_minutes=15 "
                "ORDER BY captured_ts DESC LIMIT 1", (instrument,)).fetchone()
            last_15m = dict(source_row) if source_row else None

        trigger_reason = self._event_trigger_reason(
            now=now, last_15m=last_15m, price=price)
        if trigger_reason is None:
            return []

        feed.refresh_daily()
        vol_info = self._reference_volatility(feed)
        annual_vol = vol_info.get("reference_volatility_annual")
        if annual_vol is None:
            self._contract_error_counters["producer_contract_error_n"] += 1

        option_metrics = None
        if feed.instrument.options_proxy:
            try:
                feed.refresh_proxy_price()
                feed.refresh_chain()
                option_metrics = (feed.chain or {}).get("metrics")
            except Exception:
                option_metrics = None

        forecast_15m = self._forecast(price, vol_info, 15, option_metrics, instrument)
        if forecast_15m.get("probability_measure") != "risk_neutral_Q":
            self._contract_error_counters["horizon_alignment_unavailable_n"] += 1

        provenance = {
            "price": {"source": state.get("source"), "quality": quality,
                      "age_sec": max(0., now-ts), "kind": kind},
            "options": {
                "source": (feed.chain or {}).get("source"),
                "quality": self._quality(feed.chain or {}) if option_metrics else 0.,
                "age_sec": (max(0., now-float((feed.chain or {}).get("ts")))
                            if (feed.chain or {}).get("ts") else None),
                "kind": ("proxy" if option_metrics else "unavailable")}}

        # Attach runtime feature families with No-Lookahead invariant (ts <= captured_ts)
        vrp_data = feed._vrp_payload() if hasattr(feed, "_vrp_payload") else {"available": False}
        regime_data = getattr(feed, "market_regime", "UNCLASSIFIED")
        cross_asset_data = getattr(feed, "correlation", {"available": False})
        gex_data = (feed.chain or {}).get("metrics", {}).get("gex") if option_metrics else {"available": False}

        features = {
            "source_observation_ts": ts,
            "price_state": {"price": price, "available": True},
            "volatility": vol_info,
            "options": {"available": option_metrics is not None,
                        "stale": (feed.chain or {}).get("status") == "delayed"},
            "option_distribution": option_metrics if option_metrics else {"available": False},
            "option_derivatives": {"available": option_metrics is not None, "data": option_metrics},
            "vrp_context": {"available": bool(vrp_data and vrp_data.get("available")), "data": vrp_data},
            "barrier_geometry": {"family": "volatility_normalized",
                                 "levels_sigma": list(GEOMETRY_SIGMAS)},
            "gex_context": {"available": bool(gex_data), "data": gex_data},
            "cross_asset": {"available": bool(cross_asset_data and cross_asset_data.get("available")), "data": cross_asset_data},
            "market_regime": regime_data if isinstance(regime_data, str) else "UNCLASSIFIED",
            "wavelet_context": {"available": False},
            "session": session_state["session"],
            "session_state": session_state,
            "missing_is_not_zero": True,
            "holiday_calendar_supported": False,
            "trigger_contract_version": EVENT_TRIGGER_CONTRACT_VERSION,
            "trigger_thresholds": {
                "large_price_displacement_sigma_15m": .75,
                "absolute_floor_log_return": .001,
                "minimum_event_spacing_sec": EVENT_MIN_SPACING_SEC,
            },
        }

        return self.capture_observation(
            instrument=instrument, captured_ts=ts, market_price=price,
            features=features, forecast=forecast_15m, provenance=provenance,
            trigger_reason=trigger_reason,
            evidence_eligible=not self.settings.demo)

    def step(self, now: float | None = None) -> dict:
        now = float(now or time.time())
        instruments = tuple(INSTRUMENTS)
        instrument = instruments[self._instrument_cursor % len(instruments)]
        self._instrument_cursor += 1
        created = []
        try:
            created = self._collect_instrument(instrument, now)
            self.resolve_due(now=now, limit=20)
            self._last_step_error = None
        except Exception as exc:
            self._last_step_error = f"{type(exc).__name__}: {str(exc)[:180]}"
        self._last_step_ts = now
        return {"instrument": instrument, "created": created,
                "error": self._last_step_error}

    def _resolve_one(self, row: dict, now: float) -> str:
        captured, target = float(row["captured_ts"]), float(row["target_ts"])
        with self._lock:
            points = [dict(x) for x in self._conn.execute(
                "SELECT * FROM passive_market_path WHERE instrument=? "
                "AND ts>=? AND ts<=? ORDER BY ts",
                (row["instrument"], captured-1e-6, target+1e-6)).fetchall()]
        if not points or points[-1]["ts"] < target-1e-6:
            return "pending" if now <= target+MAX_GAP_SEC else "insufficient_future_data"
        if _trading_seconds_between(
                row["instrument"], captured, float(points[0]["ts"])) > MAX_GAP_SEC:
            return "insufficient_future_data"
        gaps = [_trading_seconds_between(
                    row["instrument"], float(a["ts"]), float(b["ts"]))
                for a,b in zip(points,points[1:])]
        if gaps and max(gaps) > MAX_GAP_SEC:
            return "insufficient_future_data"

        start, end = float(row["market_price"]), float(points[-1]["price"])
        future_points = [p for p in points if p["ts"] > captured]
        prices = [start] + [float(p["price"]) for p in future_points]
        log_path = [math.log(p/start) for p in prices]
        features, forecast = json.loads(row["features_json"]), json.loads(row["forecast_json"])

        sigma_h = _finite(forecast.get("sigma_h_return"))
        barriers = {}
        ambiguous = False

        for level in GEOMETRY_SIGMAS:
            if sigma_h is None or sigma_h <= 0:
                barriers[str(level)] = {"outcome": "unavailable",
                                        "first_touch_calendar_minutes": None,
                                        "first_touch_trading_minutes": None}
                continue
            upper, lower = level*sigma_h, -level*sigma_h
            event, event_ts = None, None
            previous = log_path[0]
            for value, point in zip(log_path[1:], future_points):
                high_log = math.log(float(point.get("high", point["price"]))/start) if "high" in point else value
                low_log = math.log(float(point.get("low", point["price"]))/start) if "low" in point else value

                if (high_log >= upper and low_log <= lower) or (previous <= lower and value >= upper) or (previous >= upper and value <= lower):
                    ambiguous = True
                    event, event_ts = "ambiguous_first_touch", float(point["ts"])
                    break
                if high_log >= upper or value >= upper:
                    event, event_ts = "upper_hit_first", float(point["ts"])
                    break
                if low_log <= lower or value <= lower:
                    event, event_ts = "lower_hit_first", float(point["ts"])
                    break
                previous = value

            first_cal_mins = round((event_ts - captured) / 60.0, 6) if event_ts else None
            first_trad_mins = round(_trading_seconds_between(row["instrument"], captured, event_ts) / 60.0, 6) if event_ts else None

            barriers[str(level)] = {
                "outcome": event or "no_touch",
                "first_touch_calendar_minutes": first_cal_mins,
                "first_touch_trading_minutes": first_trad_mins,
                "first_touch_primary_time_basis": "trading",
            }

        increments = np.diff(np.asarray(log_path, dtype=float))
        realized_vol = (float(np.std(increments, ddof=1) *
                              math.sqrt(len(increments))) if len(increments)>1 else 0.)
        terminal = math.log(end/start)

        cal_elapsed = round((target - captured) / 60.0, 4)
        trad_elapsed = round(_trading_seconds_between(row["instrument"], captured, target) / 60.0, 4)

        outcome = {
            "version": RESOLVER_VERSION,
            "path_source": "recorded_real_market_path",
            "path_granularity": "1m_ohlc_or_ticks",
            "first_touch_resolution": "ohlc_or_point",
            "resolved_from": "recorded_real_market_path",
            "path_point_count": len(points),
            "future_return": end/start-1.,
            "future_log_return": terminal,
            "normalized_return": (terminal/sigma_h if sigma_h else None),
            "normalization_denominator": "T0 reference volatility",
            "max_favorable_excursion": max(log_path),
            "max_adverse_excursion": min(log_path),
            "realized_volatility": realized_vol,
            "standardized_barriers": barriers,
            "ambiguous_first_touch": ambiguous,
            "first_touch_calendar_minutes": barriers["1.0"].get("first_touch_calendar_minutes"),
            "first_touch_trading_minutes": barriers["1.0"].get("first_touch_trading_minutes"),
            "first_touch_primary_time_basis": "trading",
            "actual_quantile_placement": terminal,
        }
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE passive_market_observations SET resolution_status='resolved',"
                "resolved_ts=?,outcome_json=?,calendar_elapsed=?,trading_elapsed=?,"
                "market_open_fraction=? WHERE observation_id=?",
                (now, _json(outcome), target-captured,
                 _trading_seconds_between(row["instrument"], captured, target),
                 (_trading_seconds_between(row["instrument"], captured, target)
                  / (target-captured) if target > captured else 0.0),
                 row["observation_id"]))
            self._last_successful_resolution_ts = now
        self._resolve_virtual_states(row, points, now)
        return "resolved"

    def _resolve_virtual_states(self, passive_row: dict,
                                points: list[dict], now: float) -> None:
        with self._lock:
            virtual_rows = [dict(row) for row in self._conn.execute(
                "SELECT * FROM virtual_position_observations "
                "WHERE passive_observation_id=? AND resolution_status='pending'",
                (passive_row["observation_id"],)).fetchall()]
        if not virtual_rows:
            return
        forecast = json.loads(passive_row["forecast_json"])
        sigma_h = _finite(forecast.get("sigma_h_return"))

        if sigma_h is None or sigma_h <= 0:
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE virtual_position_observations "
                    "SET resolution_status='insufficient_reference_volatility',"
                    "resolved_ts=? WHERE passive_observation_id=? "
                    "AND resolution_status='pending'",
                    (now,passive_row["observation_id"]))
            return
        start = float(passive_row["market_price"])
        market_returns = [math.log(float(point["price"])/start)/sigma_h
                          for point in points]
        for virtual in virtual_rows:
            sign = 1.0 if virtual["direction"] == "long" else -1.0
            r_path = [float(virtual["r0"])+sign*value
                      for value in market_returns]
            stop_r, take_r = float(virtual["stop_r"]), float(virtual["take_r"])
            terminal_r, event, event_ts = r_path[-1], "horizon", None
            ambiguous = False
            previous = r_path[0]
            for value,point in zip(r_path[1:],points[1:]):
                if ((previous <= stop_r and value >= take_r)
                        or (previous >= take_r and value <= stop_r)):
                    ambiguous, event, event_ts = (
                        True, "ambiguous_first_touch", float(point["ts"]))
                    break
                if value <= stop_r:
                    terminal_r, event, event_ts = (
                        stop_r, "stop_exit", float(point["ts"]))
                    break
                if value >= take_r:
                    terminal_r, event, event_ts = (
                        take_r, "take_exit", float(point["ts"]))
                    break
                previous = value
            if ambiguous:
                status, outcome = "ambiguous_first_touch", {
                    "version":"virtual-management-outcome-f3-v1",
                    "ambiguous_first_touch":True,
                    "resolved_from":"recorded_real_market_path"}
            else:
                policies = {}
                for policy,fraction in VIRTUAL_POLICY_FRACTIONS.items():
                    total = (fraction*float(virtual["r0"])
                             +(1.0-fraction)*terminal_r)
                    policies[policy] = {
                        "fraction_closed_at_t0":fraction,
                        "remaining_fraction":1.0-fraction,
                        "total_r":round(total,8)}
                best = max(value["total_r"] for value in policies.values())
                winners = [name for name,value in policies.items()
                           if abs(value["total_r"]-best)<=1e-12]
                for value in policies.values():
                    value["regret_r"] = round(best-value["total_r"],8)
                status, outcome = "resolved", {
                    "version":"virtual-management-outcome-f3-v1",
                    "resolved_from":"recorded_real_market_path",
                    "position_origin":
                        "synthetic_position_state_on_real_market_path",
                    "event":event, "event_ts":event_ts,
                    "first_touch_calendar_minutes":(
                        round((event_ts-float(virtual["captured_ts"]))/60,6)
                        if event_ts else None),
                    "first_touch_trading_minutes":(
                        round(_trading_seconds_between(virtual["instrument"], float(virtual["captured_ts"]), event_ts)/60,6)
                        if event_ts else None),
                    "first_touch_primary_time_basis": "trading",
                    "terminal_r_per_unit":round(terminal_r,8),
                    "policies":policies, "best_policies":winners,
                    "claims_real_user_improvement":False}
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE virtual_position_observations "
                    "SET resolution_status=?,resolved_ts=?,outcome_json=? "
                    "WHERE virtual_id=?",
                    (status,now,_json(outcome),virtual["virtual_id"]))

    def virtual_management_report(self) -> dict:
        with self._lock:
            rows = [dict(row) for row in self._conn.execute(
                "SELECT v.*,p.target_ts FROM virtual_position_observations v "
                "JOIN passive_market_observations p "
                "ON p.observation_id=v.passive_observation_id "
                "WHERE v.resolution_status='resolved' "
                "AND v.evidence_eligible=1 ORDER BY v.captured_ts").fetchall()]
        policy_regret = defaultdict(list)
        unique_passive = {}
        for row in rows:
            outcome = json.loads(row["outcome_json"])
            for name,value in (outcome.get("policies") or {}).items():
                policy_regret[name].append(float(value["regret_r"]))
            unique_passive[row["passive_observation_id"]] = {
                "instrument":row["instrument"],
                "horizon_minutes":row["horizon_minutes"],
                "captured_ts":row["captured_ts"],
                "target_ts":row["target_ts"],
            }
        effective = self._effective_n(list(unique_passive.values()))
        means = {name:round(sum(values)/len(values),8)
                 for name,values in policy_regret.items() if values}
        return {
            "version":"virtual-management-report-f3-v1",
            "dataset":"virtual_position",
            "position_origin":"synthetic_position_state_on_real_market_path",
            "raw_n":len(rows), "effective_n":effective,
            "policy_mean_regret_r":means,
            "evidence":self._evidence_status(effective,0.0),
            "authority":"research_only",
            "mixed_with_real_trades":False,
            "claims_real_user_improvement":False,
            "promotion_allowed":False,
        }

    def resolve_due(self, *, now: float | None = None, limit: int = 100) -> dict:
        now = float(now or time.time())
        with self._lock:
            rows = [dict(x) for x in self._conn.execute(
                "SELECT * FROM passive_market_observations "
                "WHERE resolution_status='pending' AND target_ts<=? "
                "ORDER BY target_ts LIMIT ?", (now, int(limit))).fetchall()]
        counts = defaultdict(int)
        for row in rows:
            status = self._resolve_one(row, now)
            counts[status] += 1
            if status not in {"pending", "resolved"}:
                with self._lock, self._conn:
                    self._conn.execute(
                        "UPDATE passive_market_observations "
                        "SET resolution_status=?,resolved_ts=? "
                        "WHERE observation_id=?", (status, now,
                                                   row["observation_id"]))
        return dict(counts)

    def observations(self, *, limit: int = 100, instrument: str | None = None) -> dict:
        where, args = "", []
        if instrument:
            where, args = "WHERE instrument=?", [instrument]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM passive_market_observations {where} "
                "ORDER BY captured_ts DESC LIMIT ?", (*args, max(1,min(1000,limit)))
            ).fetchall()
        items = []
        for source in rows:
            row = dict(source)
            row["features"] = json.loads(row.pop("features_json"))
            row["forecast"] = json.loads(row.pop("forecast_json"))
            row["outcome"] = json.loads(row.pop("outcome_json") or "null")
            items.append(row)
        return {"dataset": "passive_market", "items": items,
                "mixed_with_real_trades": False}

    @staticmethod
    def _effective_n(rows: list[dict]) -> int:
        groups = defaultdict(list)
        for row in rows:
            groups[(row["instrument"], int(row["horizon_minutes"]))].append(row)
        effective = 0
        for group in groups.values():
            end = -math.inf
            for row in sorted(group, key=lambda x: x["captured_ts"]):
                if float(row["captured_ts"]) >= end:
                    effective += 1
                    end = float(row["target_ts"])
        return effective

    @staticmethod
    def purged_embargo_split(rows: list[dict]) -> dict:
        ordered = sorted(rows, key=lambda row: (
            float(row["captured_ts"]), str(row.get("observation_id", ""))))
        if len(ordered) < 3:
            return {
                "method": "chronological_purged_embargo",
                "random_shuffle": False, "purge_applied": True,
                "embargo_applied": True, "embargo_seconds": 0.0,
                "train_ids": [], "validation_ids": [], "test_ids": [],
                "consumed_test_interval": False,
            }
        val_index = max(1, min(len(ordered)-2, int(len(ordered)*.60)))
        test_index = max(val_index+1, min(len(ordered)-1, int(len(ordered)*.80)))
        validation_start = float(ordered[val_index]["captured_ts"])
        nominal_test_start = float(ordered[test_index]["captured_ts"])
        embargo = max(float(row["target_ts"])-float(row["captured_ts"])
                      for row in ordered)
        train = [row for row in ordered
                 if float(row["target_ts"]) < validation_start]
        validation = [
            row for row in ordered
            if float(row["captured_ts"]) >= validation_start
            and float(row["target_ts"]) < nominal_test_start]
        test_start = nominal_test_start + embargo
        test = [row for row in ordered
                if float(row["captured_ts"]) >= test_start]
        ids = lambda values: [str(row.get("observation_id", ""))
                              for row in values]
        return {
            "method": "chronological_purged_embargo",
            "random_shuffle": False, "purge_applied": True,
            "embargo_applied": True, "embargo_seconds": embargo,
            "validation_start": validation_start,
            "nominal_test_start": nominal_test_start,
            "embargoed_test_start": test_start,
            "train_ids": ids(train), "validation_ids": ids(validation),
            "test_ids": ids(test), "raw_n": len(ordered),
            "purged_or_embargoed_n":
                len(ordered)-len(train)-len(validation)-len(test),
            "consumed_test_interval": False,
        }

    @staticmethod
    def _evidence_status(effective_n: int, span_days: float) -> str:
        if effective_n < 30:
            return "INSUFFICIENT"
        if effective_n < 100 or span_days < 7:
            return "EARLY"
        if effective_n < 300 or span_days < 30:
            return "PROVISIONAL"
        return "SUPPORTED"

    def _resolved_rows(self) -> list[dict]:
        """Returns pristine resolved rows matching current F3 contract."""
        with self._lock:
            rows = [dict(x) for x in self._conn.execute(
                "SELECT * FROM passive_market_observations "
                "WHERE resolution_status='resolved' AND evidence_eligible=1 "
                "AND retrospective_replay=0 AND feature_contract_version=? "
                "ORDER BY captured_ts", (PASSIVE_SCHEMA_VERSION,)).fetchall()]
        for row in rows:
            row["forecast"] = json.loads(row["forecast_json"])
            row["outcome"] = json.loads(row["outcome_json"])
        return rows

    @classmethod
    def _reliability_table(cls, probabilities: list[float],
                           outcomes: list[float], rows: list[dict]) -> list[dict]:
        table = []
        for lower_i in range(10):
            lower, upper = lower_i/10, (lower_i+1)/10
            indices = [i for i,p in enumerate(probabilities)
                       if lower <= p <= upper
                       and (lower_i == 9 or p < upper)]
            selected = [rows[i] for i in indices]
            table.append({
                "forecast_bin": f"{lower:.1f}-{upper:.1f}",
                "raw_n": len(indices),
                "effective_n": cls._effective_n(selected),
                "mean_forecast": (
                    round(sum(probabilities[i] for i in indices)/len(indices), 6)
                    if indices else None),
                "actual_rate": (
                    round(sum(outcomes[i] for i in indices)/len(indices), 6)
                    if indices else None),
            })
        return table

    def calibration_report(self) -> dict:
        rows = self._resolved_rows()
        eligible = []
        for row in rows:
            q = ((row["forecast"].get("standardized_barriers") or {})
                 .get("1.0") or {})
            outcome = ((row["outcome"].get("standardized_barriers") or {})
                       .get("1.0") or {}).get("outcome")
            if all(_finite(q.get(k)) is not None
                   for k in ("up","down","no_touch")) and outcome in {
                       "upper_hit_first","lower_hit_first","no_touch"}:
                eligible.append((row,q,outcome))

        eligible_rows = [row for row,_,_ in eligible]
        oos_manifest = self.purged_embargo_split(eligible_rows)
        train_ids, test_ids = (
            set(oos_manifest["train_ids"]), set(oos_manifest["test_ids"]))
        labels = {"up": "upper_hit_first", "down": "lower_hit_first",
                  "no_touch": "no_touch"}
        score = {}
        for key, label in labels.items():
            probs = [float(q[key]) for _,q,_ in eligible]
            actual = [float(outcome==label) for *_,outcome in eligible]
            identity = _binary_score(probs, actual)
            descriptive_rate = sum(actual)/len(actual) if actual else None
            descriptive_baseline = _binary_score(
                [descriptive_rate]*len(actual)
                if descriptive_rate is not None else [], actual)

            train_actual = [
                float(outcome==label) for row,_,outcome in eligible
                if row["observation_id"] in train_ids]
            test_triples = [
                (row,float(q[key]),float(outcome==label))
                for row,q,outcome in eligible
                if row["observation_id"] in test_ids]
            train_rate = (
                sum(train_actual)/len(train_actual) if train_actual else None)
            test_probs = [probability for _,probability,_ in test_triples]
            test_actual = [outcome for *_,outcome in test_triples]
            test_identity = _binary_score(test_probs,test_actual)
            test_baseline = _binary_score(
                [train_rate]*len(test_actual)
                if train_rate is not None else [], test_actual)
            score[key] = {
                "identity_q": identity,
                "descriptive_full_sample_base_rate": {
                    **descriptive_baseline, "base_rate": descriptive_rate,
                    "authority": "descriptive_only"},
                "pristine_oos": {
                    "identity_q": test_identity,
                    "train_frozen_historical_base_rate": {
                        **test_baseline, "base_rate": train_rate},
                    "brier_improvement_vs_train_frozen_base_rate": (
                        None if test_identity["brier"] is None
                        or test_baseline["brier"] is None
                        else round(test_baseline["brier"]-
                                   test_identity["brier"],8)),
                },
                "reliability": self._reliability_table(
                    probs,actual,eligible_rows),
            }

        quantile_scores = {}
        tau_by_name = {
            "q10":.10,"q25":.25,"q50":.50,"q75":.75,"q90":.90}
        for name,tau in tau_by_name.items():
            pairs = []
            for row in rows:
                q = _finite((row["forecast"].get(
                    "quantiles_log_return") or {}).get(name))
                y = _finite(row["outcome"].get("future_log_return"))
                if q is not None and y is not None:
                    pairs.append((q,y))
            quantiles = [q for q,_ in pairs]
            outcomes = [y for _,y in pairs]
            quantile_scores[name] = {
                "nominal_coverage": tau,
                "actual_coverage": (
                    sum(float(y<=q) for q,y in pairs)/len(pairs)
                    if pairs else None),
                **_pinball_score(quantiles,outcomes,tau),
            }

        effective = self._effective_n(eligible_rows)
        test_rows = [row for row in eligible_rows
                     if row["observation_id"] in test_ids]
        test_effective = self._effective_n(test_rows)
        span = ((max((r["captured_ts"] for r in rows),default=0)-
                 min((r["captured_ts"] for r in rows),default=0))/86400
                if rows else 0)

        calibrator_gate = (
            "READY_FOR_REGISTERED_RESEARCH_TRAINING"
            if len(train_ids)>=100 and len(oos_manifest["validation_ids"])>=30
            and test_effective>=30 else "INSUFFICIENT"
        )

        calibrator_readiness_gate = {
            "data_contract_ready": True,
            "q_contract_ready": True,
            "variance_clock_ready": True,
            "outcome_contract_ready": True,
            "pristine_dataset_ready": bool(len(eligible) >= 30),
            "calibrator_training_allowed": False,
        }

        return {
            "version": "passive-q-calibration-f3-v1",
            "dataset": "passive_market",
            "probability_semantics": {
                "input": "risk_neutral_Q", "output": "physical_P_shadow",
                "physical_probability_published": False},
            "raw_n": len(rows), "q_eligible_n": len(eligible),
            "effective_n": effective, "test_effective_n": test_effective,
            "time_span_days": round(span,4),
            "binary": score, "quantiles": quantile_scores,
            "full_distribution": {
                "crps": None,
                "status": "not_implemented_without_frozen_full_density"},
            "oos_validation": {
                **oos_manifest,
                "train_n": len(oos_manifest["train_ids"]),
                "validation_n": len(oos_manifest["validation_ids"]),
                "test_n": len(oos_manifest["test_ids"]),
                "test_effective_n": test_effective,
            },
            "baselines": {
                "zero_return": "terminal-return baseline contract",
                "historical_base_rate": "train_frozen_for_pristine_test",
                "random_walk_no_drift": True,
                "current_production_forecast":
                    "not_available_for_passive_geometry",
                "identity_q": True},
            "calibrators": {
                "identity": "active_baseline",
                "platt_logistic": calibrator_gate,
                "isotonic": calibrator_gate,
                "beta": calibrator_gate,
                "training_is_automatic": False,
                "registry_required_before_shadow_prediction": True,
            },
            "calibrator_readiness_gate": calibrator_readiness_gate,
            "evidence_status": self._evidence_status(test_effective,span),
            "authority": "shadow", "promotion_allowed": False,
            "sample_count_auto_promotion": False,
        }

    def status(self) -> dict:
        with self._lock:
            counts = {row["resolution_status"]: row["n"] for row in
                      self._conn.execute(
                          "SELECT resolution_status,COUNT(*) n "
                          "FROM passive_market_observations "
                          "GROUP BY resolution_status").fetchall()}
            total = sum(counts.values())

            legacy_raw_n = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE feature_contract_version!=?", (PASSIVE_SCHEMA_VERSION,)).fetchone()[0]

            current_raw_n = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE feature_contract_version=?", (PASSIVE_SCHEMA_VERSION,)).fetchone()[0]

            eligible = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE evidence_eligible=1 AND feature_contract_version=?",
                (PASSIVE_SCHEMA_VERSION,)).fetchone()[0]

            latest = {row["instrument"]: row["captured_ts"] for row in
                      self._conn.execute(
                          "SELECT instrument,MAX(captured_ts) captured_ts "
                          "FROM passive_market_observations GROUP BY instrument")}

            q_eligible_n = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE forecast_json LIKE '%\"probability_measure\":\"risk_neutral_Q\"%'").fetchone()[0]

            q_unavailable_n = total - q_eligible_n

            sigma_valid_n = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE forecast_json LIKE '%\"sigma_h_return\":%' AND forecast_json NOT LIKE '%\"sigma_h_return\":null%'").fetchone()[0]

            sigma_missing_n = total - sigma_valid_n

            ambiguous_n = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE outcome_json LIKE '%\"ambiguous_first_touch\":true%'").fetchone()[0]

            page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]

        rows = self._resolved_rows()
        effective_current = self._effective_n(rows)

        return {
            "version": PASSIVE_SCHEMA_VERSION,
            "variance_clock_version": VARIANCE_CLOCK_VERSION,
            "option_q_contract_version": OPTION_Q_CONTRACT_VERSION,
            "collector_status": "degraded" if self._last_step_error else "running",
            "last_step_ts": self._last_step_ts,
            "last_successful_capture_ts": self._last_successful_capture_ts,
            "last_successful_resolution_ts": self._last_successful_resolution_ts,
            "latest_error": self._last_step_error,
            "supported_instruments": list(INSTRUMENTS),
            "last_observation_per_instrument": latest,
            "pending_resolutions": counts.get("pending", 0),
            "resolved_observations": counts.get("resolved", 0),
            "resolution_counts": dict(counts),
            "raw_n": total,
            "legacy_contract_raw_n": legacy_raw_n,
            "current_contract_raw_n": current_raw_n,
            "current_contract_effective_n": effective_current,
            "evidence_eligible_n": eligible,
            "effective_n": effective_current,
            "q_eligible_n": q_eligible_n,
            "q_unavailable_n": q_unavailable_n,
            "sigma_valid_n": sigma_valid_n,
            "sigma_missing_n": sigma_missing_n,
            "ambiguous_first_touch_n": ambiguous_n,
            "insufficient_future_data_n": counts.get("insufficient_future_data", 0),
            "producer_contract_error_n": self._contract_error_counters["producer_contract_error_n"],
            "invalid_density_n": self._contract_error_counters["invalid_density_n"],
            "variance_clock_error_n": self._contract_error_counters["variance_clock_error_n"],
            "horizon_alignment_unavailable_n": self._contract_error_counters["horizon_alignment_unavailable_n"],
            "database_size_bytes": page_count * page_size,
            "budget": self.budget,
            "active_trade_required": False,
            "authority": "research_only",
            "promotion_allowed": False,
        }

    def edge_report(self, real_report: dict | None = None) -> dict:
        calibration = self.calibration_report()
        return {
            "version": "three-way-edge-report-f3-v1",
            "market_forecast_edge": {
                "dataset": "passive_market", "raw_n": calibration["raw_n"],
                "effective_n": calibration["effective_n"],
                "evidence": calibration["evidence_status"],
                "q_to_p": calibration["binary"]},
            "virtual_management_edge": self.virtual_management_report(),
            "real_management_edge": {
                "dataset": "real_user_trade",
                "observations": (real_report or {}).get("observations",0),
                "resolved_trades": (real_report or {}).get("resolved_trades",0),
                "evidence": "INSUFFICIENT", "authority": "production_evidence_only"},
            "datasets_mixed": False, "promotion_allowed": False,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
