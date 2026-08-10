"""Continuous prospective market observations, outcomes and Q calibration.

This is deliberately separate from real trades. Synthetic/demo data may exercise the
pipeline but is never eligible research evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import threading
import time
import uuid
from collections import defaultdict
from statistics import NormalDist
from typing import Any

import numpy as np

from .config import INSTRUMENTS, Settings
from .data.feeds import MarketData

HORIZONS_MINUTES = (15, 30, 60, 120, 240, 480, 1440)
GEOMETRY_SIGMAS = (.5, 1.0, 1.5, 2.0)
OBSERVATION_CADENCE_SEC = 15 * 60
MAX_GAP_SEC = 5 * 60
PASSIVE_SCHEMA_VERSION = "passive-observation-f2-v1"
FORECAST_VERSION = "passive-forecast-f2-v1"
RESOLVER_VERSION = "passive-resolver-f2-v1"


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


class PassiveLearningEngine:
    """Low-priority collector and immutable prospective research store."""

    def __init__(self, path: str, settings: Settings, cache):
        self.settings, self.cache = settings, cache
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._feeds: dict[str, MarketData] = {}
        self._instrument_cursor = 0
        self._last_step_error: str | None = None
        self._last_step_ts: float | None = None
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
    def _reference_volatility(feed: MarketData) -> float | None:
        bars = (feed.daily or {}).get("bars")
        try:
            closes = np.asarray(bars["Close"].dropna().tail(40), dtype=float)
            if closes.size >= 10:
                returns = np.diff(np.log(closes))
                value = float(np.std(returns, ddof=1) * math.sqrt(252))
                return value if math.isfinite(value) and value > 0 else None
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return None

    @staticmethod
    def _forecast(price: float, annual_vol: float | None,
                  horizon_minutes: int, option_metrics: dict | None) -> dict:
        sigma_h = (annual_vol * math.sqrt(horizon_minutes/(365*24*60))
                   if annual_vol is not None else None)
        q = {str(level): {"up": None, "down": None, "no_touch": None}
             for level in GEOMETRY_SIGMAS}
        # The identity-Q block is unavailable without actual option state. A
        # Gaussian volatility reference remains a named baseline, not Q.
        q_available = bool(option_metrics)
        if q_available:
            for level in GEOMETRY_SIGMAS:
                # Explicit first-touch approximation scaffold; research-only.
                touch = min(.98, 2*(1-NormalDist().cdf(level)))
                q[str(level)] = {"up": touch/2, "down": touch/2,
                                 "no_touch": 1-touch}
        quantiles = {}
        if sigma_h is not None:
            for name, z in (("q10",-1.2815515655),("q25",-.6744897502),
                            ("q50",0.),("q75",.6744897502),("q90",1.2815515655)):
                quantiles[name] = z*sigma_h
        else:
            quantiles = {name: None for name in ("q10","q25","q50","q75","q90")}
        return {
            "version": FORECAST_VERSION, "authority": "shadow_prediction",
            "probability_measure": "risk_neutral_Q" if q_available else "unavailable",
            "physical_probability_published": False,
            "reference_volatility_annual": annual_vol,
            "reference_volatility_units": "log_return_per_sqrt_year",
            "sigma_h_return": sigma_h, "standardized_barriers": q,
            "quantiles_log_return": quantiles,
            "option_implied_center": (
                option_metrics.get("mode_price") if option_metrics else None),
            "option_implied_width": (
                option_metrics.get("implied_move_frac") if option_metrics else None),
            "skew": (option_metrics.get("skew") if option_metrics else None),
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
                target = captured_ts + horizon*60.
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
                     "identity-only-unpromoted", "standardized-geometry-f2-v1",
                     _json(features), _json({**forecast,
                        "horizon_minutes": horizon,
                        "forecast_made_at": captured_ts}),
                     int(bool(evidence_eligible)), "pending", time.time()))
                ids.append(observation_id)
        return ids

    def _collect_instrument(self, instrument: str, now: float) -> list[str]:
        feed = self._feed(instrument)
        feed.refresh_price()
        state = feed.price or {}
        price = _finite(state.get("value"))
        ts = _finite(state.get("ts")) or now
        if price is None or price <= 0:
            return []
        kind = self._source_kind(state.get("source"), self.settings.demo)
        quality = self._quality(state)
        self.record_market_point(instrument, ts, price, source=state.get("source") or "",
                                 quality=quality, kind=kind)
        with self._lock:
            last = self._conn.execute(
                "SELECT MAX(captured_ts) FROM passive_market_observations "
                "WHERE instrument=?", (instrument,)).fetchone()[0]
        if last is not None and now-float(last) < OBSERVATION_CADENCE_SEC:
            return []
        # These calls occur in the isolated passive feed and low-priority thread.
        # They cannot change the live UI instrument.
        feed.refresh_daily()
        option_metrics = None
        if feed.instrument.options_proxy:
            try:
                feed.refresh_proxy_price()
                feed.refresh_chain()
                option_metrics = (feed.chain or {}).get("metrics")
            except Exception:
                option_metrics = None
        annual_vol = self._reference_volatility(feed)
        forecast = self._forecast(price, annual_vol, 15, option_metrics)
        provenance = {
            "price": {"source": state.get("source"), "quality": quality,
                      "age_sec": max(0., now-ts), "kind": kind},
            "options": {
                "source": (feed.chain or {}).get("source"),
                "quality": self._quality(feed.chain or {}) if option_metrics else 0.,
                "age_sec": (max(0., now-float((feed.chain or {}).get("ts")))
                            if (feed.chain or {}).get("ts") else None),
                "kind": ("proxy" if option_metrics else "unavailable")}}
        features = {
            "source_observation_ts": ts,
            "price_state": {"price": price, "available": True},
            "volatility": {"reference_annual": annual_vol,
                           "available": annual_vol is not None},
            "options": {"available": option_metrics is not None,
                        "stale": (feed.chain or {}).get("status") == "delayed"},
            "option_distribution": option_metrics if option_metrics else {
                "available": False},
            "option_derivatives": {"available": False},
            "barrier_geometry": {"family": "volatility_normalized",
                                 "levels_sigma": list(GEOMETRY_SIGMAS)},
            "gex_context": {"available": bool(
                option_metrics and option_metrics.get("gex"))},
            "cross_asset": {"available": False},
            "market_regime": "UNCLASSIFIED",
            "wavelet_context": {"available": False},
            "session": "continuous_observed_clock",
            "missing_is_not_zero": True,
            "trigger_contract_version": "passive-trigger-f2-v1",
        }
        return self.capture_observation(
            instrument=instrument, captured_ts=ts, market_price=price,
            features=features, forecast=forecast, provenance=provenance,
            trigger_reason="cadence", evidence_eligible=not self.settings.demo)

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
        if points[0]["ts"] > captured+MAX_GAP_SEC:
            return "insufficient_future_data"
        gaps = [float(b["ts"])-float(a["ts"]) for a,b in zip(points,points[1:])]
        if gaps and max(gaps) > MAX_GAP_SEC:
            return "insufficient_future_data"
        start, end = float(row["market_price"]), float(points[-1]["price"])
        prices = [start]+[float(p["price"]) for p in points if p["ts"] > captured]
        log_path = [math.log(p/start) for p in prices]
        features, forecast = json.loads(row["features_json"]), json.loads(row["forecast_json"])
        annual = _finite(forecast.get("reference_volatility_annual"))
        horizon_years = float(row["horizon_minutes"])/(365*24*60)
        sigma_h = annual*math.sqrt(horizon_years) if annual else None
        barriers = {}
        ambiguous = False
        for level in GEOMETRY_SIGMAS:
            if sigma_h is None:
                barriers[str(level)] = {"outcome": "unavailable",
                                        "first_touch_minutes": None}
                continue
            upper, lower = level*sigma_h, -level*sigma_h
            event, event_ts = None, None
            previous = log_path[0]
            previous_ts = captured
            for value, point in zip(log_path[1:], points):
                if (previous <= lower and value >= upper) or (
                        previous >= upper and value <= lower):
                    ambiguous = True
                    event, event_ts = "ambiguous_first_touch", float(point["ts"])
                    break
                if value >= upper:
                    event, event_ts = "upper_hit_first", float(point["ts"])
                    break
                if value <= lower:
                    event, event_ts = "lower_hit_first", float(point["ts"])
                    break
                previous, previous_ts = value, float(point["ts"])
            barriers[str(level)] = {
                "outcome": event or "no_touch",
                "first_touch_minutes": (
                    round((event_ts-captured)/60., 6) if event_ts else None)}
        increments = np.diff(np.asarray(log_path, dtype=float))
        realized_vol = (float(np.std(increments, ddof=1) *
                              math.sqrt(len(increments))) if len(increments)>1 else 0.)
        terminal = math.log(end/start)
        outcome = {
            "version": RESOLVER_VERSION, "resolved_from": "recorded_real_market_path",
            "path_point_count": len(points), "future_return": end/start-1.,
            "future_log_return": terminal,
            "normalized_return": (terminal/sigma_h if sigma_h else None),
            "normalization_denominator": "T0 reference volatility",
            "max_favorable_excursion": max(log_path),
            "max_adverse_excursion": min(log_path),
            "realized_volatility": realized_vol,
            "standardized_barriers": barriers,
            "ambiguous_first_touch": ambiguous,
            "actual_quantile_placement": terminal,
        }
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE passive_market_observations SET resolution_status='resolved',"
                "resolved_ts=?,outcome_json=?,calendar_elapsed=?,trading_elapsed=?,"
                "market_open_fraction=? WHERE observation_id=?",
                (now, _json(outcome), target-captured, target-captured, 1.0,
                 row["observation_id"]))
        return "resolved"

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
        # Conservative interval scheduling: within each instrument/horizon only
        # non-overlapping label windows count as independent.
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
    def _evidence_status(effective_n: int, span_days: float) -> str:
        if effective_n < 30:
            return "INSUFFICIENT"
        if effective_n < 100 or span_days < 7:
            return "EARLY"
        if effective_n < 300 or span_days < 30:
            return "PROVISIONAL"
        return "SUPPORTED"

    def _resolved_rows(self) -> list[dict]:
        with self._lock:
            rows = [dict(x) for x in self._conn.execute(
                "SELECT * FROM passive_market_observations "
                "WHERE resolution_status='resolved' AND evidence_eligible=1 "
                "AND retrospective_replay=0 ORDER BY captured_ts").fetchall()]
        for row in rows:
            row["forecast"] = json.loads(row["forecast_json"])
            row["outcome"] = json.loads(row["outcome_json"])
        return rows

    def calibration_report(self) -> dict:
        rows = self._resolved_rows()
        eligible = []
        for row in rows:
            q = ((row["forecast"].get("standardized_barriers") or {})
                 .get("1.0") or {})
            outcome = ((row["outcome"].get("standardized_barriers") or {})
                       .get("1.0") or {}).get("outcome")
            if all(_finite(q.get(k)) is not None for k in ("up","down","no_touch")) \
                    and outcome in {"upper_hit_first","lower_hit_first","no_touch"}:
                eligible.append((row,q,outcome))
        labels = {"up": "upper_hit_first", "down": "lower_hit_first",
                  "no_touch": "no_touch"}
        score = {}
        for key, label in labels.items():
            probs = [float(q[key]) for _,q,_ in eligible]
            actual = [float(outcome==label) for *_,outcome in eligible]
            identity = _binary_score(probs, actual)
            base_rate = sum(actual)/len(actual) if actual else None
            baseline = _binary_score(
                [base_rate]*len(actual) if base_rate is not None else [], actual)
            score[key] = {"identity_q": identity,
                          "historical_base_rate": baseline,
                          "base_rate": base_rate,
                          "brier_improvement_vs_base_rate": (
                              None if identity["brier"] is None or baseline["brier"] is None
                              else round(baseline["brier"]-identity["brier"],8))}
        coverage = {}
        for name in ("q10","q25","q50","q75","q90"):
            values = []
            for row in rows:
                q = _finite((row["forecast"].get("quantiles_log_return") or {}).get(name))
                y = _finite(row["outcome"].get("future_log_return"))
                if q is not None and y is not None:
                    values.append(float(y <= q))
            coverage[name] = sum(values)/len(values) if values else None
        effective = self._effective_n([row for row,_,_ in eligible])
        span = ((max((r["captured_ts"] for r in rows),default=0)-
                 min((r["captured_ts"] for r in rows),default=0))/86400 if rows else 0)
        return {
            "version": "passive-q-calibration-f2-v1",
            "dataset": "passive_market", "probability_semantics": {
                "input": "risk_neutral_Q", "output": "physical_P_shadow",
                "physical_probability_published": False},
            "raw_n": len(rows), "q_eligible_n": len(eligible),
            "effective_n": effective, "time_span_days": round(span,4),
            "binary": score, "quantile_coverage": coverage,
            "full_distribution": {"crps": None,
                                  "status": "not_implemented_until_density_contract"},
            "baselines": {
                "zero_return": "reported for terminal-return objectives",
                "historical_base_rate": True, "random_walk_no_drift": True,
                "current_production_forecast": "not_available_for_passive_geometry",
                "identity_q": True},
            "calibrators": {
                "identity": "active_baseline", "platt_logistic": "research_scaffold",
                "isotonic": "research_scaffold", "beta": "research_scaffold"},
            "evidence_status": self._evidence_status(effective,span),
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
            eligible = self._conn.execute(
                "SELECT COUNT(*) FROM passive_market_observations "
                "WHERE evidence_eligible=1").fetchone()[0]
            latest = {row["instrument"]: row["captured_ts"] for row in
                      self._conn.execute(
                          "SELECT instrument,MAX(captured_ts) captured_ts "
                          "FROM passive_market_observations GROUP BY instrument")}
            page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        rows = self._resolved_rows()
        return {
            "version": PASSIVE_SCHEMA_VERSION,
            "collector_status": "degraded" if self._last_step_error else "running",
            "last_step_ts": self._last_step_ts, "latest_error": self._last_step_error,
            "supported_instruments": list(INSTRUMENTS),
            "last_observation_per_instrument": latest,
            "pending_resolutions": counts.get("pending",0),
            "resolved_observations": counts.get("resolved",0),
            "resolution_counts": counts, "raw_n": total,
            "evidence_eligible_n": eligible, "effective_n": self._effective_n(rows),
            "database_size_bytes": page_count*page_size, "budget": self.budget,
            "active_trade_required": False, "authority": "research_only",
            "promotion_allowed": False,
        }

    def edge_report(self, real_report: dict | None = None) -> dict:
        calibration = self.calibration_report()
        return {
            "version": "three-way-edge-report-f2-v1",
            "market_forecast_edge": {
                "dataset": "passive_market", "raw_n": calibration["raw_n"],
                "effective_n": calibration["effective_n"],
                "evidence": calibration["evidence_status"],
                "q_to_p": calibration["binary"]},
            "virtual_management_edge": {
                "dataset": "virtual_position", "raw_n": 0, "effective_n": 0,
                "evidence": "INSUFFICIENT", "authority": "research_only"},
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
