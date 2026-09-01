"""Read-only global crypto market page backed only by observed free data.

CoinGecko supplies the global/spot snapshot.  Yahoo supplies hourly histories
used for correlations and trajectories.  Either source may fail independently;
missing observations stay ``None`` and are exposed as N/A by the UI.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import statistics
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse


CRYPTO_PAGE = Path(__file__).resolve().parent / "web" / "crypto.html"
CONTRACT_VERSION = "crypto-global-observed-v1"
REFRESH_SEC = 300.0
STALE_SEC = 360.0

CRYPTO_ASSETS = (
    {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "yahoo": "BTC-USD"},
    {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "yahoo": "ETH-USD"},
    {"id": "binancecoin", "symbol": "BNB", "name": "BNB", "yahoo": "BNB-USD"},
    {"id": "solana", "symbol": "SOL", "name": "Solana", "yahoo": "SOL-USD"},
    {"id": "ripple", "symbol": "XRP", "name": "XRP", "yahoo": "XRP-USD"},
    {"id": "cardano", "symbol": "ADA", "name": "Cardano", "yahoo": "ADA-USD"},
    {"id": "dogecoin", "symbol": "DOGE", "name": "Dogecoin", "yahoo": "DOGE-USD"},
    {"id": "avalanche-2", "symbol": "AVAX", "name": "Avalanche", "yahoo": "AVAX-USD"},
    {"id": "chainlink", "symbol": "LINK", "name": "Chainlink", "yahoo": "LINK-USD"},
)


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value, digits: int = 4) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _iso_ts(value) -> float | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _json_get(url: str, timeout: float = 8.0) -> dict | list:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SeiltanzerTerminal/0.1 (read-only market data)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _fetch_coingecko() -> tuple[dict | None, list[dict], dict]:
    base = "https://api.coingecko.com/api/v3"
    ids = ",".join(asset["id"] for asset in CRYPTO_ASSETS)
    query = urllib.parse.urlencode({
        "vs_currency": "usd",
        "ids": ids,
        "order": "market_cap_desc",
        "per_page": len(CRYPTO_ASSETS),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d",
    })
    errors: list[str] = []
    global_data = None
    markets: list[dict] = []
    try:
        response = _json_get(f"{base}/global")
        if isinstance(response, dict) and isinstance(response.get("data"), dict):
            global_data = response["data"]
        else:
            errors.append("global response has no data object")
    except Exception as exc:  # noqa: BLE001 - upstream failure becomes provenance
        errors.append(f"global: {type(exc).__name__}: {str(exc)[:100]}")
    try:
        response = _json_get(f"{base}/coins/markets?{query}")
        if isinstance(response, list):
            markets = [row for row in response if isinstance(row, dict)]
        else:
            errors.append("markets response is not a list")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"markets: {type(exc).__name__}: {str(exc)[:100]}")
    return global_data, markets, {
        "provider": "CoinGecko public API",
        "status": "observed" if (global_data or markets) else "no_data",
        "observed_at": time.time() if (global_data or markets) else None,
        "stale_after_sec": 300.0,
        "error": "; ".join(errors) or None,
        "endpoints": ["/api/v3/global", "/api/v3/coins/markets"],
    }


def _fetch_yahoo_hourly() -> tuple[dict[str, list[tuple[float, float]]], dict]:
    series: dict[str, list[tuple[float, float]]] = {
        asset["symbol"]: [] for asset in CRYPTO_ASSETS
    }
    errors: list[str] = []

    def fetch_asset(asset: dict) -> tuple[str, list[tuple[float, float]]]:
        symbol = asset["symbol"]
        ticker = urllib.parse.quote(asset["yahoo"], safe="")
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{ticker}?range=7d&interval=1h&events=history")
        response = _json_get(url, timeout=6.0)
        chart = response.get("chart") if isinstance(response, dict) else None
        results = chart.get("result") if isinstance(chart, dict) else None
        result = results[0] if isinstance(results, list) and results else None
        timestamps = result.get("timestamp") if isinstance(result, dict) else None
        indicators = result.get("indicators") if isinstance(result, dict) else None
        quotes = indicators.get("quote") if isinstance(indicators, dict) else None
        closes = quotes[0].get("close") if isinstance(quotes, list) and quotes else None
        if not isinstance(timestamps, list) or not isinstance(closes, list):
            raise RuntimeError("chart has no hourly closes")
        points = []
        for raw_ts, raw_price in zip(timestamps, closes):
            timestamp, price = _finite(raw_ts), _finite(raw_price)
            if timestamp is not None and price is not None and price > 0:
                points.append((timestamp, price))
        if len(points) < 2:
            raise RuntimeError("chart has fewer than two valid closes")
        return symbol, points

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="crypto-yahoo") as pool:
        jobs = {pool.submit(fetch_asset, asset): asset["symbol"] for asset in CRYPTO_ASSETS}
        for future in as_completed(jobs):
            symbol = jobs[future]
            try:
                returned_symbol, points = future.result()
                series[returned_symbol] = points
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{symbol}: {type(exc).__name__}: {str(exc)[:70]}")
    available = sum(bool(points) for points in series.values())
    return series, {
        "provider": "Yahoo Finance chart API",
        "status": "observed" if available else "no_data",
        "observed_at": max(
            (points[-1][0] for points in series.values() if points),
            default=None,
        ),
        "interval": "1h",
        "window": "7d",
        "available_assets": available,
        "stale_after_sec": 7200.0,
        "error": "; ".join(errors)[:500] or (None if available else "no valid hourly closes"),
    }


def _returns(points: list[tuple[float, float]]) -> dict[float, float]:
    result: dict[float, float] = {}
    for (previous_ts, previous), (current_ts, current) in zip(points, points[1:]):
        if previous > 0 and current > 0 and current_ts > previous_ts:
            result[current_ts] = math.log(current / previous)
    return result


def _correlation(left: dict[float, float], right: dict[float, float], minimum: int = 36) -> tuple[float | None, int]:
    shared = sorted(set(left) & set(right))
    if len(shared) < minimum:
        return None, len(shared)
    a = [left[ts] for ts in shared]
    b = [right[ts] for ts in shared]
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    da = [value - mean_a for value in a]
    db = [value - mean_b for value in b]
    denom = math.sqrt(sum(value * value for value in da) * sum(value * value for value in db))
    if denom <= 0:
        return None, len(shared)
    return sum(x * y for x, y in zip(da, db)) / denom, len(shared)


def _window_change(points: list[tuple[float, float]], hours: int) -> float | None:
    if len(points) < 2 or points[-1][1] <= 0:
        return None
    target = points[-1][0] - hours * 3600.0
    candidates = [point for point in points if point[0] <= target]
    if not candidates:
        return None
    previous_ts, previous = candidates[-1]
    # Do not call a partial window "7d": the chosen observation must be no
    # more than two hourly bars away from the requested boundary.
    if previous <= 0 or target - previous_ts > 2 * 3600:
        return None
    return (points[-1][1] / previous - 1.0) * 100.0


def _realized_vol(points: list[tuple[float, float]], periods: int = 24) -> float | None:
    values = list(_returns(points).values())[-periods:]
    if len(values) < 12:
        return None
    return statistics.stdev(values) * math.sqrt(24 * 365) * 100.0


def _leadership_path(series: dict[str, list[tuple[float, float]]]) -> list[dict]:
    price_maps = {symbol: dict(points) for symbol, points in series.items() if points}
    btc = price_maps.get("BTC") or {}
    timestamps = sorted(btc)[-72:]
    path: list[dict] = []
    alt_symbols = [symbol for symbol in price_maps if symbol != "BTC"]
    for index, timestamp in enumerate(timestamps):
        if index < 24:
            continue
        previous_ts = timestamps[index - 24]
        btc_now, btc_previous = btc.get(timestamp), btc.get(previous_ts)
        if not btc_now or not btc_previous:
            continue
        alt_changes = []
        for symbol in alt_symbols:
            now_price = price_maps[symbol].get(timestamp)
            previous_price = price_maps[symbol].get(previous_ts)
            if now_price and previous_price:
                alt_changes.append((now_price / previous_price - 1.0) * 100.0)
        if len(alt_changes) < 3:
            continue
        path.append({
            "ts": timestamp,
            "btc_24h_pct": _round((btc_now / btc_previous - 1.0) * 100.0, 3),
            "alt_median_24h_pct": _round(statistics.median(alt_changes), 3),
            "breadth_positive": _round(sum(value > 0 for value in alt_changes) / len(alt_changes), 4),
            "observed_alts": len(alt_changes),
        })
    return path


def _market_observation(global_data: dict | None, markets: list[dict]) -> dict:
    data = global_data or {}
    market_cap = data.get("total_market_cap") if isinstance(data.get("total_market_cap"), dict) else {}
    volume = data.get("total_volume") if isinstance(data.get("total_volume"), dict) else {}
    dominance = data.get("market_cap_percentage") if isinstance(data.get("market_cap_percentage"), dict) else {}
    return {
        "active_cryptocurrencies": int(data["active_cryptocurrencies"])
        if _finite(data.get("active_cryptocurrencies")) is not None else None,
        "total_market_cap_usd": _round(market_cap.get("usd"), 2),
        "total_volume_24h_usd": _round(volume.get("usd"), 2),
        "market_cap_change_24h_pct": _round(data.get("market_cap_change_percentage_24h_usd"), 3),
        "btc_dominance_pct": _round(dominance.get("btc"), 3),
        "eth_dominance_pct": _round(dominance.get("eth"), 3),
        "tracked_assets_observed": len(markets),
    }


def build_crypto_global_payload(
    *,
    coingecko_fetcher: Callable[[], tuple[dict | None, list[dict], dict]] = _fetch_coingecko,
    history_fetcher: Callable[[], tuple[dict[str, list[tuple[float, float]]], dict]] = _fetch_yahoo_hourly,
    now: float | None = None,
) -> dict:
    """Build one immutable, fully-provenanced observed crypto snapshot."""
    built_at = float(now if now is not None else time.time())
    # The two providers are independent.  Fetch them concurrently so a blocked
    # upstream cannot serially extend first-page warmup; the HTTP route itself
    # still only reads this background cache.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="crypto-source") as pool:
        coingecko_job = pool.submit(coingecko_fetcher)
        history_job = pool.submit(history_fetcher)
        try:
            global_data, market_rows, coingecko_meta = coingecko_job.result()
        except Exception as exc:  # noqa: BLE001
            global_data, market_rows, coingecko_meta = None, [], {
                "provider": "CoinGecko public API", "status": "no_data",
                "observed_at": None, "stale_after_sec": 300.0,
                "error": f"{type(exc).__name__}: {str(exc)[:140]}",
            }
        try:
            series, yahoo_meta = history_job.result()
        except Exception as exc:  # noqa: BLE001
            series = {asset["symbol"]: [] for asset in CRYPTO_ASSETS}
            yahoo_meta = {
                "provider": "Yahoo Finance chart API", "status": "no_data",
                "observed_at": None, "stale_after_sec": 7200.0,
                "error": f"{type(exc).__name__}: {str(exc)[:140]}",
            }
    market_by_id = {str(row.get("id")): row for row in market_rows}
    returns = {symbol: _returns(points) for symbol, points in series.items()}
    btc_returns = returns.get("BTC") or {}

    assets = []
    for definition in CRYPTO_ASSETS:
        symbol = definition["symbol"]
        row = market_by_id.get(definition["id"]) or {}
        points = series.get(symbol) or []
        correlation, shared_n = _correlation(btc_returns, returns.get(symbol) or {})
        if symbol == "BTC" and len(btc_returns) >= 36:
            correlation, shared_n = 1.0, len(btc_returns)
        change_7d = _finite(row.get("price_change_percentage_7d_in_currency"))
        change_7d_source = "CoinGecko coins/markets"
        if change_7d is None:
            change_7d = _window_change(points, 24 * 7)
            change_7d_source = "Yahoo observed hourly closes" if change_7d is not None else None
        market_asof = _iso_ts(row.get("last_updated"))
        history_asof = points[-1][0] if points else None
        asset_asof = max((value for value in (market_asof, history_asof) if value is not None), default=None)
        assets.append({
            "id": definition["id"],
            "symbol": symbol,
            "name": definition["name"],
            "price_usd": _round(row.get("current_price"), 8),
            "market_cap_usd": _round(row.get("market_cap"), 2),
            "volume_24h_usd": _round(row.get("total_volume"), 2),
            "change_1h_pct": _round(row.get("price_change_percentage_1h_in_currency"), 3),
            "change_24h_pct": _round(row.get("price_change_percentage_24h_in_currency"), 3),
            "change_7d_pct": _round(change_7d, 3),
            "change_7d_source": change_7d_source,
            "realized_vol_24h_annual_pct": _round(_realized_vol(points), 2),
            "btc_correlation_7d": _round(correlation, 4),
            "correlation_observations": shared_n,
            "history_observations": len(points),
            "asof": asset_asof,
            "age_sec": _round(max(0.0, built_at - asset_asof), 1) if asset_asof is not None else None,
            "available": bool(row or points),
        })

    symbols = [asset["symbol"] for asset in CRYPTO_ASSETS]
    matrix: list[list[float | None]] = []
    count_matrix: list[list[int]] = []
    for left in symbols:
        row, counts = [], []
        for right in symbols:
            value, count = _correlation(returns.get(left) or {}, returns.get(right) or {})
            if left == right and len(returns.get(left) or {}) >= 36:
                value, count = 1.0, len(returns[left])
            row.append(_round(value, 4))
            counts.append(count)
        matrix.append(row)
        count_matrix.append(counts)

    observed_24h = [asset["change_24h_pct"] for asset in assets if asset["change_24h_pct"] is not None]
    alt_24h = [asset["change_24h_pct"] for asset in assets if asset["symbol"] != "BTC" and asset["change_24h_pct"] is not None]
    btc_24h = next((asset["change_24h_pct"] for asset in assets if asset["symbol"] == "BTC"), None)
    breadth = (sum(value > 0 for value in observed_24h) / len(observed_24h)) if observed_24h else None
    median_24h = statistics.median(observed_24h) if observed_24h else None
    alt_median = statistics.median(alt_24h) if alt_24h else None
    leadership = (btc_24h - alt_median) if btc_24h is not None and alt_median is not None else None
    observations = []
    if breadth is None:
        observations.append("Ширина рынка N/A: CoinGecko не вернул 24h изменения.")
    elif breadth >= 0.7 and (median_24h or 0) > 0:
        observations.append(f"Повышение широкое: {sum(v > 0 for v in observed_24h)}/{len(observed_24h)} наблюдаемых активов растут за 24h.")
    elif breadth <= 0.3 and (median_24h or 0) < 0:
        observations.append(f"Снижение широкое: {sum(v < 0 for v in observed_24h)}/{len(observed_24h)} наблюдаемых активов падают за 24h.")
    else:
        observations.append(f"Рынок смешанный: доля растущих активов {breadth:.0%}.")
    if leadership is not None:
        direction = "опережает" if leadership >= 0 else "отстаёт от"
        observations.append(f"BTC {direction} медианы наблюдаемых альткоинов на {abs(leadership):.2f} п.п. за 24h.")
    observations.append("Это описательный snapshot, не прогноз и не торговый сигнал.")

    has_coingecko = coingecko_meta.get("status") == "observed"
    has_yahoo = yahoo_meta.get("status") == "observed"
    state = "observed" if has_coingecko and has_yahoo else "partial" if has_coingecko or has_yahoo else "no_data"
    return {
        "contract_version": CONTRACT_VERSION,
        "available": state != "no_data",
        "status": state,
        "built_at": built_at,
        "global": _market_observation(global_data, market_rows),
        "assets": assets,
        "correlation": {
            "symbols": symbols,
            "matrix": matrix,
            "observations": count_matrix,
            "minimum_pair_observations": 36,
            "window": "7d hourly log returns",
        },
        "leadership_path": _leadership_path(series),
        "summary": {
            "breadth_positive_24h": _round(breadth, 4),
            "median_change_24h_pct": _round(median_24h, 3),
            "btc_vs_alt_median_24h_pp": _round(leadership, 3),
            "observed_change_assets": len(observed_24h),
            "observations_ru": observations,
            "authority": "descriptive_only",
        },
        "sources": {"coingecko": coingecko_meta, "yahoo": yahoo_meta},
        "freshness": {
            "stale_after_sec": STALE_SEC,
            "stale": False,
            "stale_reason": None,
            "oldest_source_age_sec": _round(max((max(0.0, built_at - float(value)) for value in (
                coingecko_meta.get("observed_at"), yahoo_meta.get("observed_at"))
                if _finite(value) is not None), default=0.0), 1),
        },
    }


def crypto_warming_payload() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "available": False,
        "status": "warming",
        "built_at": None,
        "global": {},
        "assets": [],
        "correlation": {"symbols": [], "matrix": [], "observations": []},
        "leadership_path": [],
        "summary": {
            "observations_ru": ["Первый реальный snapshot ещё загружается."],
            "authority": "descriptive_only",
        },
        "sources": {},
        "freshness": {"stale_after_sec": STALE_SEC, "stale": False, "stale_reason": None},
    }


class CryptoGlobalCache:
    """Non-blocking stale-while-refresh cache for the external data bundle."""

    def __init__(self, loader: Callable[[], dict] = build_crypto_global_payload):
        self.loader = loader
        self._lock = threading.Lock()
        self._payload: dict | None = None
        self._loaded_at: float | None = None
        self._building = False
        self._last_error: str | None = None

    def refresh_now(self) -> None:
        try:
            payload = self.loader()
            if not isinstance(payload, dict):
                raise TypeError("crypto loader returned non-object")
            with self._lock:
                # A total upstream outage must not erase the last observed
                # topology, but the old topology keeps its original timestamp
                # and will become explicit STALE in ``get`` below.
                preserve = (
                    self._payload is not None
                    and payload.get("status") == "no_data"
                    and self._payload.get("status") in {"observed", "partial"}
                )
                if preserve:
                    failures = [
                        str(source.get("error"))
                        for source in (payload.get("sources") or {}).values()
                        if isinstance(source, dict) and source.get("error")
                    ]
                    self._last_error = "; ".join(failures)[:280] or "all upstream sources unavailable"
                else:
                    self._payload = payload
                    self._loaded_at = time.time()
                    self._last_error = None
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {str(exc)[:140]}"
        finally:
            with self._lock:
                self._building = False

    def start_refresh(self) -> bool:
        with self._lock:
            if self._building:
                return False
            self._building = True
        threading.Thread(target=self.refresh_now, name="crypto-global-refresh", daemon=True).start()
        return True

    def get(self) -> dict:
        now = time.time()
        with self._lock:
            payload = copy.deepcopy(self._payload)
            loaded_at = self._loaded_at
            building = self._building
            error = self._last_error
        age = max(0.0, now - loaded_at) if loaded_at is not None else None
        if not building and (age is None or age >= REFRESH_SEC):
            self.start_refresh()
            building = True
        result = payload if payload is not None else crypto_warming_payload()
        stale = payload is not None and age is not None and age >= STALE_SEC
        if stale:
            result["status"] = "stale"
            result["available"] = True
            result["freshness"] = dict(result.get("freshness") or {})
            result["freshness"].update({
                "stale": True,
                "stale_after_sec": STALE_SEC,
                "stale_reason": error or "cached payload exceeded freshness limit",
            })
        result["transport"] = {
            "cache_state": (
                "WARMING" if payload is None else
                "STALE_REFRESHING" if stale and building else
                "STALE" if stale else
                "REFRESHING" if building else "FRESH"
            ),
            "payload_age_sec": _round(age, 2),
            "refresh_in_progress": building,
            "last_refresh_error": error,
        }
        return result


def install_crypto_global_routes(app: FastAPI, *, cache: CryptoGlobalCache | None = None) -> None:
    if getattr(app.state, "crypto_global_routes_installed", False):
        return
    cache = cache or CryptoGlobalCache()
    cache.start_refresh()

    def crypto_page():
        return FileResponse(CRYPTO_PAGE)

    def crypto_snapshot():
        return cache.get()

    app.add_api_route("/crypto", crypto_page, methods=["GET"], name="crypto_global")
    app.add_api_route("/api/crypto/global", crypto_snapshot, methods=["GET"], name="crypto_global_snapshot")
    app.state.crypto_global_cache = cache
    app.state.crypto_global_routes_installed = True
