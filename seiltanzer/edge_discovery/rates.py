"""Causal U.S. Treasury rates context for strategy-agnostic edge research.

The first RATES source is the official U.S. Treasury *Daily Treasury Par Yield
Curve Rates*.  Those observations are daily closing-market context, not an
intraday rates feed.  To avoid same-session look-ahead, a source-date value is
made research-available only from 00:00 UTC on the following calendar day.

Consequently this module deliberately exposes daily changes/ranks/z-scores and
never fabricates 5m/15m velocity from a forward-filled daily observation.
Intraday rate dynamics require a future provider with real intraday as-of
observations.
"""
from __future__ import annotations

import bisect
import csv
import io
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

from .feature_view import feature_value


RATES_CONTRACT_VERSION = "g1s-rates-context-v1"
TREASURY_SOURCE = "U.S. Treasury Daily Treasury Par Yield Curve Rates"
TREASURY_SOURCE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/all/{yyyymm}?_format=csv&"
    "field_tdr_date_value_month={yyyymm}&page=&type=daily_treasury_yield_curve"
)
# Five calendar days admits normal weekends/market holidays but rejects an old
# macro print after a prolonged source outage.  This is intentionally a slow
# regime context, not a fresh intraday signal.
RATES_MAX_STALENESS_SECONDS = 5 * 24 * 60 * 60.0
ROLLING_WINDOW = 20

BASE_RATE_FEATURE_IDS = (
    "rates.us10y_yield",
    "rates.us02y_yield",
    "rates.curve_2s10s",
)
DAILY_DERIVED_FEATURE_IDS = (
    "rates.us10y_change_1d",
    "rates.us02y_change_1d",
    "rates.curve_2s10s_change_1d",
    "rates.us10y_rolling_zscore_20d",
    "rates.us02y_rolling_zscore_20d",
    "rates.curve_2s10s_rolling_zscore_20d",
    "rates.us10y_rolling_rank_20d",
    "rates.us02y_rolling_rank_20d",
    "rates.curve_2s10s_rolling_rank_20d",
)
RATE_FEATURE_IDS = BASE_RATE_FEATURE_IDS + DAILY_DERIVED_FEATURE_IDS


@dataclass(frozen=True)
class TreasuryDailyRate:
    source_date: str
    source_date_ts: float
    asof: float
    us02y: float
    us10y: float
    source: str = TREASURY_SOURCE

    @property
    def curve_2s10s(self) -> float:
        return float(self.us10y) - float(self.us02y)


@dataclass(frozen=True)
class RatesState:
    source_date: str
    asof: float
    values: dict[str, float | None]
    source: str = TREASURY_SOURCE


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_to_source_ts(value: str) -> float:
    parsed = datetime.strptime(str(value).strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def conservative_daily_asof(source_date_ts: float) -> float:
    """Earliest safe generic as-of for a Treasury daily closing observation.

    Treasury daily par yields are based on closing market bid prices.  We do not
    assume a same-day publication timestamp that the source contract does not
    guarantee.  The next UTC day is therefore a conservative causal boundary.
    """
    source_day = datetime.fromtimestamp(float(source_date_ts), tz=timezone.utc)
    next_day = datetime(
        source_day.year, source_day.month, source_day.day, tzinfo=timezone.utc
    ) + timedelta(days=1)
    return next_day.timestamp()


def parse_treasury_daily_csv(text: str) -> list[TreasuryDailyRate]:
    rows: list[TreasuryDailyRate] = []
    reader = csv.DictReader(io.StringIO(str(text)))
    for raw in reader:
        date_text = str(raw.get("Date") or raw.get("DATE") or "").strip()
        us02y = _finite(raw.get("2 Yr") if "2 Yr" in raw else raw.get("2 YR"))
        us10y = _finite(raw.get("10 Yr") if "10 Yr" in raw else raw.get("10 YR"))
        if not date_text or us02y is None or us10y is None:
            continue
        try:
            source_ts = _date_to_source_ts(date_text)
        except ValueError:
            continue
        rows.append(TreasuryDailyRate(
            source_date=date_text,
            source_date_ts=source_ts,
            asof=conservative_daily_asof(source_ts),
            us02y=us02y,
            us10y=us10y,
        ))
    rows.sort(key=lambda item: (item.asof, item.source_date))
    deduped: dict[float, TreasuryDailyRate] = {item.source_date_ts: item for item in rows}
    return [deduped[key] for key in sorted(deduped)]


def _month_keys(start_ts: float, end_ts: float) -> list[str]:
    start = datetime.fromtimestamp(float(start_ts), tz=timezone.utc)
    end = datetime.fromtimestamp(float(end_ts), tz=timezone.utc)
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    last = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    values: list[str] = []
    while cursor <= last:
        values.append(f"{cursor.year:04d}{cursor.month:02d}")
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return values


def treasury_month_url(yyyymm: str) -> str:
    token = str(yyyymm)
    if len(token) != 6 or not token.isdigit():
        raise ValueError("yyyymm must be YYYYMM")
    return TREASURY_SOURCE_URL.format(yyyymm=token)


def fetch_treasury_daily_rates(
    start_ts: float,
    end_ts: float,
    *,
    fetch_text: Callable[[str], str] | None = None,
) -> list[TreasuryDailyRate]:
    """Fetch official daily 2Y/10Y observations covering a research interval.

    One extra calendar month is requested before the target interval so the
    first research rows can have a causal prior state and rolling context.
    Network access belongs in off-host research/collector work, never in a
    request-time terminal path.
    """
    if float(end_ts) < float(start_ts):
        raise ValueError("end_ts must be >= start_ts")
    start = datetime.fromtimestamp(float(start_ts), tz=timezone.utc)
    padded_start = (start - timedelta(days=35)).timestamp()

    def default_fetch(url: str) -> str:
        request = Request(url, headers={"User-Agent": "trading-dbd-research/1.0"})
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed official host
            return response.read().decode("utf-8-sig")

    loader = fetch_text or default_fetch
    values: dict[float, TreasuryDailyRate] = {}
    for month in _month_keys(padded_start, float(end_ts)):
        for item in parse_treasury_daily_csv(loader(treasury_month_url(month))):
            if item.source_date_ts <= float(end_ts) + 24 * 60 * 60.0:
                values[item.source_date_ts] = item
    return [values[key] for key in sorted(values)]


def _zscore(values: list[float], current: float) -> float | None:
    if len(values) < ROLLING_WINDOW:
        return None
    window = values[-ROLLING_WINDOW:]
    mean = sum(window) / len(window)
    variance = sum((value - mean) ** 2 for value in window) / len(window)
    std = math.sqrt(max(0.0, variance))
    return 0.0 if std <= 1e-12 else (float(current) - mean) / std


def _rank(values: list[float], current: float) -> float | None:
    if len(values) < ROLLING_WINDOW:
        return None
    window = values[-ROLLING_WINDOW:]
    return sum(value <= float(current) for value in window) / len(window)


def build_rates_states(observations: Iterable[TreasuryDailyRate]) -> list[RatesState]:
    ordered = sorted(observations, key=lambda item: (item.asof, item.source_date_ts))
    states: list[RatesState] = []
    h2: list[float] = []
    h10: list[float] = []
    curve: list[float] = []
    previous: TreasuryDailyRate | None = None
    for item in ordered:
        c = item.curve_2s10s
        h2.append(float(item.us02y)); h10.append(float(item.us10y)); curve.append(float(c))
        values: dict[str, float | None] = {
            "rates.us10y_yield": float(item.us10y),
            "rates.us02y_yield": float(item.us02y),
            "rates.curve_2s10s": float(c),
            "rates.us10y_change_1d": (
                None if previous is None else float(item.us10y) - float(previous.us10y)),
            "rates.us02y_change_1d": (
                None if previous is None else float(item.us02y) - float(previous.us02y)),
            "rates.curve_2s10s_change_1d": (
                None if previous is None else c - previous.curve_2s10s),
            "rates.us10y_rolling_zscore_20d": _zscore(h10, item.us10y),
            "rates.us02y_rolling_zscore_20d": _zscore(h2, item.us02y),
            "rates.curve_2s10s_rolling_zscore_20d": _zscore(curve, c),
            "rates.us10y_rolling_rank_20d": _rank(h10, item.us10y),
            "rates.us02y_rolling_rank_20d": _rank(h2, item.us02y),
            "rates.curve_2s10s_rolling_rank_20d": _rank(curve, c),
        }
        states.append(RatesState(item.source_date, item.asof, values))
        previous = item
    return states


def rates_feature_values_at_t0(
    states: Iterable[RatesState],
    *,
    instrument: str,
    t0: float,
    horizon: int,
    confirmatory_frozen_at_t0: bool = False,
    max_staleness_seconds: float = RATES_MAX_STALENESS_SECONDS,
) -> dict[str, dict[str, Any]]:
    ordered = sorted(states, key=lambda item: item.asof)
    asofs = [float(item.asof) for item in ordered]
    index = bisect.bisect_right(asofs, float(t0) + 1e-6) - 1
    if index < 0:
        return {}
    state = ordered[index]
    age = max(0.0, float(t0) - float(state.asof))
    stale = age > float(max_staleness_seconds)
    output: dict[str, dict[str, Any]] = {}
    for feature_id in RATE_FEATURE_IDS:
        value = state.values.get(feature_id)
        record = feature_value(
            instrument=str(instrument), t0=float(t0), horizon=int(horizon),
            feature_id=feature_id, value=value, asof=(state.asof if value is not None else None),
            quality=1.0, stale_after_seconds=float(max_staleness_seconds),
            historical_available=True, live_available=bool(confirmatory_frozen_at_t0),
            training_eligible=bool(not stale), dependency_group="rates_daily",
        ).as_dict()
        record.update({
            "source": TREASURY_SOURCE,
            "source_date": state.source_date,
            "source_age_seconds": age,
            "slow_macro_context": True,
            "intraday_velocity_claim": False,
            "prospective_confirmation_eligible": bool(confirmatory_frozen_at_t0),
            "future_points_used": False,
        })
        output[feature_id] = record
    return output


def attach_rates_context(
    rows: Iterable[dict[str, Any]],
    states: Iterable[RatesState],
    *,
    confirmatory_frozen_at_t0: bool = False,
) -> None:
    """Attach the most recent causal daily rates state to research rows in-place."""
    frozen_states = tuple(states)
    for row in rows:
        records = rates_feature_values_at_t0(
            frozen_states,
            instrument=str(row["instrument"]),
            t0=float(row["captured_ts"]),
            horizon=int(row["horizon_minutes"]),
            confirmatory_frozen_at_t0=confirmatory_frozen_at_t0,
        )
        if not records:
            continue
        row.setdefault("feature_values", {}).update(records)
        ede = row.setdefault("ede_features", {})
        for feature_id, record in records.items():
            if bool(record.get("training_eligible")):
                ede[feature_id] = record.get("value")
        row["rates_context"] = {
            "contract_version": RATES_CONTRACT_VERSION,
            "source": TREASURY_SOURCE,
            "confirmatory_frozen_at_t0": bool(confirmatory_frozen_at_t0),
            "intraday_velocity_claim": False,
            "future_points_used": False,
        }
