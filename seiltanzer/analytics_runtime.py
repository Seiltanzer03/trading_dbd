"""Runtime analytics adapters for the advanced visual modules.

The first implementation of Macro/Wavelet/Cross-Asset shipped with synthetic
price curves and mock correlation links.  This module replaces those payload
methods at runtime with market-backed implementations while keeping the public
Engine/API contracts stable.
"""

from __future__ import annotations

import math
import threading
import time
from copy import deepcopy

from .engine import Engine, clean_nans

_HISTORY_TTL_SEC = 240.0
_HISTORY_CACHE: dict[str, dict] = {}
_CORR_HISTORY: list[dict] = []
_LOCK = threading.RLock()


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _market_history(engine: Engine) -> tuple[list[dict], dict]:
    """Return real 5m market bars for the active instrument.

    Yahoo history is used only as a return/history source.  When the terminal
    is anchored to a direct broker/OTC quote, the entire historical curve is
    scaled multiplicatively to the current direct price.  That preserves all
    historical returns and makes the last point agree with the terminal scale.
    No synthetic interpolation is performed.
    """
    market = engine.market
    code = market.instrument_code
    now = time.time()

    with _LOCK:
        cached = _HISTORY_CACHE.get(code)
        if cached and now - cached["loaded_at"] < _HISTORY_TTL_SEC:
            return deepcopy(cached["points"]), dict(cached["meta"])

    points: list[dict] = []
    meta = {
        "source": None, "status": "no_data", "mapping": "none",
        "interval": "5m", "time_basis": "trading_bars", "loaded_at": now,
    }

    if market.demo:
        raw = list(market.intraday or [])
        points = [
            {"ts": float(ts), "price": float(price)}
            for ts, price, _vol in raw
            if _finite(ts) and _finite(price) and float(price) > 0
        ]
        meta.update(source="demo intraday", status="demo", mapping="direct")
    else:
        try:
            import yfinance as yf

            hist = yf.Ticker(market.instrument.yahoo).history(
                period="5d", interval="5m", auto_adjust=False)
            if len(hist):
                closes = []
                for ts, row in hist.iterrows():
                    value = row.get("Close")
                    if _finite(value) and float(value) > 0:
                        closes.append((float(ts.timestamp()), float(value)))

                current = engine._current_instrument_price()
                ratio = 1.0
                mapping = "direct_history"
                if closes and _finite(current) and current and closes[-1][1] > 0:
                    ratio = float(current) / closes[-1][1]
                    if abs(ratio - 1.0) > 1e-6:
                        mapping = "return_shape_to_live_anchor"
                points = [{"ts": ts, "price": price * ratio} for ts, price in closes]
                meta.update(
                    source=f"yfinance {market.instrument.yahoo} 5d/5m",
                    status="derived" if mapping != "direct_history" else "delayed",
                    mapping=mapping,
                )
        except Exception as exc:  # noqa: BLE001
            meta["error"] = str(exc)[:180]

    if len(points) < 24:
        raw = list(market.intraday or [])
        fallback = [
            {"ts": float(ts), "price": float(price)}
            for ts, price, _vol in raw
            if _finite(ts) and _finite(price) and float(price) > 0
        ]
        if len(fallback) > len(points):
            points = fallback
            meta.update(
                source="terminal intraday observations",
                status=(market.price.get("status") or "delayed"),
                mapping="terminal_scale",
            )

    by_ts: dict[float, float] = {}
    for p in points:
        if _finite(p.get("ts")) and _finite(p.get("price")) and float(p["price"]) > 0:
            by_ts[float(p["ts"])] = float(p["price"])
    points = [{"ts": ts, "price": by_ts[ts]} for ts in sorted(by_ts)]

    current = engine._current_instrument_price()
    if _finite(current) and float(current) > 0:
        current_ts = float(market.price.get("ts") or now)
        if not points or current_ts > points[-1]["ts"] + 30:
            points.append({"ts": current_ts, "price": float(current)})

    if points:
        meta["points"] = len(points)
        meta["history_hours_trading"] = round(max(0, len(points) - 1) * 5 / 60, 1)
        meta["history_span_hours_clock"] = round(
            max(0.0, points[-1]["ts"] - points[0]["ts"]) / 3600.0, 1)
        meta["asof"] = points[-1]["ts"]

    with _LOCK:
        _HISTORY_CACHE[code] = {
            "loaded_at": now, "points": deepcopy(points), "meta": dict(meta),
        }
    return points, meta


def _remember_correlation(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        with _LOCK:
            return deepcopy(_CORR_HISTORY)
    asof = payload.get("asof")
    if not _finite(asof):
        asof = time.time()
    item = deepcopy(payload)
    item["asof"] = float(asof)
    with _LOCK:
        if not _CORR_HISTORY or abs(_CORR_HISTORY[-1].get("asof", 0) - item["asof"]) > 1:
            _CORR_HISTORY.append(item)
        cutoff = time.time() - 26 * 3600
        _CORR_HISTORY[:] = [p for p in _CORR_HISTORY if p.get("asof", 0) >= cutoff][-320:]
        return deepcopy(_CORR_HISTORY)


def _macro_regime_payload(self: Engine) -> dict:
    from .core.macro_regime import compute_macro_regime

    prices, meta = _market_history(self)
    corr_status = getattr(self.market, "correlation", {}) or {}
    corr = corr_status.get("value") if isinstance(corr_status, dict) else None
    corr_history = _remember_correlation(corr)
    prev = getattr(self, "_last_macro_regime", None)
    res = compute_macro_regime(
        prices,
        self.market.vols,
        corr,
        prev,
        instrument_code=self.market.instrument_code,
        source_meta=meta,
        correlation_history=corr_history,
    )
    if res.get("available") and res.get("summary"):
        self._last_macro_regime = res["summary"].get("regime")
        self._macro_regime_summary_cache = res.get("summary")
    return clean_nans(res)


def _wavelet_payload(self: Engine) -> dict:
    from .core.wavelet import compute_wavelet_analysis

    prices, meta = _market_history(self)
    res = compute_wavelet_analysis(prices, sampling_minutes=5.0, source_meta=meta)
    if res.get("available") and res.get("summary"):
        self._wavelet_summary_cache = res.get("summary")
    return clean_nans(res)


def _cross_asset_payload(self: Engine) -> dict:
    from .core.cross_asset import compute_correlation_graph

    status = getattr(self.market, "correlation", {}) or {}
    corr = status.get("value") if isinstance(status, dict) else None
    history = _remember_correlation(corr)
    res = compute_correlation_graph(
        corr,
        history=history,
        source_meta={
            "status": status.get("status") if isinstance(status, dict) else "no_data",
            "source": status.get("source") if isinstance(status, dict) else None,
            "ts": status.get("ts") if isinstance(status, dict) else None,
        },
    )
    if res.get("available") and res.get("summary"):
        self._cross_asset_summary_cache = res.get("summary")
    return clean_nans(res)


def install_analytics_runtime() -> None:
    """Install real-data implementations without changing public API routes."""
    Engine.macro_regime_payload = _macro_regime_payload
    Engine.wavelet_payload = _wavelet_payload
    Engine.cross_asset_payload = _cross_asset_payload
