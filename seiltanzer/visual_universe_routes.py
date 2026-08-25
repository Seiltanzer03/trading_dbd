"""Read-only data contracts for the two experimental universe visualizations.

The visualizations are intentionally isolated from the existing terminal panels.
They never mutate market state, research state, policy selection, CVaR constraints
or execution. Missing source data stays missing: no synthetic fallback is used.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from fastapi import FastAPI

from . import active_edge_ai_integration as active_edge_ai
from . import active_edge_policy_weight as active_edge_weight
from .active_edge_policy_weight import (
    CONTRACT_VERSION as EDGE_WEIGHT_CONTRACT,
    HIGH_RISK_ONLY_CAP,
    MAX_EDGE_WEIGHT,
)
from .edge_discovery.ai_context import (
    _latest_frozen_context,
    canonical_current_feature_map,
)


CONTRACT_VERSION = "visual-universe-scenes-v1"
RATES_CACHE_TTL_SEC = 300.0
RATES_FAILURE_TTL_SEC = 60.0

RATE_SERIES = (
    {"id": "UST_13W", "ticker": "^IRX", "label": "13W", "maturity_years": 0.25},
    {"id": "UST_5Y", "ticker": "^FVX", "label": "5Y", "maturity_years": 5.0},
    {"id": "UST_10Y", "ticker": "^TNX", "label": "10Y", "maturity_years": 10.0},
    {"id": "UST_30Y", "ticker": "^TYX", "label": "30Y", "maturity_years": 30.0},
)

_RATES_LOCK = threading.Lock()
_RATES_CACHE: dict[str, Any] = {"expires_ts": 0.0, "payload": None}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _last_finite_pairs(timestamps: list[Any], closes: list[Any]) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for ts, close in zip(timestamps, closes):
        tsv = _finite(ts)
        value = _finite(close)
        if tsv is not None and value is not None:
            pairs.append((tsv, value))
    return pairs


def parse_yahoo_chart(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    """Extract one honest current/previous observation from Yahoo chart JSON."""
    chart = payload.get("chart") or {}
    error = chart.get("error")
    results = chart.get("result") or []
    if error or not results:
        raise ValueError(str(error or f"Yahoo returned no chart for {ticker}"))
    result = results[0] or {}
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0] or {})
    closes = quote.get("close") or []
    pairs = _last_finite_pairs(timestamps, closes)

    current = _finite(meta.get("regularMarketPrice"))
    if current is None and pairs:
        current = pairs[-1][1]
    if current is None:
        raise ValueError(f"Yahoo returned no finite value for {ticker}")

    previous = None
    if len(pairs) >= 2:
        previous = pairs[-2][1]
    if previous is None:
        previous = _finite(meta.get("chartPreviousClose"))
    if previous is None:
        previous = _finite(meta.get("previousClose"))

    asof = _finite(meta.get("regularMarketTime"))
    if asof is None and pairs:
        asof = pairs[-1][0]
    return {
        "ticker": ticker,
        "value": current,
        "previous": previous,
        "asof": asof,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
    }


def _fetch_yahoo_daily(ticker: str, timeout: float = 3.0) -> dict[str, Any]:
    encoded = urllib.parse.quote(ticker, safe="")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded}?range=5d&interval=1d&includePrePost=false"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 Seiltanzer-Terminal/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.load(response)
    return parse_yahoo_chart(body, ticker)


def _spread(nodes: dict[str, dict[str, Any]], left: str, right: str) -> dict[str, Any] | None:
    a, b = nodes.get(left), nodes.get(right)
    if not a or not b or not a.get("available") or not b.get("available"):
        return None
    value = float(b["yield_pct"]) - float(a["yield_pct"])
    change = None
    if a.get("change_bps") is not None and b.get("change_bps") is not None:
        change = float(b["change_bps"]) - float(a["change_bps"])
    return {
        "from": left,
        "to": right,
        "spread_bps": round(value * 100.0, 3),
        "change_bps": round(change, 3) if change is not None else None,
    }


def build_rates_orbit_payload(
    *,
    now: float | None = None,
    fetcher: Callable[[str], dict[str, Any]] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return the observed Treasury curve used by RATES ORBITAL SYSTEM.

    This function deliberately does not infer a missing 2Y point and does not
    interpolate missing yields. Each visual node maps to one received series.
    """
    now = float(now or time.time())
    fetcher = fetcher or _fetch_yahoo_daily
    if use_cache:
        with _RATES_LOCK:
            cached = _RATES_CACHE.get("payload")
            if cached is not None and now < float(_RATES_CACHE.get("expires_ts") or 0.0):
                return cached

    rows: list[dict[str, Any]] = []
    for spec in RATE_SERIES:
        row = {
            **spec,
            "available": False,
            "yield_pct": None,
            "previous_yield_pct": None,
            "change_bps": None,
            "asof": None,
            "source": "Yahoo Finance chart",
            "status": "no_data",
        }
        try:
            raw = fetcher(str(spec["ticker"]))
            current = _finite(raw.get("value"))
            previous = _finite(raw.get("previous"))
            if current is None:
                raise ValueError("non-finite yield")
            row.update({
                "available": True,
                "yield_pct": round(current, 6),
                "previous_yield_pct": round(previous, 6) if previous is not None else None,
                "change_bps": round((current - previous) * 100.0, 3)
                if previous is not None else None,
                "asof": _finite(raw.get("asof")),
                "exchange": raw.get("exchange"),
                "status": "delayed",
            })
        except Exception as exc:
            row["reason"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)

    by_id = {str(row["id"]): row for row in rows}
    spreads = [item for item in (
        _spread(by_id, "UST_13W", "UST_10Y"),
        _spread(by_id, "UST_5Y", "UST_10Y"),
        _spread(by_id, "UST_10Y", "UST_30Y"),
    ) if item is not None]
    available_rows = [row for row in rows if row["available"]]
    short10 = next((row for row in spreads
                    if row["from"] == "UST_13W" and row["to"] == "UST_10Y"), None)
    curve_state = "INSUFFICIENT_DATA"
    if short10 is not None:
        curve_state = (
            "SHORT_10Y_INVERTED" if float(short10["spread_bps"]) < 0
            else "SHORT_10Y_POSITIVE"
        )
    latest_asof = max(
        (float(row["asof"]) for row in available_rows if row.get("asof") is not None),
        default=None,
    )
    payload = {
        "contract_version": CONTRACT_VERSION,
        "available": bool(available_rows),
        "curve_available": len(available_rows) >= 2,
        "asof": latest_asof,
        "fetched_ts": now,
        "status": "delayed" if available_rows else "no_data",
        "source": "Yahoo Finance daily/delayed Treasury yield indices",
        "series": rows,
        "spreads": spreads,
        "curve_state": curve_state,
        "semantics": {
            "orbital_angle": "log maturity",
            "orbital_radius": "observed yield level",
            "vertical_axis": "daily yield change in basis points",
            "interpolation": False,
            "synthetic_fallback": False,
            "irx_is_13_week_tbill": True,
        },
        "production_authority": False,
    }
    if use_cache:
        ttl = RATES_CACHE_TTL_SEC if available_rows else RATES_FAILURE_TTL_SEC
        with _RATES_LOCK:
            _RATES_CACHE["payload"] = payload
            _RATES_CACHE["expires_ts"] = now + ttl
    return payload


def _safe_call(obj: Any, method: str) -> dict[str, Any]:
    fn = getattr(obj, method, None)
    if not callable(fn):
        return {"available": False, "reason": f"{method} unavailable"}
    try:
        result = fn()
        return result if isinstance(result, dict) else {"available": False}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _compact_horizons(status: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in status.get("horizons") or []:
        if not isinstance(row, dict):
            continue
        keep = {
            key: row.get(key) for key in (
                "horizon_minutes", "status", "observation_n", "resolved_n",
                "model_n", "candidate_n", "edge_candidate_n", "effective_n",
                "primary_oos_n", "maturity", "edge_maturity",
            ) if key in row
        }
        if not keep:
            keep = dict(row)
        output.append(keep)
    return output


def _edge_measurement_available(active: dict[str, Any]) -> bool:
    explicit = active.get("measurement_available")
    if explicit is not None:
        return bool(explicit)
    return not bool(active.get("reason"))


def _visual_profile(
    active: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any]:
    """Project policy-weight diagnostics for display without fabricating zeros."""
    measurement_available = _edge_measurement_available(active)
    output = dict(profile)
    output["measurement_available"] = measurement_available
    output["report_state"] = active.get("report_state")
    output["source_report_n"] = active.get("source_report_n")
    output["expected_report_n"] = active.get("expected_report_n")
    if measurement_available:
        return output
    for key in (
        "weight_fraction",
        "max_weight_fraction",
        "direction_score",
        "agreement",
        "preferred_close_fraction",
        "strict_directional_share",
        "independent_bucket_n",
        "matched_directional_signal_n",
        "strict_directional_signal_n",
    ):
        output[key] = None
    output["available"] = False
    return output


def _edge_decision_reason(active: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    """Explain unavailable/zero/non-zero Universe states without adding a vote."""
    if not _edge_measurement_available(active):
        state = str(active.get("report_state") or "")
        if state == "CURRENT_SHA_REPORTS_PARTIAL":
            return {"code": "EDGE_REPORTS_PARTIAL", "label": "EDGE REPORTS PARTIAL"}
        if state == "CURRENT_SHA_REPORTS_MISSING":
            return {"code": "EDGE_REPORTS_MISSING", "label": "EDGE REPORTS MISSING"}
        return {"code": "EDGE_CONTEXT_UNAVAILABLE", "label": "EDGE N/A"}

    total_active = max(0, int(active.get("total_active_signal_n") or 0))
    matched = max(0, int(active.get("matched_structured_signal_n") or 0))
    supporting = max(0, int(active.get("supporting_position_n") or 0))
    opposing = max(0, int(active.get("opposing_position_n") or 0))
    directional = supporting + opposing
    buckets = max(0, int(profile.get("independent_bucket_n") or 0))
    weight = _finite(profile.get("weight_fraction")) or 0.0

    if total_active == 0:
        return {"code": "NO_ACTIVE_EDGE", "label": "NO ACTIVE EDGE"}
    if matched == 0:
        return {"code": "NO_T0_MATCH", "label": "NO T0 MATCH"}
    if directional == 0:
        return {"code": "NON_DIRECTIONAL_ONLY", "label": "NON-DIR ONLY"}
    if buckets == 0 or weight <= 0.0:
        return {"code": "ZERO_NET_DIRECTION", "label": "ZERO NET"}
    return {"code": "ACTIVE_MATCH", "label": "ACTIVE MATCH"}


def build_edge_universe_payload(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    """Aggregate current T0 edge, canonical features and prospective feedback."""
    now = float(now or time.time())
    trade = engine.journal.active_trade()
    instrument = str((trade or {}).get("instrument") or engine.market.instrument_code or "")
    direction = str((trade or {}).get("direction") or "")
    snapshot = {
        "captured_ts": now,
        "strategy": {"instrument": instrument, "direction": direction},
    }

    try:
        active = active_edge_ai.build_active_edge_context(engine, snapshot)
    except Exception as exc:
        active = {
            "available": False,
            "measurement_available": False,
            "report_state": "EDGE_CONTEXT_EXCEPTION",
            "matched_groups": [],
            "reason": f"{type(exc).__name__}: {exc}",
        }
    active = active if isinstance(active, dict) else {}
    raw_profile = active_edge_weight.edge_weight_profile(active)
    decision_reason = _edge_decision_reason(active, raw_profile)
    profile = _visual_profile(active, raw_profile)

    feature_map: dict[str, Any] = {}
    observation_t0 = None
    try:
        frozen = _latest_frozen_context(engine, snapshot)
        observation_t0 = _finite(frozen.get("observation_t0"))
        feature_map = canonical_current_feature_map(frozen, instrument) if frozen else {}
    except Exception:
        feature_map = {}
    available_features = sum(
        1 for row in feature_map.values()
        if isinstance(row, dict) and row.get("available") and not row.get("stale")
    )
    stale_features = sum(
        1 for row in feature_map.values()
        if isinstance(row, dict) and row.get("stale")
    )

    runtime = getattr(engine, "short_horizon", None)
    g1s_status = _safe_call(runtime, "status") if runtime is not None else {
        "available": False, "reason": "short_horizon runtime unavailable"}
    management = getattr(engine, "management_local", None)
    management_status = _safe_call(management, "status") if management is not None else {
        "available": False, "reason": "management_local runtime unavailable"}
    management_edge = _safe_call(management, "edge") if management is not None else {
        "available": False, "reason": "management_local runtime unavailable"}

    cross_asset_payload = _safe_call(engine, "cross_asset_payload")
    cross_asset_summary = cross_asset_payload.get("summary") or {}
    if not isinstance(cross_asset_summary, dict):
        cross_asset_summary = {}

    return {
        "contract_version": CONTRACT_VERSION,
        "captured_ts": now,
        "instrument": instrument,
        "direction": direction,
        "trade_id": (trade or {}).get("id"),
        "active_edge": active,
        "production_weight": {
            **profile,
            "decision_reason": decision_reason,
            "weight_contract": EDGE_WEIGHT_CONTRACT,
            "high_risk_only_cap": HIGH_RISK_ONLY_CAP,
            "absolute_cap": MAX_EDGE_WEIGHT,
            "scope": "SOFT_POLICY_RANKING_INSIDE_HARD_RISK_ELIGIBLE_SET",
            "hard_risk_override": False,
            "cvar_override": False,
            "may_widen_stop": False,
            "automatic_execution": False,
        },
        "canonical_features": {
            "observation_t0": observation_t0,
            "total_n": len(feature_map),
            "available_n": available_features,
            "stale_n": stale_features,
            "items": feature_map,
        },
        "g1s": {
            "contract_version": g1s_status.get("contract_version"),
            "horizons": _compact_horizons(g1s_status),
            "status": g1s_status.get("status"),
        },
        "management_attribution": {
            "status": management_status,
            "edge": management_edge,
        },
        "cross_asset": {
            "available": bool(cross_asset_payload.get("available")),
            "version": cross_asset_payload.get("version"),
            "summary": cross_asset_summary,
            "break_alerts": cross_asset_payload.get("break_alerts") or [],
            "production_authority": False,
            "independent_vote": False,
        },
        "semantics": {
            "node_height": "net vote ratio relative to current position",
            "radial_distance": "signal horizon minutes",
            "node_mass": "matched candidate count",
            "strictness": "strict-reference participation",
            "missing_active_edge_is_not_zero": True,
            "random_motion": False,
        },
        "production_authority": False,
        "visualization_only": True,
    }


def install_visual_universe_routes(app: FastAPI) -> None:
    """Install the two removable read-only visualization endpoints."""
    if getattr(app.state, "visual_universe_routes_installed", False):
        return

    def rates_orbit():
        return build_rates_orbit_payload()

    def edge_universe():
        return build_edge_universe_payload(app.state.engine)

    app.add_api_route(
        "/api/visual/rates-orbit", rates_orbit,
        methods=["GET"], name="visual_rates_orbit")
    app.add_api_route(
        "/api/visual/edge-universe", edge_universe,
        methods=["GET"], name="visual_edge_universe")
    app.state.visual_universe_routes_installed = True
