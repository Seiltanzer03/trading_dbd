"""G.1S refinement: frozen T0 ATR, standardized first-touch barriers and ablation.

Existing prospective rows are never backfilled with ATR from future data.  New
background captures freeze ATR from daily bars already available at T0.  Barrier
outcomes are derived only from the recorded post-T0 path.  The refinement also
adds price+regime and full challenger feature sets without changing the base
production collector or authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
import types
from typing import Any

from .passive_learning import PassiveLearningEngine
from .g1_short_horizon_runtime import ShortHorizonRuntime, FEATURE_SETS, _finite, _loads, _json


REFINEMENT_VERSION = "g1s-atr-barrier-ablation-v1"
ATR_CONTRACT_VERSION = "g1s-frozen-daily-atr14-v1"
BARRIER_CONTRACT_VERSION = "g1s-atr-first-touch-v1"
ATR_MULTIPLES = (0.25, 0.50, 0.75, 1.0, 1.5, 2.0)

# Four explicit ablation families requested by the research contract.
FEATURE_SETS.update({
    "PRICE_REGIME_V1": (
        "sigma_h", "annual_vol", "price_quality", "tod_sin", "tod_cos",
        "regime_low_vol", "regime_high_vol", "regime_trend", "regime_range",
    ),
    "FULL_V1": (
        "sigma_h", "annual_vol", "price_quality", "tod_sin", "tod_cos",
        "regime_low_vol", "regime_high_vol", "regime_trend", "regime_range",
        "option_available", "option_quality", "option_skew", "option_width",
    ),
})


def _frozen_atr(feed, market_price: float) -> dict:
    daily = getattr(feed, "daily", None) or {}
    bars = daily.get("bars") if isinstance(daily, dict) else None
    if not isinstance(bars, dict):
        return {"available": False, "contract_version": ATR_CONTRACT_VERSION,
                "reason": "DAILY_BARS_UNAVAILABLE"}
    highs, lows, closes = bars.get("highs"), bars.get("lows"), bars.get("closes")
    if not all(isinstance(x, (list, tuple)) for x in (highs, lows, closes)):
        return {"available": False, "contract_version": ATR_CONTRACT_VERSION,
                "reason": "DAILY_BAR_SHAPE_INVALID"}
    n = min(len(highs), len(lows), len(closes))
    if n < 15:
        return {"available": False, "contract_version": ATR_CONTRACT_VERSION,
                "reason": "INSUFFICIENT_DAILY_BARS"}
    try:
        h=[float(x) for x in highs[-15:]]; l=[float(x) for x in lows[-15:]]
        c=[float(x) for x in closes[-15:]]
    except (TypeError, ValueError):
        return {"available": False, "contract_version": ATR_CONTRACT_VERSION,
                "reason": "NON_NUMERIC_DAILY_BARS"}
    if not all(math.isfinite(x) and x > 0 for x in h+l+c):
        return {"available": False, "contract_version": ATR_CONTRACT_VERSION,
                "reason": "INVALID_DAILY_BARS"}
    trs=[]
    for i in range(1, 15):
        trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    atr=sum(trs)/len(trs)
    if not math.isfinite(atr) or atr <= 0 or market_price <= 0:
        return {"available": False, "contract_version": ATR_CONTRACT_VERSION,
                "reason": "ATR_INVALID"}
    return {"available": True, "contract_version": ATR_CONTRACT_VERSION,
            "period_days": 14, "atr_price": atr,
            "atr_fraction": atr/float(market_price),
            "source": "daily_bars_available_before_t0"}


def _feature_vector_with_regime(row: dict, feature_set: str):
    # Replicate the base feature extraction so model artifacts keep one stable
    # dimension contract across training and future prediction.
    features = _loads(row.get("frozen_features_json"), {})
    forecast = _loads(row.get("frozen_forecast_json"), {})
    vol = features.get("volatility") or {}
    option = features.get("option_distribution") or {}
    captured=float(row.get("captured_ts") or 0)
    day_frac=(captured % 86400.0)/86400.0
    regime=str(row.get("market_regime") or features.get("market_regime") or "").upper()
    values = {
        "sigma_h": _finite(forecast.get("sigma_h_return")),
        "annual_vol": _finite(forecast.get("reference_volatility_annual"))
                      or _finite(vol.get("reference_volatility_annual")),
        "price_quality": _finite(row.get("price_quality")),
        "tod_sin": math.sin(2*math.pi*day_frac),
        "tod_cos": math.cos(2*math.pi*day_frac),
        "regime_low_vol": 1.0 if "LOW" in regime and "VOL" in regime else 0.0,
        "regime_high_vol": 1.0 if "HIGH" in regime and "VOL" in regime else 0.0,
        "regime_trend": 1.0 if "TREND" in regime else 0.0,
        "regime_range": 1.0 if any(x in regime for x in ("RANGE","MEAN_REVERT","SIDEWAYS")) else 0.0,
        "option_available": 1.0 if (features.get("options") or {}).get("available") else 0.0,
        "option_quality": _finite(row.get("option_quality")),
        "option_skew": _finite(forecast.get("skew")) or _finite(option.get("skew")),
        "option_width": _finite(forecast.get("option_implied_width"))
                        or _finite(option.get("implied_move_frac")),
    }
    vector=[0.0 if values.get(name) is None else float(values[name])
            for name in FEATURE_SETS[feature_set]]
    from .config import INSTRUMENTS
    instruments=tuple(INSTRUMENTS)
    vector.extend(1.0 if row["instrument"] == code else 0.0 for code in instruments[1:])
    return vector, values


def _ensure_barrier_table(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_barrier_outcomes(
                observation_id TEXT NOT NULL,
                barrier_atr_multiple REAL NOT NULL,
                atr_available INTEGER NOT NULL,
                atr_price REAL,
                atr_fraction REAL,
                upper_price REAL,
                lower_price REAL,
                first_touch TEXT NOT NULL,
                first_touch_ts REAL,
                terminal_atr_return REAL,
                mfe_atr REAL,
                mae_atr REAL,
                detail_json TEXT NOT NULL,
                created_ts REAL NOT NULL,
                PRIMARY KEY(observation_id,barrier_atr_multiple))""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_barrier_touch "
            "ON g1s_barrier_outcomes(barrier_atr_multiple,first_touch,observation_id)")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1s_barrier_outcomes_immutable_update
            BEFORE UPDATE ON g1s_barrier_outcomes
            BEGIN SELECT RAISE(ABORT,'immutable G1S barrier row'); END""")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1s_barrier_outcomes_immutable_delete
            BEFORE DELETE ON g1s_barrier_outcomes
            BEGIN SELECT RAISE(ABORT,'immutable G1S barrier row'); END""")


def _touch_from_path(runtime, row: dict, atr: float, multiple: float) -> tuple[str, float | None]:
    start=float(row["market_price"]); upper=start+multiple*atr; lower=start-multiple*atr
    captured=float(row["captured_ts"]); target=float(row["target_ts"]); instrument=str(row["instrument"])
    with runtime._lock:
        bars=runtime._conn.execute(
            "SELECT bar_start_ts,bar_end_ts,high,low FROM passive_market_bars "
            "WHERE instrument=? AND bar_start_ts>=? AND bar_end_ts<=? ORDER BY bar_start_ts",
            (instrument,captured-1e-6,target+1e-6)).fetchall()
        points=runtime._conn.execute(
            "SELECT ts,price FROM passive_market_path WHERE instrument=? AND ts>=? AND ts<=? ORDER BY ts",
            (instrument,captured-1e-6,target+1e-6)).fetchall()
    # Exact recorded path points have precedence when they observe a crossing.
    for point in points:
        p=float(point["price"])
        if p >= upper: return "UPPER_FIRST", float(point["ts"])
        if p <= lower: return "LOWER_FIRST", float(point["ts"])
    for bar in bars:
        up=float(bar["high"]) >= upper; down=float(bar["low"]) <= lower
        if up and down: return "AMBIGUOUS_SAME_BAR", float(bar["bar_end_ts"])
        if up: return "UPPER_FIRST", float(bar["bar_end_ts"])
        if down: return "LOWER_FIRST", float(bar["bar_end_ts"])
    return "NEITHER", None


def _materialize_barriers(runtime: ShortHorizonRuntime, limit: int = 5000) -> int:
    _ensure_barrier_table(runtime)
    with runtime._lock:
        rows=runtime._conn.execute("""
            SELECT g.*,r.terminal_log_return,r.mfe_log_return,r.mae_log_return
            FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
            WHERE NOT EXISTS(SELECT 1 FROM g1s_barrier_outcomes b
                             WHERE b.observation_id=g.observation_id)
            ORDER BY r.resolved_ts LIMIT ?""", (max(1,min(int(limit),10000)),)).fetchall()
    written=0
    for raw_row in rows:
        row=dict(raw_row); features=_loads(row["frozen_features_json"], {})
        atr_state=((features.get("price_state") or {}).get("atr") or {})
        atr=_finite(atr_state.get("atr_price")); atr_frac=_finite(atr_state.get("atr_fraction"))
        available=bool(atr_state.get("available") and atr is not None and atr > 0)
        for multiple in ATR_MULTIPLES:
            if available:
                touch,touch_ts=_touch_from_path(runtime,row,float(atr),multiple)
                upper=float(row["market_price"])+multiple*float(atr)
                lower=float(row["market_price"])-multiple*float(atr)
                terminal_atr=(math.expm1(float(row["terminal_log_return"]))*float(row["market_price"]))/float(atr)
                mfe_atr=(math.expm1(float(row["mfe_log_return"]))*float(row["market_price"]))/float(atr) if row["mfe_log_return"] is not None else None
                mae_atr=(math.expm1(float(row["mae_log_return"]))*float(row["market_price"]))/float(atr) if row["mae_log_return"] is not None else None
                reason=None
            else:
                touch,touch_ts="UNAVAILABLE",None; upper=lower=terminal_atr=mfe_atr=mae_atr=None
                reason="ATR_NOT_FROZEN_AT_T0"
            detail={"contract_version":BARRIER_CONTRACT_VERSION,
                    "atr_contract_version":atr_state.get("contract_version"),
                    "availability_reason":reason,"path_only_after_t0":True,
                    "hindsight_atr_backfill":False}
            with runtime._lock, runtime._conn:
                cur=runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_barrier_outcomes(observation_id,barrier_atr_multiple,"
                    "atr_available,atr_price,atr_fraction,upper_price,lower_price,first_touch,"
                    "first_touch_ts,terminal_atr_return,mfe_atr,mae_atr,detail_json,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row["observation_id"],multiple,int(available),atr,atr_frac,upper,lower,touch,
                     touch_ts,terminal_atr,mfe_atr,mae_atr,_json(detail),time.time()))
                written += int(cur.rowcount > 0)
    return written


def install_g1_short_horizon_refinement() -> None:
    if getattr(ShortHorizonRuntime, "_atr_refinement", None) == REFINEMENT_VERSION:
        return

    original_capture=PassiveLearningEngine.capture_observation
    def capture_with_frozen_atr(self, *, instrument: str, captured_ts: float,
                                market_price: float, features: dict, forecast: dict,
                                provenance: dict, trigger_reason: str="cadence",
                                evidence_eligible: bool=True, observation_origin: str|None=None):
        frozen=copy.deepcopy(features) if isinstance(features,dict) else {}
        feed=self._feeds.get(instrument)
        if feed is not None:
            frozen.setdefault("price_state", {})["atr"]=_frozen_atr(feed,float(market_price))
        else:
            frozen.setdefault("price_state", {})["atr"]={
                "available":False,"contract_version":ATR_CONTRACT_VERSION,
                "reason":"FEED_NOT_INITIALIZED"}
        return original_capture(self,instrument=instrument,captured_ts=captured_ts,
                                market_price=market_price,features=frozen,forecast=forecast,
                                provenance=provenance,trigger_reason=trigger_reason,
                                evidence_eligible=evidence_eligible,
                                observation_origin=observation_origin)
    PassiveLearningEngine.capture_observation=capture_with_frozen_atr

    original_ensure=ShortHorizonRuntime._ensure_tables
    def ensure_with_barriers(self):
        original_ensure(self); _ensure_barrier_table(self)
    ShortHorizonRuntime._ensure_tables=ensure_with_barriers
    ShortHorizonRuntime._feature_vector=staticmethod(_feature_vector_with_regime)

    original_step=ShortHorizonRuntime.step
    def step_with_barriers(self):
        result=original_step(self)
        try:
            result["barrier_rows_created"]=_materialize_barriers(self)
        except Exception as exc:
            self._last_error=f"barrier materializer: {type(exc).__name__}: {str(exc)[:250]}"
            result["barrier_rows_created"]=0
            result["barrier_error"]=self._last_error
        return result
    ShortHorizonRuntime.step=step_with_barriers

    def barriers(self, limit: int=500):
        with self._lock:
            rows=self._conn.execute("""
                SELECT b.*,g.instrument,g.horizon_minutes,g.captured_ts,g.origin
                FROM g1s_barrier_outcomes b JOIN g1s_observations g USING(observation_id)
                ORDER BY g.captured_ts DESC,b.barrier_atr_multiple LIMIT ?""",
                (max(1,min(int(limit),5000)),)).fetchall()
        return {"contract_version":BARRIER_CONTRACT_VERSION,
                "atr_contract_version":ATR_CONTRACT_VERSION,
                "multiples":list(ATR_MULTIPLES),"items":[dict(r) for r in rows],
                "legacy_missing_atr_is_not_backfilled":True,
                "production_authority":False}
    ShortHorizonRuntime.barriers=barriers
    ShortHorizonRuntime._atr_refinement=REFINEMENT_VERSION
