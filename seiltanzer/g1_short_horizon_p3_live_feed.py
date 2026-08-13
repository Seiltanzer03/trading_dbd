"""Research-only raw intraday feed channel for P3 live volatility parity.

Existing ``MarketData.refresh_intraday`` shifts Yahoo history by a current basis
when the terminal displays a broker CFD / OTC spot series.  That behavior is
correct for UI/VWAP/path geometry, but an additive price shift changes log returns
slightly and therefore is not exact parity with the historical Yahoo 5m series
used by P3.

This installer preserves the existing public adjusted outputs and, from the very
same yfinance request, additionally exposes ``intraday_ohlcv_raw``. No second
network request is made. Nothing in production pricing or trading reads the new
attribute.
"""
from __future__ import annotations

import math
import time

from .data.feeds import MarketData


P3_LIVE_RAW_FEED_VERSION = "g1s-p3-live-raw-yahoo-1m-v1"


def _refresh_intraday_with_raw(self: MarketData) -> None:
    if self.demo:
        self.intraday_ohlcv_raw = []
        self.intraday_raw_source = "demo_unavailable"
        self.intraday_raw_fetched_ts = None
        return
    try:
        import yfinance as yf

        hist = yf.Ticker(self.instrument.yahoo).history(period="1d", interval="1m")
        if not len(hist):
            return
        fetched_ts = time.time()
        raw = []
        for ts, row in hist.iterrows():
            values = (
                float(row["Open"]), float(row["High"]), float(row["Low"]),
                float(row["Close"]), float(row["Volume"]),
            )
            if not all(math.isfinite(value) for value in values):
                continue
            if min(values[:4]) <= 0:
                continue
            raw.append((float(ts.timestamp()), *values))
        if not raw:
            return

        offset = 0.0
        if self.instrument.swissquote_pair or self.instrument.tradingview_symbol:
            if self.price.get("value") is None:
                return
            if self._has_direct_price_scale():
                offset = float(self.price["value"]) - float(raw[-1][4])

        # Existing public semantics are kept exactly: adjusted OHLC is in the
        # terminal's current quote scale; volume/timestamps remain Yahoo's.
        self.intraday_is_offset = (offset != 0.0)
        self.intraday_ohlcv = [
            (ts, open_p + offset, high + offset, low + offset, close + offset, volume)
            for ts, open_p, high, low, close, volume in raw
        ]
        self.intraday = [(ts, close + offset, volume)
                         for ts, _open, _high, _low, close, volume in raw]

        # New research-only channel: immutable-source values before quote-basis
        # transformation. P3L consumes this and nothing else consumes it.
        self.intraday_ohlcv_raw = list(raw)
        self.intraday_raw_source = f"yfinance {self.instrument.yahoo} 1m raw"
        self.intraday_raw_fetched_ts = fetched_ts
        self.intraday_offset_value = float(offset)
    except Exception:
        # Preserve the legacy contract: intraday refresh failure must never make
        # market/terminal collection fail. Previous successful state remains.
        return


def install_g1_short_horizon_p3_live_feed() -> None:
    if getattr(MarketData, "_p3_live_raw_feed_version", None) == P3_LIVE_RAW_FEED_VERSION:
        return
    MarketData.refresh_intraday = _refresh_intraday_with_raw
    MarketData._p3_live_raw_feed_version = P3_LIVE_RAW_FEED_VERSION
