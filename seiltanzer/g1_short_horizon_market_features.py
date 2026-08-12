"""Freeze price/momentum features that match intraday trading horizons.

The feed has already refreshed 1m OHLCV before PassiveLearningEngine calls
`capture_observation`.  This refinement uses only bars whose *end* is <= the
frozen source observation timestamp; later bars are ignored.  Old observations are
never hindsight-enriched and carry `intraday_feature_available=0` in model input.
"""
from __future__ import annotations

import copy
import math
from statistics import pstdev

from .passive_learning import PassiveLearningEngine
from .g1_short_horizon_runtime import ShortHorizonRuntime, FEATURE_SETS, _finite, _loads


CONTRACT_VERSION = "g1s-frozen-intraday-price-state-v1"
REFINEMENT_VERSION = "g1s-intraday-market-features-v1"

_PRICE = (
    "sigma_h", "annual_vol", "price_quality", "intraday_feature_available",
    "ret_15m", "ret_30m", "ret_60m", "realized_vol_15m", "realized_vol_60m",
    "intraday_range_position",
)
_REGIME = ("tod_sin", "tod_cos", "regime_low_vol", "regime_high_vol",
           "regime_trend", "regime_range")
_OPTION = ("option_available", "option_quality", "option_skew", "option_width")


def _intraday_state(feed, source_ts: float, market_price: float) -> dict:
    raw = getattr(feed, "intraday_ohlcv", None) or []
    rows = []
    for item in raw:
        try:
            bar_ts, _open, high, low, close, _vol = item
            bar_ts=float(bar_ts); high=float(high); low=float(low); close=float(close)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in (high,low,close)):
            continue
        if bar_ts + 60.0 <= float(source_ts) + 1e-6:
            rows.append((bar_ts, high, low, close))
    rows.sort(key=lambda x:x[0])
    if not rows or not math.isfinite(float(market_price)) or market_price <= 0:
        return {"contract_version":CONTRACT_VERSION,"available":False,
                "reason":"NO_PRE_T0_1M_BARS","future_bars_used":False}

    def previous_close(minutes: int):
        cutoff=float(source_ts)-minutes*60.0
        candidates=[r[3] for r in rows if r[0]+60.0 <= cutoff+1e-6]
        return candidates[-1] if candidates else None

    def logret(minutes: int):
        prev=previous_close(minutes)
        return math.log(float(market_price)/prev) if prev and prev>0 else None

    def realized(minutes: int):
        cutoff=float(source_ts)-minutes*60.0
        closes=[r[3] for r in rows if r[0]+60.0 >= cutoff-1e-6]
        if len(closes)<3:
            return None
        rets=[math.log(closes[i]/closes[i-1]) for i in range(1,len(closes))
              if closes[i]>0 and closes[i-1]>0]
        return pstdev(rets) if len(rets)>=2 else None

    cutoff60=float(source_ts)-60*60.0
    recent=[r for r in rows if r[0]+60.0 >= cutoff60-1e-6]
    hi=max((r[1] for r in recent),default=None); lo=min((r[2] for r in recent),default=None)
    range_position=None
    if hi is not None and lo is not None and hi>lo:
        range_position=max(0.0,min(1.0,(float(market_price)-lo)/(hi-lo)))

    return {
        "contract_version":CONTRACT_VERSION,"available":True,
        "source_observation_ts":float(source_ts),"future_bars_used":False,
        "latest_admissible_bar_end_ts":rows[-1][0]+60.0,
        "ret_15m":logret(15),"ret_30m":logret(30),"ret_60m":logret(60),
        "realized_vol_15m":realized(15),"realized_vol_60m":realized(60),
        "intraday_range_position":range_position,
    }


def _feature_vector(row: dict, feature_set: str):
    features=_loads(row.get("frozen_features_json"),{})
    forecast=_loads(row.get("frozen_forecast_json"),{})
    vol=features.get("volatility") or {}; option=features.get("option_distribution") or {}
    state=((features.get("price_state") or {}).get("g1s_intraday") or {})
    captured=float(row.get("captured_ts") or 0); day_frac=(captured%86400.0)/86400.0
    regime=str(row.get("market_regime") or features.get("market_regime") or "").upper()
    values={
        "sigma_h":_finite(forecast.get("sigma_h_return")),
        "annual_vol":_finite(forecast.get("reference_volatility_annual")) or _finite(vol.get("reference_volatility_annual")),
        "price_quality":_finite(row.get("price_quality")),
        "intraday_feature_available":1.0 if state.get("available") else 0.0,
        "ret_15m":_finite(state.get("ret_15m")),"ret_30m":_finite(state.get("ret_30m")),
        "ret_60m":_finite(state.get("ret_60m")),
        "realized_vol_15m":_finite(state.get("realized_vol_15m")),
        "realized_vol_60m":_finite(state.get("realized_vol_60m")),
        "intraday_range_position":_finite(state.get("intraday_range_position")),
        "tod_sin":math.sin(2*math.pi*day_frac),"tod_cos":math.cos(2*math.pi*day_frac),
        "regime_low_vol":1.0 if "LOW" in regime and "VOL" in regime else 0.0,
        "regime_high_vol":1.0 if "HIGH" in regime and "VOL" in regime else 0.0,
        "regime_trend":1.0 if "TREND" in regime else 0.0,
        "regime_range":1.0 if any(x in regime for x in ("RANGE","MEAN_REVERT","SIDEWAYS")) else 0.0,
        "option_available":1.0 if (features.get("options") or {}).get("available") else 0.0,
        "option_quality":_finite(row.get("option_quality")),
        "option_skew":_finite(forecast.get("skew")) or _finite(option.get("skew")),
        "option_width":_finite(forecast.get("option_implied_width")) or _finite(option.get("implied_move_frac")),
    }
    vector=[0.0 if values.get(name) is None else float(values[name]) for name in FEATURE_SETS[feature_set]]
    from .config import INSTRUMENTS
    instruments=tuple(INSTRUMENTS)
    vector.extend(1.0 if row["instrument"]==code else 0.0 for code in instruments[1:])
    return vector,values


def install_g1_short_horizon_market_features():
    if getattr(ShortHorizonRuntime,"_market_feature_refinement",None)==REFINEMENT_VERSION:
        return
    FEATURE_SETS["PRICE_ONLY_V1"]=_PRICE
    FEATURE_SETS["PRICE_REGIME_V1"]=_PRICE+_REGIME
    FEATURE_SETS["PRICE_OPTIONS_V1"]=_PRICE+_OPTION
    FEATURE_SETS["FULL_V1"]=_PRICE+_REGIME+_OPTION

    original_capture=PassiveLearningEngine.capture_observation
    def capture(self, *, instrument: str, captured_ts: float, market_price: float,
                features: dict, forecast: dict, provenance: dict,
                trigger_reason: str="cadence", evidence_eligible: bool=True,
                observation_origin: str|None=None):
        frozen=copy.deepcopy(features) if isinstance(features,dict) else {}
        source_ts=_finite(frozen.get("source_observation_ts")) or float(captured_ts)
        feed=self._feeds.get(instrument)
        state=_intraday_state(feed,source_ts,float(market_price)) if feed is not None else {
            "contract_version":CONTRACT_VERSION,"available":False,
            "reason":"FEED_NOT_INITIALIZED","future_bars_used":False}
        frozen.setdefault("price_state",{})["g1s_intraday"]=state
        return original_capture(self,instrument=instrument,captured_ts=captured_ts,
                                market_price=market_price,features=frozen,forecast=forecast,
                                provenance=provenance,trigger_reason=trigger_reason,
                                evidence_eligible=evidence_eligible,
                                observation_origin=observation_origin)
    PassiveLearningEngine.capture_observation=capture
    ShortHorizonRuntime._feature_vector=staticmethod(_feature_vector)
    ShortHorizonRuntime._market_feature_refinement=REFINEMENT_VERSION
