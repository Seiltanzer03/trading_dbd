"""Fail-closed official ALFRED vintage transport for historical BLS features.

FRED/ALFRED are Federal Reserve Bank of St. Louis services.  The release
calendar supplies the release date and ALFRED supplies the data vintage that
was visible on that date.  Because ALFRED vintages are day-granular, records
become causally admissible only at midnight Central on the following day.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import httpx

from .macro_bls_historical_bootstrap import BLSReleaseSpec, FAMILIES, _period


SOURCE_KIND = "OFFICIAL_FED_ALFRED_BLS_VINTAGE"
FRED_HOST = "fred.stlouisfed.org"
ALFRED_HOST = "alfred.stlouisfed.org"
FRED_RELEASE_IDS = {"CPI": "10", "NFP": "50"}
SERIES = {
    "CPI": ("CPIAUCSL", "CPILFESL", "CPIAUCNS", "CPILFENS"),
    "NFP": ("PAYEMS", "UNRATE", "CES0500000003"),
}
DEFAULT_TIMEOUT_SECONDS = 45.0


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _official_fed_history_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    valid_path = (
        host == FRED_HOST and parsed.path == "/releases/calendar"
    ) or (
        host == ALFRED_HOST and parsed.path == "/graph/alfredgraph.csv"
    )
    return parsed.scheme == "https" and valid_path and not parsed.fragment


def _month_shift(period: str, offset: int) -> str:
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", str(period))
    if match is None:
        raise ValueError("ALFRED_BLS_PERIOD_INVALID")
    ordinal = int(match.group(1)) * 12 + int(match.group(2)) - 1 + int(offset)
    return _period(ordinal // 12, ordinal % 12 + 1)


def _period_for_release(release_date: str) -> str:
    try:
        released = date.fromisoformat(str(release_date))
    except ValueError as exc:
        raise ValueError("ALFRED_BLS_RELEASE_DATE_INVALID") from exc
    if released.month == 1:
        return _period(released.year - 1, 12)
    return _period(released.year, released.month - 1)


def conservative_available_at(release_date: str) -> float:
    """Return the first instant after the complete day-granular vintage."""
    try:
        released = date.fromisoformat(str(release_date))
    except ValueError as exc:
        raise ValueError("ALFRED_BLS_RELEASE_DATE_INVALID") from exc
    available = datetime.combine(
        released + timedelta(days=1), datetime.min.time(),
        tzinfo=ZoneInfo("America/Chicago"),
    )
    return float(available.timestamp())


def fred_calendar_url(family: str, *, start_date: str, end_date: str) -> str:
    if family not in FAMILIES:
        raise ValueError("ALFRED_BLS_FAMILY_INVALID")
    query = urlencode({
        "rdc": "1", "vs": start_date, "ve": end_date,
        "rid": FRED_RELEASE_IDS[family],
    })
    return f"https://{FRED_HOST}/releases/calendar?{query}"


def parse_fred_release_calendar(
    content: str, *, family: str, source_url: str,
) -> list[str]:
    if family not in FAMILIES or not _official_fed_history_url(source_url):
        raise ValueError("ALFRED_BLS_CALENDAR_SOURCE_INVALID")
    parsed_url = urlparse(source_url)
    query = parse_qs(parsed_url.query, strict_parsing=True)
    if (
        parsed_url.hostname != FRED_HOST
        or parsed_url.path != "/releases/calendar"
        or query.get("rdc") != ["1"]
        or query.get("rid") != [FRED_RELEASE_IDS[family]]
        or len(query.get("vs", [])) != 1
        or len(query.get("ve", [])) != 1
    ):
        raise ValueError("ALFRED_BLS_CALENDAR_SOURCE_INVALID")
    try:
        payload = json.loads(str(content))
    except json.JSONDecodeError as exc:
        raise ValueError("ALFRED_BLS_CALENDAR_JSON_INVALID") from exc
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("ALFRED_BLS_CALENDAR_EVENTS_INVALID")
    start = date.fromisoformat(query["vs"][0])
    end = date.fromisoformat(query["ve"][0])
    output: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or event.get("title") != "1 release":
            raise ValueError("ALFRED_BLS_CALENDAR_EVENT_INVALID")
        try:
            released = date.fromisoformat(str(event.get("start") or ""))
        except ValueError as exc:
            raise ValueError("ALFRED_BLS_CALENDAR_EVENT_INVALID") from exc
        if not start <= released <= end:
            raise ValueError("ALFRED_BLS_CALENDAR_RANGE_INVALID")
        output.add(released.isoformat())
    if not output:
        raise ValueError("ALFRED_BLS_CALENDAR_EVENTS_MISSING")
    return sorted(output)


def alfred_series_url(series_id: str, *, period: str, vintage_date: str) -> str:
    allowed = {item for values in SERIES.values() for item in values}
    if series_id not in allowed:
        raise ValueError("ALFRED_BLS_SERIES_INVALID")
    start = _month_shift(period, -14) + "-01"
    end = period + "-01"
    query = urlencode({
        "id": series_id, "cosd": start, "coed": end,
        "vintage_date": vintage_date,
    })
    return f"https://{ALFRED_HOST}/graph/alfredgraph.csv?{query}"


def parse_alfred_csv(
    content: str, *, series_id: str, vintage_date: str,
) -> dict[str, float]:
    rows = list(csv.reader(StringIO(str(content))))
    expected_header = [
        "observation_date", f"{series_id}_{vintage_date.replace('-', '')}",
    ]
    if not rows or rows[0] != expected_header:
        raise ValueError("ALFRED_BLS_CSV_HEADER_INVALID")
    output: dict[str, float] = {}
    for row in rows[1:]:
        if len(row) != 2 or not row[1] or row[1] == ".":
            continue
        try:
            observed = date.fromisoformat(row[0])
            value = float(row[1])
        except (ValueError, TypeError) as exc:
            raise ValueError("ALFRED_BLS_CSV_ROW_INVALID") from exc
        if observed.day != 1 or not math.isfinite(value):
            raise ValueError("ALFRED_BLS_CSV_ROW_INVALID")
        output[observed.strftime("%Y-%m")] = value
    if not output:
        raise ValueError("ALFRED_BLS_CSV_VALUES_MISSING")
    return output


def _pct(current: float, previous: float) -> float:
    if previous == 0:
        raise ValueError("ALFRED_BLS_ZERO_DENOMINATOR")
    return (current / previous - 1.0) * 100.0


def build_vintage_payload(
    *, family: str, period: str, release_date: str,
    values: dict[str, dict[str, float]],
) -> dict[str, Any]:
    if family not in FAMILIES or set(values) != set(SERIES[family]):
        raise ValueError("ALFRED_BLS_SERIES_SET_INVALID")
    if period != _period_for_release(release_date):
        raise ValueError("ALFRED_BLS_RELEASE_PERIOD_MISMATCH")
    previous = _month_shift(period, -1)
    if family == "CPI":
        previous_2 = _month_shift(period, -2)
        year_ago = _month_shift(period, -12)
        headline_mom = _pct(values["CPIAUCSL"][period], values["CPIAUCSL"][previous])
        prior_headline_mom = _pct(
            values["CPIAUCSL"][previous], values["CPIAUCSL"][previous_2])
        core_mom = _pct(values["CPILFESL"][period], values["CPILFESL"][previous])
        prior_core_mom = _pct(
            values["CPILFESL"][previous], values["CPILFESL"][previous_2])
        payload = {
            "family": family, "period": period,
            "headline_mom_pct": headline_mom,
            "core_mom_pct": core_mom,
            "headline_yoy_pct": _pct(
                values["CPIAUCNS"][period], values["CPIAUCNS"][year_ago]),
            "core_yoy_pct": _pct(
                values["CPILFENS"][period], values["CPILFENS"][year_ago]),
            "headline_mom_change_pp": headline_mom - prior_headline_mom,
            "core_mom_change_pp": core_mom - prior_core_mom,
        }
    else:
        previous_2 = _month_shift(period, -2)
        year_ago = _month_shift(period, -12)
        payroll = values["PAYEMS"]
        unemployment = values["UNRATE"]
        earnings = values["CES0500000003"]
        payload = {
            "family": family, "period": period,
            "payroll_change_k": payroll[period] - payroll[previous],
            "previous_payroll_change_k": payroll[previous] - payroll[previous_2],
            "unemployment_rate_pct": unemployment[period],
            "unemployment_change_pp": unemployment[period] - unemployment[previous],
            "average_hourly_earnings_mom_pct": _pct(
                earnings[period], earnings[previous]),
            "average_hourly_earnings_yoy_pct": _pct(
                earnings[period], earnings[year_ago]),
        }
    return payload | {
        "consensus_available": False,
        "surprise_computed": False,
        "historical_archive_vintage": True,
        "historical_alfred_vintage": True,
        "official_release_date": release_date,
        "causal_available_at": conservative_available_at(release_date),
        "source_kind": SOURCE_KIND,
    }


def parse_vintage_evidence(
    raw: str, *, spec: BLSReleaseSpec, calendar_dates: set[str],
) -> dict[str, Any]:
    try:
        evidence = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("ALFRED_BLS_EVIDENCE_JSON_INVALID") from exc
    if not isinstance(evidence, dict):
        raise ValueError("ALFRED_BLS_EVIDENCE_JSON_INVALID")
    release_date = str(evidence.get("release_date") or "")
    if (
        evidence.get("source_kind") != SOURCE_KIND
        or evidence.get("family") != spec.family
        or release_date not in calendar_dates
        or spec.period != _period_for_release(release_date)
        or spec.published_at != conservative_available_at(release_date)
    ):
        raise ValueError("ALFRED_BLS_EVIDENCE_SPEC_MISMATCH")
    rows = evidence.get("series")
    if not isinstance(rows, list) or len(rows) != len(SERIES[spec.family]):
        raise ValueError("ALFRED_BLS_EVIDENCE_SERIES_INVALID")
    values: dict[str, dict[str, float]] = {}
    for row in rows:
        series_id = str(row.get("series_id") or "")
        content = str(row.get("content") or "")
        expected_url = alfred_series_url(
            series_id, period=spec.period, vintage_date=release_date)
        if (
            series_id not in SERIES[spec.family]
            or series_id in values
            or row.get("source_url") != expected_url
            or not _official_fed_history_url(expected_url)
            or row.get("source_sha256") != _sha(content)
        ):
            raise ValueError("ALFRED_BLS_EVIDENCE_SERIES_INVALID")
        values[series_id] = parse_alfred_csv(
            content, series_id=series_id, vintage_date=release_date)
    return build_vintage_payload(
        family=spec.family, period=spec.period,
        release_date=release_date, values=values,
    )


class OfficialALFREDBLSVintageSource:
    def __init__(
        self, *, timeout_sec: float = DEFAULT_TIMEOUT_SECONDS, fetch_attempts: int = 3,
        retry_delay_sec: float = 2.0,
    ) -> None:
        self.timeout_sec = max(5.0, min(45.0, float(timeout_sec)))
        self.fetch_attempts = max(1, min(4, int(fetch_attempts)))
        self.retry_delay_sec = max(0.0, min(10.0, float(retry_delay_sec)))

    def _client(self, *, proxy: str | None) -> httpx.Client:
        from .macro_transport_refinement import BROWSER_USER_AGENT
        return httpx.Client(
            timeout=self.timeout_sec, follow_redirects=True, proxy=proxy,
            trust_env=False,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"},
        )

    @staticmethod
    def _validated(response: httpx.Response) -> str:
        response.raise_for_status()
        if not _official_fed_history_url(str(response.url)):
            raise ValueError("ALFRED_BLS_REDIRECT_REJECTED")
        content = response.text
        if len(content) < 20 or len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("ALFRED_BLS_RESPONSE_SIZE_INVALID")
        return content

    def _fetch(self, url: str) -> str:
        from .macro_transport_refinement import macro_proxy_url
        routes = [("DIRECT_OFFICIAL", None)]
        proxy = macro_proxy_url()
        if proxy:
            routes.append(("CONFIGURED_PROXY", proxy))
        errors = []
        for route_name, route_proxy in routes:
            for attempt in range(1, self.fetch_attempts + 1):
                try:
                    with self._client(proxy=route_proxy) as client:
                        return self._validated(client.get(url))
                except httpx.TransportError as exc:
                    if attempt < self.fetch_attempts:
                        time.sleep(self.retry_delay_sec * attempt)
                        continue
                    errors.append(f"{route_name}:{type(exc).__name__}")
                    break
                except (httpx.HTTPStatusError, ValueError, TypeError) as exc:
                    # A rejected source, malformed response or deterministic
                    # HTTP status is a contract failure, not retryable evidence.
                    errors.append(f"{route_name}:{type(exc).__name__}")
                    break
        raise ValueError(
            "ALFRED_BLS_OFFICIAL_TRANSPORT_EXHAUSTED:" + "|".join(errors)
        )

    def calendar(
        self, *, family: str, start_ts: float, end_ts: float,
    ) -> tuple[str, str, list[str]]:
        start_date = datetime.fromtimestamp(start_ts, tz=ZoneInfo("UTC")).date().isoformat()
        end_date = datetime.fromtimestamp(end_ts, tz=ZoneInfo("UTC")).date().isoformat()
        url = fred_calendar_url(family, start_date=start_date, end_date=end_date)
        content = self._fetch(url)
        return content, url, parse_fred_release_calendar(
            content, family=family, source_url=url)

    def vintage_record(
        self, *, family: str, release_date: str, calendar_url: str,
    ) -> tuple[BLSReleaseSpec, float, str, dict[str, Any]]:
        period = _period_for_release(release_date)
        series_evidence = []
        for series_id in SERIES[family]:
            url = alfred_series_url(
                series_id, period=period, vintage_date=release_date)
            content = self._fetch(url)
            series_evidence.append({
                "series_id": series_id, "source_url": url,
                "content": content, "source_sha256": _sha(content),
            })
        raw = _canonical({
            "source_kind": SOURCE_KIND, "family": family,
            "release_date": release_date, "series": series_evidence,
        })
        spec = BLSReleaseSpec(
            family=family, period=period,
            published_at=conservative_available_at(release_date),
            source_url=calendar_url,
        )
        payload = parse_vintage_evidence(
            raw, spec=spec, calendar_dates={release_date})
        return spec, time.time(), raw, payload


def spec_dict(spec: BLSReleaseSpec) -> dict[str, Any]:
    return asdict(spec)
