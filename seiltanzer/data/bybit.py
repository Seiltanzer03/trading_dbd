"""Public Bybit supplements. No orders, credentials, or replacement option evidence."""
from __future__ import annotations

import math
import statistics
import time

import httpx

# Exact product candidates, verified against instruments-info before use.
# QQQ is deliberately a mapped proxy, never a NAS100 price in its native scale.
PRODUCTS = {
    "NAS100": ("QQQUSDT", "QQQ", True),
    "SP500": ("SPXUSDT", "SPY", False),
    "XAU": ("XAUUSDT", "GLD", False),
    "XAG": ("XAGUSDT", "SLV", False),
    "EURUSD": ("EURUSDUSDT", "EURUSD", False),
    "BTCUSD": ("BTCUSDT", "BTC", False),
    "ETHUSD": ("ETHUSDT", "ETH", False),
    "SOLUSD": ("SOLUSDT", "SOL", False),
}


def number(value, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and (result > 0 if positive else result >= 0) else None


def missing(reason):
    return {"status": "no_data", "value": None, "error": str(reason)[:180],
            "source": "Bybit public V5", "production_authority": False}


def fresh_quote(quote, now, max_age=120):
    stamp = number(quote.get("ts"), positive=True)
    return (number(quote.get("value"), positive=True) is not None
            and quote.get("status") in {"live", "delayed"}
            and stamp is not None and -5 <= now - stamp <= max_age
            and quote.get("fresh") is not False)


class BybitPublic:
    def __init__(self):
        self._cache = {}

    def get(self, path, params=None, ttl=0):
        key = (path, tuple(sorted((params or {}).items())))
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
        with httpx.Client(timeout=3.0, trust_env=False) as client:
            response = client.get("https://api.bybit.com/v5/market/" + path, params=params)
            response.raise_for_status()
            data = response.json()
        if data.get("retCode") != 0:
            raise ValueError("Bybit market request rejected: " + str(data.get("retCode")))
        if ttl:
            self._cache[key] = (time.time(), data)
        return data

    def quote(self, instrument):
        symbol, _, mapped = PRODUCTS[instrument]
        info = self.get("instruments-info", {"category": "linear", "symbol": symbol}, 3600)
        specs = info.get("result", {}).get("list", [])
        if not any(x.get("symbol") == symbol and x.get("status") == "Trading"
                   and x.get("settleCoin") == "USDT" and not x.get("isPreListing")
                   and x.get("contractType") == "LinearPerpetual" for x in specs):
            raise ValueError("Bybit perpetual not listed/trading: " + symbol)
        body = self.get("tickers", {"category": "linear", "symbol": symbol})
        rows = body.get("result", {}).get("list", [])
        row = next((x for x in rows if x.get("symbol") == symbol), {})
        bid, ask = number(row.get("bid1Price"), positive=True), number(row.get("ask1Price"), positive=True)
        ts = number(body.get("time"), positive=True)
        if (bid is None or ask is None or ask < bid or ask / bid > 1.01
                or not number(row.get("bid1Size"), positive=True)
                or not number(row.get("ask1Size"), positive=True)
                or ts is None or not -5 <= time.time() - ts / 1000 <= 30):
            raise ValueError("Bybit quote missing, stale or spread >1%")
        return {"value": (bid + ask) / 2, "bid": bid, "ask": ask, "ts": ts / 1000,
                "status": "delayed", "source": f"Bybit {symbol} perpetual bid/ask · indicative",
                "symbol": symbol, "mapped_proxy": mapped, "derived": True,
                "instrument_type": "reference_proxy", "production_authority": False,
                "timestamp_kind": "server_snapshot_not_underlying_tick",
                "index_price": number(row.get("indexPrice"), positive=True),
                "mark_price": number(row.get("markPrice"), positive=True)}

    def options(self, instrument):
        _, base, _ = PRODUCTS[instrument]
        catalog = self.get("option-base-coins", ttl=3600)
        entries = catalog.get("result", {}).get("list", [])
        # Only documented 1x TradFi options. Crypto contract multipliers differ.
        if not any(x.get("baseCoin") == base and x.get("hasSymbol") == 1
                   and str(x.get("underlyingType")) in {"1", "2", "3", "4"}
                   and x.get("settleCoin") == "USDT" for x in entries):
            raise ValueError("No listed Bybit TradFi option chain for " + base)
        specs, cursor = [], ""
        for _ in range(4):
            params = {"category": "option", "baseCoin": base, "limit": 500}
            if cursor:
                params["cursor"] = cursor
            page = self.get("instruments-info", params, 600).get("result", {})
            specs.extend(page.get("list", []))
            cursor = page.get("nextPageCursor") or ""
            if not cursor:
                break
        if cursor:
            raise ValueError("Bybit option catalogue exceeds bounded pagination")
        body = self.get("tickers", {"category": "option", "baseCoin": base})
        stamp = number(body.get("time"), positive=True)
        if stamp is None or not -5 <= time.time() - stamp / 1000 <= 30:
            raise ValueError("Stale Bybit option snapshot")
        return option_context(base, specs, body.get("result", {}).get("list", []), stamp / 1000)

    def historical_anchor(self, instrument, primary):
        """Cold-start mapping from the SAME completed minute, never today's ratio to yesterday."""
        symbol, _, mapped = PRODUCTS[instrument]
        stamp = number(primary.get("ts"), positive=True)
        price = number(primary.get("value"), positive=True)
        if (not mapped or primary.get("timestamp_kind") != "bar_start"
                or stamp is None or price is None or not 60 <= time.time()-stamp <= 72*3600):
            return None
        start = int(stamp // 60) * 60000
        data = self.get("kline", {"category": "linear", "symbol": symbol, "interval": "1",
                                 "start": start, "end": start+59999, "limit": 1})
        rows = data.get("result", {}).get("list", [])
        if not rows or int(rows[0][0]) != start:
            return None
        proxy = number(rows[0][4], positive=True)
        return {"price": price, "proxy": proxy, "ts": stamp, "symbol": symbol} if proxy else None


def option_context(base, specs, tickers, now):
    """Native-strike IV and unsigned gamma concentration, never dealer GEX."""
    specs = {x.get("symbol"): x for x in specs if x.get("status") == "Trading"
             and x.get("baseCoin") == base and x.get("settleCoin") == "USDT"}
    groups, rejected = {}, 0
    for row in tickers:
        try:
            spec = specs[row["symbol"]]
            strike = float(row["symbol"].split("-")[2])
            expiry = float(spec["deliveryTime"]) / 1000
            iv = number(row.get("markIv"), positive=True)
            spot = number(row.get("underlyingPrice"), positive=True)
            bid, ask = number(row.get("bid1Price"), positive=True), number(row.get("ask1Price"), positive=True)
            if (not math.isfinite(strike) or strike <= 0 or not math.isfinite(expiry)
                    or expiry <= now + 3600 or iv is None or iv > 5 or spot is None
                    or bid is None or ask is None or ask < bid or (ask-bid)/((ask+bid)/2) > 0.5
                    or not number(row.get("bid1Size"), positive=True)
                    or not number(row.get("ask1Size"), positive=True)):
                raise ValueError("invalid/thin option")
            groups.setdefault(expiry, []).append((strike, iv, spot,
                number(row.get("gamma")), number(row.get("openInterest")), spec.get("optionsType")))
        except (KeyError, TypeError, ValueError, IndexError):
            rejected += 1
    surface, gamma = [], []
    for expiry, rows in sorted(groups.items())[:3]:
        spot = statistics.median(x[2] for x in rows)
        # Consistent underlying and at least three distinct strikes, no filling holes.
        rows = [x for x in rows if abs(x[2]/spot - 1) <= 0.01]
        strikes = sorted({x[0] for x in rows})
        if len(strikes) < 3:
            continue
        surface.append({"days": (expiry-now)/86400, "expiry": str(int(expiry)),
                        "strikes": strikes, "ivs": [statistics.mean(x[1] for x in rows if x[0] == k) for k in strikes],
                        "spot_at_snapshot": spot})
        for k in strikes:
            for side in ("Call", "Put"):
                points = [x for x in rows if x[0] == k and x[5] == side and x[3] is not None and x[4] is not None]
                if points:
                    gamma.append({"strike": k, "expiry": expiry, "side": side,
                                  "value": sum(x[3]*x[4]*spot*spot*0.01 for x in points)})
    if not surface:
        raise ValueError("Bybit IV coverage insufficient after liquidity checks")
    return {"status": "delayed", "ts": now, "source": f"Bybit {base} PerpOptions · context only",
            "production_authority": False, "rejected_rows": rejected,
            "surface": {"value": surface, "status": "delayed", "ts": now,
                        "source": f"Bybit {base} PerpOptions · context only",
                        "spot_current": surface[0]["spot_at_snapshot"], "spot_status": "delayed",
                        "production_authority": False},
            "gamma": gamma, "gamma_units": "USDT delta-notional per 1% move · unsigned · multiplier 1",
            "zero_flip": None, "base": base}


def fallback_quote(primary, quote, anchor, now):
    if fresh_quote(primary, now) or not fresh_quote(quote, now, 45):
        return primary
    result = dict(quote)
    if quote.get("mapped_proxy"):
        if not anchor or not 0 <= now-anchor["ts"] <= 72*3600 or anchor["symbol"] != quote["symbol"]:
            return primary
        factor = anchor["price"] / anchor["proxy"]
        for key in ("value", "bid", "ask"):
            result[key] *= factor
        result.update({"anchor_ts": anchor["ts"], "anchor_ticker": "NAS100",
                       "driver_ticker": quote["symbol"], "source": quote["source"] + " → NAS100 mapped"})
    result.update({"fallback": True, "error": "Primary unavailable/stale; Bybit reference, verify broker stop/BE",
                   "primary_source": primary.get("source")})
    return result
