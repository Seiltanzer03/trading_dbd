"""Adapter from immutable G.1S T0 captures plus future outcomes to EDE rows.

This module is a reader of the existing collector.  It never creates captures,
reconstructs option history, or mutates G.1S rows.
"""
from __future__ import annotations

import json
import math
import time
import bisect
from collections import defaultdict
from typing import Any, Callable

from seiltanzer.g1_short_horizon_p2e_segmented_persistence import (
    ASSET_FAMILY_BY_INSTRUMENT,
    session_utc,
)
from seiltanzer.g1_short_horizon_historical_wf import _weights

from .feature_view import FeatureValue, causal_dynamics, feature_value
from .historical import aligned_cross_asset_context
from .maturity import data_maturity
from .registry import FEATURES, ZERO_COVERAGE_DIAGNOSIS


PROSPECTIVE_ADAPTER_VERSION = "g1s-ede-prospective-adapter-v1.2"
HORIZONS = (15, 30, 60, 120, 240)


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _path(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _block_meta(block: dict[str, Any], t0: float) -> tuple[float | None, float | None, float | None]:
    quality = block.get("quality") if isinstance(block, dict) else None
    quality = quality if isinstance(quality, dict) else {}
    asof = _first(
        _finite(quality.get("source_ts")), _finite(block.get("source_ts")),
        _finite(block.get("source_last_bar_end_ts")), _finite(block.get("captured_ts")),
    )
    score = _finite(quality.get("source_quality"))
    stale_after = None
    if bool(quality.get("stale")) and asof is not None:
        stale_after = max(0.0, t0-asof-1e-6)
    return asof, score, stale_after


def _dynamic_value(block: dict[str, Any], metric: str, field: str = "slope") -> Any:
    derivatives = block.get("derivatives") if isinstance(block, dict) else None
    derivative = (derivatives or {}).get(metric) if isinstance(derivatives, dict) else None
    if not isinstance(derivative, dict) or not derivative.get("available"):
        return None
    return derivative.get(field)


Extractor = Callable[[dict[str, Any]], tuple[Any, dict[str, Any]]]


def _v3_block(name: str, *value_path: str) -> Extractor:
    def extract(frozen: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        block = _path(frozen, "g1s_evidence_v3", name)
        block = block if isinstance(block, dict) else {}
        return _path(block, *value_path), block
    return extract


def _option_dynamic(metric: str, field: str = "slope") -> Extractor:
    def extract(frozen: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        block = _path(frozen, "g1s_evidence_v3", "option_dynamics")
        block = block if isinstance(block, dict) else {}
        return _dynamic_value(block, metric, field), block
    return extract


def _v2_option(name: str) -> Extractor:
    def extract(frozen: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        block = _path(frozen, "g1s_evidence_v2", "option_context")
        block = block if isinstance(block, dict) else {}
        return block.get(name), block
    return extract


def _raw_option_skew(frozen: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Recover the observed ``rr`` value already frozen at T0.

    This is a field mapping repair, not a reconstruction of an option chain.
    """
    raw = frozen.get("option_distribution") if isinstance(frozen, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    skew = raw.get("skew")
    skew = skew if isinstance(skew, dict) else {}
    value = _first(
        skew.get("rr"), skew.get("rr_25"), skew.get("risk_reversal"),
        skew.get("skew"), skew.get("value"))
    meta = _path(frozen, "g1s_evidence_v3", "option_static")
    if not isinstance(meta, dict):
        meta = _path(frozen, "g1s_evidence_v2", "option_context")
    return value, meta if isinstance(meta, dict) else {}


def _fallback(primary: Extractor, secondary: Extractor) -> Extractor:
    def extract(frozen: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        value, block = primary(frozen)
        return (value, block) if value is not None else secondary(frozen)
    return extract


EXTRACTORS: dict[str, Extractor] = {
    "price.ret_5m": _v3_block("price_volatility", "ret_5m"),
    "price.ret_15m": _v3_block("price_volatility", "ret_15m"),
    "price.ret_60m": _v3_block("price_volatility", "ret_60m"),
    "price.momentum": _v3_block("price_volatility", "return_dynamics", "slope"),
    "price.acceleration": _v3_block("price_volatility", "return_dynamics", "acceleration"),
    "price.trend_efficiency_60": _v3_block("price_volatility", "trend_efficiency_60"),
    "price.range_60": _v3_block("price_volatility", "range_60m"),
    "price.drawdown_60": _v3_block("price_volatility", "drawdown_60m"),
    "price.drawup_60": _v3_block("price_volatility", "drawup_60m"),
    "vol.rv_15m": _v3_block("price_volatility", "realized_vol_15m"),
    "vol.rv_60m": _v3_block("price_volatility", "realized_vol_60m"),
    "vol.expansion_state": _v3_block("price_volatility", "rv_dynamics", "slope"),
    "vol.vol_of_vol": _v3_block("price_volatility", "rv_dynamics", "noise"),
    "option.iv": _fallback(_v3_block("option_static", "iv"), _v2_option("implied_vol_annual")),
    "option.iv_rv_ratio": _fallback(
        _v3_block("option_static", "iv_rv_ratio"), _v2_option("iv_rv_ratio")),
    "option.skew": _fallback(
        _fallback(_v3_block("option_static", "skew"), _v2_option("skew")),
        _raw_option_skew),
    "option.term_slope": _fallback(
        _v3_block("option_static", "term_slope"), _v2_option("term_slope")),
    "option.gex_net_balance": _v2_option("gex_net_balance"),
    "option.zero_gamma_distance": _fallback(
        _v3_block("gex", "zero_gamma_log_moneyness"),
        _v2_option("gex_zero_flip_log_moneyness")),
    "option.delta": _v3_block("option_static", "delta"),
    "option.vanna": _v3_block("option_static", "vanna"),
    "option.charm": _v3_block("option_static", "charm_per_day"),
    "option_dynamics.iv_velocity": _option_dynamic("iv"),
    "option_dynamics.skew_velocity": _option_dynamic("skew"),
    "option_dynamics.gex_velocity": _option_dynamic("gex_force_score"),
    "option_dynamics.vanna_velocity": _option_dynamic("vanna"),
    "option_dynamics.charm_velocity": _option_dynamic("charm_per_day"),
    "option_dynamics.zero_gamma_velocity": _option_dynamic(
        "gex_zero_gamma_log_moneyness"),
    "cross.correlation": _v3_block("cross_asset", "systemic_coupling"),
    "cross.correlation_change": _v3_block("cross_asset", "network_tension"),
    "regime.macro": _v3_block("macro", "regime"),
    "regime.wavelet_phase": _v3_block("wavelet", "phase_stability"),
    "regime.trend": _v3_block("price_volatility", "trend_regime"),
    "regime.volatility": _v3_block("price_volatility", "volatility_regime"),
}


CAUSAL_OPTION_SERIES = {
    "iv": "option.iv", "skew": "option.skew",
    "gex": "option.gex_net_balance", "vanna": "option.vanna",
    "charm": "option.charm", "zero_gamma": "option.zero_gamma_distance",
}
DERIVED_IMPLEMENTED_IDS = {
    "vol.rv15_over_rv60", "cross.confirmation", "cross.family_breadth",
    "cross.market_breadth", "cross.correlation", "cross.correlation_change",
    "regime.asset", "regime.asset_family", "regime.session_utc",
    "price.trend_efficiency_60", "price.range_60", "price.drawdown_60",
    "price.drawup_60", "regime.trend", "regime.volatility",
    *{
        f"option_dynamics.{metric}_{transform}"
        for metric in CAUSAL_OPTION_SERIES
        for transform in (
            "acceleration", "rolling_rank", "rolling_zscore", "direction_consistency")
    },
}


class ProspectiveFeatureAdapter:
    def __init__(self, runtime: Any, *, available_asof: float | None = None):
        self.runtime = runtime
        self.available_asof = float(time.time() if available_asof is None else available_asof)
        with runtime._lock:
            self.tables = {str(row[0]) for row in runtime._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self._causal_bars = self._load_causal_bars()
        self._causal_bar_cache: dict[tuple[str, float, float], dict[str, Any]] = {}

    def _load_causal_bars(self) -> dict[str, list[dict[str, Any]]]:
        if "passive_market_bars" not in self.tables:
            return {}
        with self.runtime._lock:
            columns = {str(row[1]) for row in self.runtime._conn.execute(
                "PRAGMA table_info(passive_market_bars)").fetchall()}
            if not {"instrument", "bar_end_ts", "high", "low", "close"} <= columns:
                return {}
            created = "created_ts" if "created_ts" in columns else "bar_end_ts AS created_ts"
            rows = self.runtime._conn.execute(
                "SELECT instrument,bar_end_ts,high,low,close,quality," + created + " "
                "FROM passive_market_bars ORDER BY instrument,bar_end_ts").fetchall()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["instrument"])].append(dict(row))
        return dict(grouped)

    def _recomputed_price_context(self, row: dict[str, Any]) -> dict[str, Any]:
        """Recompute only from bars demonstrably admitted by the T0 capture."""
        instrument = str(row["instrument"])
        t0 = float(row["captured_ts"])
        capture_recorded_ts = _finite(row.get("created_ts")) or t0
        key = (instrument, t0, capture_recorded_ts)
        cached = self._causal_bar_cache.get(key)
        if cached is not None:
            return cached
        all_bars = self._causal_bars.get(instrument, [])
        eligible = [bar for bar in all_bars
                    if float(bar["bar_end_ts"]) <= t0+1e-6
                    and float(bar.get("created_ts") or bar["bar_end_ts"])
                    <= capture_recorded_ts+1e-6]
        if len(eligible) < 12:
            self._causal_bar_cache[key] = {}
            return {}
        ends = [float(bar["bar_end_ts"]) for bar in eligible]
        end_index = bisect.bisect_right(ends, t0+1e-6)-1
        if end_index < 1 or t0-ends[end_index] > 5*60.0:
            self._causal_bar_cache[key] = {}
            return {}
        end_ts = ends[end_index]
        target = end_ts-60*60.0
        start_index = bisect.bisect_right(ends, target+1e-6, hi=end_index)-1
        if start_index < 0 or target-ends[start_index] > 2*60.0:
            self._causal_bar_cache[key] = {}
            return {}
        window = eligible[start_index:end_index+1]
        closes = [float(bar["close"]) for bar in window]
        if len(closes) < 10 or any(value <= 0 for value in closes):
            self._causal_bar_cache[key] = {}
            return {}
        steps = [math.log(closes[index]/closes[index-1])
                 for index in range(1, len(closes))]
        ret60 = math.log(closes[-1]/closes[0])
        path_abs = sum(abs(value) for value in steps)
        efficiency = abs(ret60)/path_abs if path_abs > 1e-12 else 0.0
        high = max(float(bar["high"]) for bar in window)
        low = min(float(bar["low"]) for bar in window)
        rv60 = math.sqrt(sum(value*value for value in steps))
        cutoff15 = end_ts-15*60.0
        rv15_steps = [steps[index-1] for index in range(1, len(window))
                      if float(window[index]["bar_end_ts"]) > cutoff15]
        rv15 = math.sqrt(sum(value*value for value in rv15_steps)) if rv15_steps else None
        qualities = [_finite(bar.get("quality")) for bar in window]
        qualities = [value for value in qualities if value is not None]
        quality = sum(qualities)/len(qualities) if qualities else None
        volatility_regime = None
        if rv15 is not None and rv60 > 0:
            ratio = 2.0*rv15/rv60
            volatility_regime = (
                "EXPANDING" if ratio >= 1.15
                else "CONTRACTING" if ratio <= 0.85 else "NORMAL")
        block = {
            "price.trend_efficiency_60": max(0.0, min(1.0, efficiency)),
            "price.range_60": math.log(high/low) if high > 0 and low > 0 else None,
            "price.drawdown_60": math.log(closes[-1]/high) if high > 0 else None,
            "price.drawup_60": math.log(closes[-1]/low) if low > 0 else None,
            "regime.trend": (
                "TREND_UP" if ret60 > 0 and efficiency >= 0.35
                else "TREND_DOWN" if ret60 < 0 and efficiency >= 0.35
                else "CHOP"),
            "regime.volatility": volatility_regime,
            "_meta": {
                "quality": {"source_ts": end_ts, "source_quality": quality, "stale": False},
                "provenance": "CAUSAL_RECOMPUTED",
                "source": "passive_market_bars",
                "source_created_cutoff_ts": capture_recorded_ts,
                "source_window_start_ts": float(window[0]["bar_end_ts"]),
                "source_window_end_ts": end_ts,
                "future_points_used": False,
                "bar_end_ts_lte_t0": True,
                "bar_created_ts_lte_capture_record": True,
            },
        }
        self._causal_bar_cache[key] = block
        return block

    def _source_rows(self) -> list[dict[str, Any]]:
        if "g1s_observations" not in self.tables:
            return []
        has_resolutions = "g1s_resolutions" in self.tables
        resolution_columns = (
            "r.resolved_ts,r.terminal_log_return,r.direction_label,"
            "r.mfe_log_return,r.mae_log_return,r.path_quality_status"
            if has_resolutions else
            "NULL resolved_ts,NULL terminal_log_return,NULL direction_label,"
            "NULL mfe_log_return,NULL mae_log_return,NULL path_quality_status")
        join = "LEFT JOIN g1s_resolutions r USING(observation_id)" if has_resolutions else ""
        with self.runtime._lock:
            rows = self.runtime._conn.execute(
                f"SELECT g.*,{resolution_columns} FROM g1s_observations g {join} "
                "WHERE g.horizon_minutes IN (15,30,60,120,240) "
                "ORDER BY g.captured_ts,g.instrument,g.horizon_minutes").fetchall()
        return [dict(row) for row in rows]

    def _feature_values(self, row: dict[str, Any], *, strict: bool) -> tuple[
            dict[str, FeatureValue], list[str], dict[str, dict[str, Any]]]:
        t0 = float(row["captured_ts"])
        frozen = _loads(row.get("frozen_features_json"))
        values: dict[str, FeatureValue] = {}
        rejected: list[str] = []
        provenance_by_feature: dict[str, dict[str, Any]] = {}
        definitions = {item.feature_id: item for item in FEATURES}
        recomputed = self._recomputed_price_context(row)
        for feature_id, extractor in EXTRACTORS.items():
            value, block = extractor(frozen)
            provenance = "FROZEN_T0"
            if value is None and feature_id in recomputed:
                value = recomputed.get(feature_id)
                block = recomputed.get("_meta") or {}
                provenance = "CAUSAL_RECOMPUTED"
            asof, quality, stale_after = _block_meta(block, t0)
            definition = definitions[feature_id]
            try:
                record = feature_value(
                    instrument=str(row["instrument"]), t0=t0,
                    horizon=int(row["horizon_minutes"]), feature_id=feature_id,
                    value=value, asof=asof, quality=quality,
                    stale_after_seconds=stale_after,
                    historical_available=definition.historical_availability == "AVAILABLE",
                    live_available=True, training_eligible=definition.training_eligibility,
                    dependency_group=definition.dependency_family)
                values[feature_id] = record
                provenance_by_feature[feature_id] = {
                    "provenance": provenance,
                    "future_points_used": False,
                }
                if provenance == "CAUSAL_RECOMPUTED":
                    provenance_by_feature[feature_id].update(recomputed.get("_meta") or {})
            except ValueError as exc:
                rejected.append(feature_id)
                if strict:
                    raise ValueError(f"{feature_id} rejected: {exc}") from exc
        return values, rejected, provenance_by_feature

    def rows(self, *, resolved_only: bool = True, strict: bool = True) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for source in self._source_rows():
            t0, target = float(source["captured_ts"]), float(source["target_ts"])
            if target <= t0:
                raise ValueError("target_ts must be after T0")
            resolved_ts = _finite(source.get("resolved_ts"))
            outcome_available = bool(
                target <= self.available_asof+1e-6
                and resolved_ts is not None and resolved_ts >= target-1e-6)
            if resolved_only and not outcome_available:
                continue
            if resolved_ts is not None and resolved_ts < target-1e-6:
                raise ValueError("future outcome was recorded before target_ts")
            feature_values, rejected, feature_provenance = self._feature_values(
                source, strict=strict)
            ret5 = feature_values.get("price.ret_5m")
            ret15 = feature_values.get("price.ret_15m")
            if resolved_only and (ret5 is None or ret15 is None
                                  or not ret5.training_eligible or not ret15.training_eligible):
                continue
            ede_features = {
                feature_id: item.value for feature_id, item in feature_values.items()
                if item.training_eligible
            }
            rv15 = ede_features.get("vol.rv_15m")
            rv60 = ede_features.get("vol.rv_60m")
            trend_efficiency = ede_features.get("price.trend_efficiency_60")
            ede_features.update({
                "asset": str(source["instrument"]),
                "asset_family": ASSET_FAMILY_BY_INSTRUMENT.get(
                    str(source["instrument"]), "UNKNOWN"),
                "session_utc": session_utc(t0),
                "rv15_over_rv60": (
                    float(rv15)/float(rv60)
                    if rv15 is not None and rv60 is not None and float(rv60) > 0 else None),
                "trend_efficiency_60": trend_efficiency,
                "range_60": ede_features.get("price.range_60"),
                "cross_confirmation": "NEUTRAL",
                "family_breadth": None, "market_breadth": None,
            })
            output.append({
                "observation_id": str(source["observation_id"]),
                "instrument": str(source["instrument"]),
                "captured_ts": t0, "target_ts": target,
                "resolved_ts": resolved_ts if outcome_available else None,
                "horizon_minutes": int(source["horizon_minutes"]),
                "direction_label": source.get("direction_label") if outcome_available else None,
                "terminal_log_return": (
                    _finite(source.get("terminal_log_return")) if outcome_available else None),
                "mfe_log_return": _finite(source.get("mfe_log_return")) if outcome_available else None,
                "mae_log_return": _finite(source.get("mae_log_return")) if outcome_available else None,
                "outcome_available": outcome_available,
                "outcome_available_asof": self.available_asof,
                "features": {
                    "ret_5m": None if ret5 is None else ret5.value,
                    "ret_15m": None if ret15 is None else ret15.value,
                },
                "ede_features": ede_features,
                "feature_values": {
                    key: {**value.as_dict(), **feature_provenance.get(key, {})}
                    for key, value in feature_values.items()},
                "rejected_feature_ids": rejected,
                "prospective_adapter_version": PROSPECTIVE_ADAPTER_VERSION,
                "retrospective_options_reconstruction": False,
            })
        output.sort(key=lambda row: (
            float(row["captured_ts"]), str(row["instrument"]), int(row["horizon_minutes"])))
        self._add_causal_option_transforms(output)
        by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in output:
            by_horizon[int(row["horizon_minutes"])].append(row)
        for rows in by_horizon.values():
            aligned_cross_asset_context(rows)
            self._add_context_feature_values(rows)
        return output

    @staticmethod
    def _add_context_feature_values(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            t0 = float(row["captured_ts"])
            ede = row["ede_features"]
            mapping = {
                "vol.rv15_over_rv60": ede.get("rv15_over_rv60"),
                "cross.confirmation": (
                    ede.get("cross_confirmation") if ede.get("cross_peer_count", 0) else None),
                "cross.family_breadth": ede.get("family_breadth"),
                "cross.market_breadth": ede.get("market_breadth"),
                "cross.correlation": ede.get("cross_correlation"),
                "cross.correlation_change": ede.get("cross_correlation_change"),
                "regime.asset": ede.get("asset"),
                "regime.asset_family": ede.get("asset_family"),
                "regime.session_utc": ede.get("session_utc"),
            }
            for feature_id, value in mapping.items():
                if (feature_id == "cross.correlation"
                        and ede.get("cross_correlation") is None):
                    continue
                if (feature_id == "cross.correlation_change"
                        and ede.get("cross_correlation_change") is None):
                    continue
                cross = feature_id.startswith("cross.")
                cross_meta = ede.get("cross_join_metadata") or {}
                asof = (cross_meta.get("peer_asof") if cross else t0)
                if cross and feature_id in {"cross.family_breadth", "cross.market_breadth"}:
                    asof = ede.get("cross_asof")
                record = feature_value(
                    instrument=str(row["instrument"]), t0=t0,
                    horizon=int(row["horizon_minutes"]), feature_id=feature_id,
                    value=value, asof=asof if value is not None else None,
                    training_eligible=True, dependency_group=(
                        "cross_asset" if feature_id.startswith("cross.")
                        else "regime" if feature_id.startswith("regime.")
                        else "volatility"),
                )
                serialized = record.as_dict()
                if cross:
                    serialized.update({
                        "provenance": "CAUSAL_NEAREST_PEER_JOIN",
                        "peer_asof": cross_meta.get("peer_asof"),
                        "peer_age_sec": cross_meta.get("peer_age_sec"),
                        "peer_count": cross_meta.get("peer_count", 0),
                        "coverage": cross_meta.get("coverage", 0.0),
                        "stale": bool(cross_meta.get("stale", False)),
                        "future_points_used": False,
                    })
                row["feature_values"][feature_id] = serialized
                if record.training_eligible:
                    row["ede_features"][feature_id] = record.value

    @staticmethod
    def _add_causal_option_transforms(rows: list[dict[str, Any]]) -> None:
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row["instrument"]), int(row["horizon_minutes"]))].append(row)
        for group in grouped.values():
            group.sort(key=lambda row: float(row["captured_ts"]))
            for metric, source_feature in CAUSAL_OPTION_SERIES.items():
                points = [
                    (float(row["captured_ts"]), float(row["ede_features"][source_feature]))
                    for row in group if row["ede_features"].get(source_feature) is not None
                ]
                dynamics = {float(item["t0"]): item for item in causal_dynamics(points, window=20)}
                for index, row in enumerate(group):
                    state = dynamics.get(float(row["captured_ts"]))
                    if state is None:
                        continue
                    velocity_id = f"option_dynamics.{metric}_velocity"
                    if (state.get("rate") is not None
                            and row["ede_features"].get(velocity_id) is None):
                        row["ede_features"][velocity_id] = float(state["rate"])
                        velocity_record = feature_value(
                            instrument=str(row["instrument"]),
                            t0=float(row["captured_ts"]),
                            horizon=int(row["horizon_minutes"]),
                            feature_id=velocity_id, value=float(state["rate"]),
                            asof=float(row["captured_ts"]),
                            historical_available=False, live_available=True,
                            training_eligible=True,
                            dependency_group="option_distribution",
                        ).as_dict()
                        velocity_record.update({
                            "provenance": "CAUSAL_DERIVED_FROM_IMMUTABLE_T0",
                            "future_points_used": False,
                        })
                        row["feature_values"][velocity_id] = velocity_record
                    derived = {
                        "acceleration": state["acceleration"] if index >= 2 else None,
                        "rolling_rank": state["rolling_rank"] if index >= 19 else None,
                        "rolling_zscore": state["rolling_zscore"] if index >= 19 else None,
                        "direction_consistency": state["direction_consistency"] if index >= 2 else None,
                    }
                    for transform, value in derived.items():
                        feature_id = f"option_dynamics.{metric}_{transform}"
                        if value is not None:
                            row["ede_features"][feature_id] = value
                            record = feature_value(
                                instrument=str(row["instrument"]),
                                t0=float(row["captured_ts"]),
                                horizon=int(row["horizon_minutes"]),
                                feature_id=feature_id, value=float(value),
                                asof=float(row["captured_ts"]),
                                historical_available=False, live_available=True,
                                training_eligible=True,
                                dependency_group="option_distribution",
                            ).as_dict()
                            record.update({
                                "provenance": "CAUSAL_DERIVED_FROM_IMMUTABLE_T0",
                                "future_points_used": False,
                            })
                            row["feature_values"][feature_id] = record

    def feature_capture_audit(self) -> dict[str, Any]:
        rows = self.rows(resolved_only=False, strict=False)
        totals = len(rows)
        records: list[dict[str, Any]] = []
        maturity_rank = {
            "INSUFFICIENT_DATA": 0, "DATA_READY_EARLY": 1,
            "DATA_READY_RESEARCH": 2, "DATA_READY_PROVISIONAL": 3,
            "DATA_READY_ROBUST": 4,
        }
        for definition in FEATURES:
            values = [row["feature_values"].get(definition.feature_id) for row in rows]
            present = [value for value in values if value and value["availability"] == "AVAILABLE"]
            stale = [value for value in present if value["stale"]]
            eligible = [value for value in present if value["training_eligible"]]
            t0s = [float(row["captured_ts"]) for row, value in zip(rows, values)
                   if value and value["availability"] == "AVAILABLE"]
            implemented = (definition.feature_id in EXTRACTORS
                           or definition.feature_id in DERIVED_IMPLEMENTED_IDS)
            by_horizon: dict[str, dict[str, Any]] = {}
            for horizon in HORIZONS:
                horizon_rows = [row for row in rows
                                if int(row["horizon_minutes"]) == horizon]
                eligible_rows = [
                    row for row in horizon_rows
                    if (row["feature_values"].get(definition.feature_id) or {}).get(
                        "training_eligible")]
                resolved_rows = [row for row in eligible_rows if row["outcome_available"]]
                _unused_weights, effective = _weights(resolved_rows) if resolved_rows else ([], 0)
                temporal_blocks = len({
                    time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"])))
                    for row in resolved_rows})
                maturity = data_maturity(
                    raw_n=len(resolved_rows), effective_n=int(effective),
                    temporal_blocks=temporal_blocks)
                by_horizon[str(horizon)] = {
                    "raw": len(eligible_rows),
                    "effective": int(effective),
                    "resolved": len(resolved_rows),
                    "temporal_blocks": temporal_blocks,
                    "coverage_pct": 100.0*len(eligible_rows)/max(1, len(horizon_rows)),
                    "data_maturity": maturity,
                    "edge_maturity": "INSUFFICIENT_DATA",
                }
            best_maturity = max(
                (item["data_maturity"] for item in by_horizon.values()),
                key=lambda item: maturity_rank[item], default="INSUFFICIENT_DATA")
            if definition.research_scope == "G1M_ONLY":
                status = "G1M_ONLY"
            elif definition.research_scope == "QUALITY_ONLY":
                status = "QUALITY_ONLY"
            else:
                status = best_maturity
            recomputed_n = sum(
                bool(value and value.get("provenance") == "CAUSAL_RECOMPUTED")
                for value in values)
            records.append({
                "feature_id": definition.feature_id,
                "research_scope": definition.research_scope,
                "live_capture_implemented": bool(implemented),
                "real_observations": len(present),
                "first_t0": min(t0s) if t0s else None,
                "latest_t0": max(t0s) if t0s else None,
                "coverage_pct": 100.0*len(present)/max(1, totals),
                "available_pct": 100.0*len(present)/max(1, totals),
                "stale_pct": 100.0*len(stale)/max(1, len(present)),
                "training_eligible_observations": len(eligible),
                "causal_recomputed_observations": recomputed_n,
                "usable_for_ede": bool(
                    definition.research_scope == "G1S"
                    and best_maturity != "INSUFFICIENT_DATA"),
                "data_maturity": best_maturity,
                "edge_maturity": "INSUFFICIENT_DATA",
                "status": status,
                "by_horizon": by_horizon,
                "zero_coverage_diagnosis": ZERO_COVERAGE_DIAGNOSIS.get(
                    definition.feature_id),
            })
        zero_diagnosis = [{
            "feature": row["feature_id"],
            "current_observations": row["real_observations"],
            **(row["zero_coverage_diagnosis"] or {}),
            "actual_status_after_fix": row["status"],
            "actual_coverage_after_fix_pct": row["coverage_pct"],
        } for row in records if row["feature_id"] in ZERO_COVERAGE_DIAGNOSIS]
        return {
            "contract_version": PROSPECTIVE_ADAPTER_VERSION,
            "observation_count": totals,
            "resolved_outcome_count": sum(bool(row["outcome_available"]) for row in rows),
            "features": records,
            "summary": {
                "feature_definitions": len(FEATURES),
                "live_capture_implemented": sum(row["live_capture_implemented"] for row in records),
                "with_real_observations": sum(row["real_observations"] > 0 for row in records),
                "with_prospective_coverage": sum(row["training_eligible_observations"] > 0
                                                 for row in records),
                "unavailable": sum(row["real_observations"] == 0 for row in records),
                "g1s_insufficient_data": sum(
                    row["research_scope"] == "G1S"
                    and row["status"] == "INSUFFICIENT_DATA" for row in records),
                "g1m_only": sum(row["status"] == "G1M_ONLY" for row in records),
                "quality_only": sum(row["status"] == "QUALITY_ONLY" for row in records),
                "causal_recomputed_features": sum(
                    row["causal_recomputed_observations"] > 0 for row in records),
            },
            "zero_feature_diagnosis": zero_diagnosis,
            "retrospective_options_reconstruction": False,
            "adjacent_collectors": self._adjacent_collector_audit(),
            "production_authority": False, "auto_promotion": False,
        }

    def _adjacent_collector_audit(self) -> dict[str, Any]:
        management_count = 0
        management_first = None
        management_latest = None
        if "g1m_t0_feature_context_v2" in self.tables:
            with self.runtime._lock:
                row = self.runtime._conn.execute(
                    "SELECT COUNT(*),MIN(captured_ts),MAX(captured_ts) "
                    "FROM g1m_t0_feature_context_v2").fetchone()
            management_count = int(row[0] or 0)
            management_first = _finite(row[1])
            management_latest = _finite(row[2])
        return {
            "g1m_option_barrier_context": {
                "live_capture_implemented": "g1m_t0_feature_context_v2" in self.tables,
                "real_observations": management_count,
                "first_t0": management_first, "latest_t0": management_latest,
                "usable_for_current_ede": False,
                "reason": (
                    "trade-management cohort is not silently mixed with per-instrument "
                    "G1S fixed-horizon observations"),
            },
            "option_rnd_geometry": {
                "live_capture_implemented": False,
                "real_observations": 0, "first_t0": None, "latest_t0": None,
                "usable_for_current_ede": False,
                "reason": "no immutable per-instrument G1S T0 materialization found",
            },
        }
