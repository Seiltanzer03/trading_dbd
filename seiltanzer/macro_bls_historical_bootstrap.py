"""Point-in-time BLS CPI/NFP archive bootstrap for EDE research.

The live numeric macro runtime intentionally uses first-seen timestamps and never
backfills old T0 captures.  This module is a separate *historical research*
source: it downloads the BLS release calendar plus the archived release copy that
was publicly available at that release time, parses only values printed in that
archive, and exposes them to old EDE T0 rows without mutating those rows.

Important safety properties:
- official ``bls.gov`` HTTPS only;
- release ``published_at`` comes from the official BLS release calendar;
- no current BLS time-series values are projected backwards;
- no consensus/surprise is invented;
- archive payloads are immutable and SHA256-addressed;
- a feature is visible only when ``published_at <= T0``;
- production/trading authority remains false.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx


BLS_HISTORICAL_BOOTSTRAP_VERSION = "macro-bls-archive-point-in-time-v1"
BLS_SCHEDULE_TEMPLATE = "https://www.bls.gov/schedule/{year}/home.htm"
BLS_ICAL_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BLS_ARCHIVE_TEMPLATE = "https://www.bls.gov/news.release/archives/{slug}_{date_code}.htm"
BLS_HOSTS = frozenset({"www.bls.gov", "bls.gov"})
FAMILIES = ("CPI", "NFP")
ARCHIVE_SLUG = {"CPI": "cpi", "NFP": "empsit"}
RELEASE_LABEL = {
    "CPI": "Consumer Price Index",
    "NFP": "Employment Situation",
}
MONTHS = {
    name.lower(): index for index, name in enumerate((
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ), start=1)
}
HISTORICAL_LOOKBACK_BUFFER_SEC = 45.0 * 86400.0
MAX_BOOTSTRAP_CALENDAR_YEARS = 5

CPI_FEATURES = {
    "headline_mom_pct": "macro.cpi_headline_mom_pct",
    "core_mom_pct": "macro.cpi_core_mom_pct",
    "headline_yoy_pct": "macro.cpi_headline_yoy_pct",
    "core_yoy_pct": "macro.cpi_core_yoy_pct",
    "headline_mom_change_pp": "macro.cpi_headline_mom_change_pp",
    "core_mom_change_pp": "macro.cpi_core_mom_change_pp",
}
NFP_FEATURES = {
    "payroll_change_k": "macro.nfp_payroll_change_k",
    "previous_payroll_change_k": "macro.nfp_previous_payroll_change_k",
    "unemployment_rate_pct": "macro.nfp_unemployment_rate_pct",
    "unemployment_change_pp": "macro.nfp_unemployment_change_pp",
    "average_hourly_earnings_mom_pct": "macro.nfp_wage_mom_pct",
    "average_hourly_earnings_yoy_pct": "macro.nfp_wage_yoy_pct",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _period(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def _period_from_text(text: str) -> str | None:
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
        text or "", flags=re.I)
    if not match:
        return None
    return _period(int(match.group(2)), MONTHS[match.group(1).lower()])


def _official_bls_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in BLS_HOSTS


class _TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.text_parts: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"}:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        clean = re.sub(r"\s+", " ", str(data)).strip()
        if not clean:
            return
        self.text_parts.append(clean)
        if self._cell is not None:
            self._cell.append(clean)


@dataclass(frozen=True)
class BLSReleaseSpec:
    family: str
    period: str
    published_at: float
    source_url: str


def _parse_calendar_datetime(date_text: str, time_text: str) -> float | None:
    clean_date = re.sub(r"\s+", " ", date_text).strip()
    clean_time = re.sub(r"\s+", " ", time_text).strip().upper()
    date_value = None
    for fmt in ("%A, %B %d, %Y", "%B %d, %Y"):
        try:
            date_value = datetime.strptime(clean_date, fmt)
            break
        except ValueError:
            continue
    if date_value is None:
        return None
    try:
        time_value = datetime.strptime(clean_time, "%I:%M %p")
    except ValueError:
        return None
    local = date_value.replace(
        hour=time_value.hour, minute=time_value.minute, second=0, microsecond=0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    return local.timestamp()


def parse_bls_schedule(html: str, *, year: int) -> list[BLSReleaseSpec]:
    """Extract CPI/Employment release vintages from one official yearly calendar."""
    parser = _TableTextParser(); parser.feed(html or "")
    output: dict[tuple[str, str, float], BLSReleaseSpec] = {}
    for row in parser.rows:
        if len(row) < 3:
            continue
        description = " ".join(row[2:])
        family = None
        for candidate, label in RELEASE_LABEL.items():
            if re.search(rf"\b{re.escape(label)}\s+for\s+", description, flags=re.I):
                family = candidate
                break
        if family is None:
            continue
        period = _period_from_text(description)
        published_at = _parse_calendar_datetime(row[0], row[1])
        if period is None or published_at is None:
            continue
        local = datetime.fromtimestamp(published_at, tz=ZoneInfo("America/New_York"))
        date_code = local.strftime("%m%d%Y")
        source_url = BLS_ARCHIVE_TEMPLATE.format(
            slug=ARCHIVE_SLUG[family], date_code=date_code)
        spec = BLSReleaseSpec(
            family=family, period=period, published_at=float(published_at),
            source_url=source_url)
        output[(family, period, float(published_at))] = spec
    return sorted(output.values(), key=lambda item: (item.published_at, item.family))


def parse_bls_ical(calendar: str) -> list[BLSReleaseSpec]:
    """Extract causal CPI/NFP releases from the official machine calendar.

    The BLS iCalendar identifies the release family and exact Eastern release
    timestamp. CPI and Employment Situation are monthly releases for the prior
    calendar month; the canonical archive parser independently verifies that
    derived period against the period printed in the official archived release.
    """
    unfolded: list[str] = []
    for raw in str(calendar or "").replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw[1:]
        else:
            unfolded.append(raw.rstrip("\r"))
    output: dict[tuple[str, str, float], BLSReleaseSpec] = {}
    for block in "\n".join(unfolded).split("BEGIN:VEVENT")[1:]:
        event = block.split("END:VEVENT", 1)[0]
        fields: dict[str, str] = {}
        for line in event.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
        summary = fields.get("SUMMARY", "")
        family = next(
            (candidate for candidate, label in RELEASE_LABEL.items()
             if summary.casefold() == label.casefold()),
            None,
        )
        if family is None:
            continue
        calendar_time = next(
            ((key, value) for key, value in fields.items()
             if key.startswith("DTSTART")),
            ("", ""),
        )
        if calendar_time[0] not in {
            "DTSTART;TZID=US-Eastern",
            "DTSTART;TZID=America/New_York",
        }:
            continue
        raw_dt = calendar_time[1]
        match = re.fullmatch(r"(\d{8})T(\d{6})", raw_dt)
        if match is None:
            continue
        local = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(
            tzinfo=ZoneInfo("America/New_York")
        )
        if local.month == 1:
            period = _period(local.year - 1, 12)
        else:
            period = _period(local.year, local.month - 1)
        published_at = float(local.timestamp())
        source_url = BLS_ARCHIVE_TEMPLATE.format(
            slug=ARCHIVE_SLUG[family], date_code=local.strftime("%m%d%Y")
        )
        output[(family, period, published_at)] = BLSReleaseSpec(
            family=family,
            period=period,
            published_at=published_at,
            source_url=source_url,
        )
    return sorted(output.values(), key=lambda item: (item.published_at, item.family))


def _cell_number(value: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value).replace("$", ""))
    return _finite(match.group(0).replace(",", "")) if match else None


def _row_numbers(row: list[str]) -> list[float]:
    output = []
    for cell in row[1:]:
        number = _cell_number(cell)
        if number is not None:
            output.append(float(number))
    return output


def _label(value: str) -> str:
    clean = re.sub(r"\([^)]*\)", "", str(value)).lower()
    clean = re.sub(r"[^a-z]+", " ", clean).strip()
    return clean


def _first_numeric_row(rows: list[list[str]], exact_label: str, *,
                       min_n: int, max_n: int | None = None) -> list[float]:
    wanted = _label(exact_label)
    for row in rows:
        if not row or _label(row[0]) != wanted:
            continue
        numbers = _row_numbers(row)
        if len(numbers) < min_n:
            continue
        if max_n is not None and len(numbers) > max_n:
            continue
        return numbers
    raise ValueError("BLS_ARCHIVE_ROW_MISSING:" + wanted.replace(" ", "_"))


def parse_cpi_archive(html: str, *, expected_period: str) -> dict[str, Any]:
    parser = _TableTextParser(); parser.feed(html or "")
    full_text = " ".join(parser.text_parts)
    title = re.search(
        r"CONSUMER\s+PRICE\s+INDEX\s*-\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})",
        full_text, flags=re.I)
    if not title:
        raise ValueError("CPI_ARCHIVE_PERIOD_NOT_FOUND")
    period = _period(int(title.group(2)), MONTHS[title.group(1).lower()])
    if period != expected_period:
        raise ValueError("CPI_ARCHIVE_PERIOD_MISMATCH")
    headline = _first_numeric_row(parser.rows, "All items", min_n=3)
    core = _first_numeric_row(parser.rows, "All items less food and energy", min_n=3)
    headline_mom, headline_yoy = headline[-2], headline[-1]
    core_mom, core_yoy = core[-2], core[-1]
    return {
        "family": "CPI", "period": period,
        "headline_mom_pct": headline_mom,
        "core_mom_pct": core_mom,
        "headline_yoy_pct": headline_yoy,
        "core_yoy_pct": core_yoy,
        "headline_mom_change_pp": headline_mom-headline[-3],
        "core_mom_change_pp": core_mom-core[-3],
        "consensus_available": False,
        "surprise_computed": False,
        "historical_archive_vintage": True,
    }


def parse_nfp_archive(html: str, *, expected_period: str) -> dict[str, Any]:
    parser = _TableTextParser(); parser.feed(html or "")
    full_text = " ".join(parser.text_parts)
    title = re.search(
        r"THE\s+EMPLOYMENT\s+SITUATION\s*-\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})",
        full_text, flags=re.I)
    if not title:
        raise ValueError("NFP_ARCHIVE_PERIOD_NOT_FOUND")
    period = _period(int(title.group(2)), MONTHS[title.group(1).lower()])
    if period != expected_period:
        raise ValueError("NFP_ARCHIVE_PERIOD_MISMATCH")

    # Summary B reports over-the-month payroll changes directly.  The first
    # exact Total nonfarm row is the summary row; later B-1 rows contain many
    # employment-level columns and are deliberately excluded by max_n.
    payroll = _first_numeric_row(
        parser.rows, "Total nonfarm", min_n=4, max_n=5)
    unemployment = _first_numeric_row(
        parser.rows, "Unemployment rate", min_n=5, max_n=6)
    earnings = _first_numeric_row(
        parser.rows, "Average hourly earnings", min_n=4, max_n=5)
    current_wage, previous_wage, year_ago_wage = earnings[-1], earnings[-2], earnings[0]
    wage_mom = ((current_wage/previous_wage)-1.0)*100.0 if previous_wage else None
    wage_yoy = ((current_wage/year_ago_wage)-1.0)*100.0 if year_ago_wage else None
    if wage_mom is None or wage_yoy is None:
        raise ValueError("NFP_WAGE_HISTORY_INVALID")
    return {
        "family": "NFP", "period": period,
        "payroll_change_k": payroll[-1],
        "previous_payroll_change_k": payroll[-2],
        # Summary A contains current rate as the penultimate value and the
        # current-over-previous change in the final column.
        "unemployment_rate_pct": unemployment[-2],
        "unemployment_change_pp": unemployment[-1],
        "average_hourly_earnings_mom_pct": wage_mom,
        "average_hourly_earnings_yoy_pct": wage_yoy,
        "consensus_available": False,
        "surprise_computed": False,
        "historical_archive_vintage": True,
    }


class BLSHistoricalReleaseStore:
    """Append-only archive-vintage store separate from first-seen live releases."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._conn = runtime._conn
        self._lock = runtime._lock
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_bls_historical_releases(
                    release_id TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    period TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    published_at REAL NOT NULL,
                    fetched_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    UNIQUE(family,period,published_at,source_sha256)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_macro_bls_hist_causal "
                "ON macro_bls_historical_releases(family,published_at,period)")
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS macro_bls_historical_releases_immutable_update
                BEFORE UPDATE ON macro_bls_historical_releases
                BEGIN SELECT RAISE(ABORT,'immutable BLS historical release'); END""")
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS macro_bls_historical_releases_immutable_delete
                BEFORE DELETE ON macro_bls_historical_releases
                BEGIN SELECT RAISE(ABORT,'immutable BLS historical release'); END""")

    def has(self, spec: BLSReleaseSpec) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM macro_bls_historical_releases "
                "WHERE family=? AND period=? AND published_at=? LIMIT 1",
                (spec.family, spec.period, float(spec.published_at)),
            ).fetchone()
        return row is not None

    def ingest(self, spec: BLSReleaseSpec, *, html: str, payload: dict[str, Any],
               fetched_at: float | None = None) -> dict[str, Any]:
        if spec.family not in FAMILIES or payload.get("family") != spec.family:
            raise ValueError("BLS_HISTORICAL_FAMILY_MISMATCH")
        if payload.get("period") != spec.period:
            raise ValueError("BLS_HISTORICAL_PERIOD_MISMATCH")
        if not _official_bls_url(spec.source_url):
            raise ValueError("BLS_HISTORICAL_UNOFFICIAL_SOURCE")
        published_at = float(spec.published_at)
        fetched = float(time.time() if fetched_at is None else fetched_at)
        if not math.isfinite(published_at) or published_at <= 0:
            raise ValueError("BLS_HISTORICAL_PUBLISHED_AT_INVALID")
        if not math.isfinite(fetched) or fetched <= 0 or fetched+300.0 < published_at:
            raise ValueError("BLS_HISTORICAL_FETCHED_AT_INVALID")
        source_sha = _sha((html or "").encode("utf-8"))
        release_id = "macro-bls-hist-" + _sha(
            f"{spec.family}|{spec.period}|{published_at:.6f}|{source_sha}")[:28]
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT release_id FROM macro_bls_historical_releases "
                "WHERE family=? AND period=? AND published_at=? AND source_sha256=?",
                (spec.family, spec.period, published_at, source_sha),
            ).fetchone()
            if existing:
                return {"status": "CACHED", "release_id": str(existing[0]),
                        "family": spec.family, "period": spec.period,
                        "published_at": published_at}
            self._conn.execute(
                "INSERT INTO macro_bls_historical_releases("
                "release_id,family,period,source_url,published_at,fetched_at,payload_json,"
                "source_sha256,contract_version,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (release_id, spec.family, spec.period, spec.source_url, published_at,
                 fetched, _json(payload), source_sha,
                 BLS_HISTORICAL_BOOTSTRAP_VERSION, time.time()),
            )
        return {"status": "STORED", "release_id": release_id,
                "family": spec.family, "period": spec.period,
                "published_at": published_at}

    def latest_admissible(self, family: str, captured_ts: float) -> dict[str, Any]:
        return _latest_historical_release(self.runtime, family, captured_ts)

    def status(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT family,COUNT(*),MIN(published_at),MAX(published_at) "
                "FROM macro_bls_historical_releases GROUP BY family").fetchall()
        materialized = {
            str(row[0]): {
                "row_n": int(row[1]),
                "first_published_at": float(row[2]),
                "latest_published_at": float(row[3]),
            } for row in rows
        }
        return {
            "contract_version": BLS_HISTORICAL_BOOTSTRAP_VERSION,
            "families": {family: materialized.get(family, {
                "row_n": 0, "first_published_at": None, "latest_published_at": None,
            }) for family in FAMILIES},
            "causal_rule": "published_at<=T0",
            "source_kind": "OFFICIAL_ARCHIVED_RELEASE_COPY",
            "mutates_historical_t0_rows": False,
            "current_revised_series_backfill": False,
            "research_only": True,
            "production_authority": False,
        }


def _table_exists(runtime: Any, table: str) -> bool:
    try:
        with runtime._lock:
            row = runtime._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _latest_historical_release(runtime: Any, family: str,
                               captured_ts: float) -> dict[str, Any]:
    if family not in FAMILIES or not _table_exists(runtime, "macro_bls_historical_releases"):
        return {"status": "UNAVAILABLE", "family": family,
                "reason": "NO_HISTORICAL_BLS_TABLE"}
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT release_id,period,source_url,published_at,fetched_at,payload_json,"
            "source_sha256,contract_version FROM macro_bls_historical_releases "
            "WHERE family=? AND published_at<=? "
            "ORDER BY published_at DESC,created_ts DESC LIMIT 1",
            (family, float(captured_ts)),
        ).fetchone()
    if row is None:
        return {"status": "UNAVAILABLE", "family": family,
                "reason": "NO_ARCHIVE_RELEASE_BEFORE_T0"}
    return {
        "status": "VALID", "family": family,
        "release_id": str(row[0]), "period": str(row[1]),
        "source_url": str(row[2]), "published_at": float(row[3]),
        "available_at": float(row[3]), "fetched_at": float(row[4]),
        "payload": json.loads(str(row[5])), "source_sha256": str(row[6]),
        "contract_version": str(row[7]),
        "official_source_verified": True,
        "historical_reconstruction": True,
        "provenance": "OFFICIAL_BLS_ARCHIVE_POINT_IN_TIME",
        "causal_rule": "published_at<=T0",
        "research_only": True,
        "production_authority": False,
    }


def historical_feature_records_from_runtime(
        runtime: Any, *, instrument: str, t0: float, horizon: int) -> tuple[
            dict[str, Any], dict[str, dict[str, Any]]]:
    """Read old-T0 CPI/NFP features without mutating the frozen observation."""
    from .edge_discovery.feature_view import feature_value

    values: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for family, mapping in (("CPI", CPI_FEATURES), ("NFP", NFP_FEATURES)):
        release = _latest_historical_release(runtime, family, t0)
        if release.get("status") != "VALID":
            continue
        payload = release.get("payload") or {}
        asof = float(release["published_at"])
        for source_name, feature_id in mapping.items():
            value = _finite(payload.get(source_name))
            if value is None:
                continue
            record = feature_value(
                instrument=instrument, t0=t0, horizon=horizon,
                feature_id=feature_id, value=value, asof=asof,
                historical_available=True, live_available=True,
                training_eligible=True, dependency_group=f"macro_release:{family}",
            )
            if not record.training_eligible:
                continue
            values[feature_id] = record
            provenance[feature_id] = {
                "provenance": "OFFICIAL_BLS_ARCHIVE_POINT_IN_TIME",
                "release_id": release["release_id"],
                "release_family": family,
                "release_period": release["period"],
                "published_at": asof,
                "available_at": asof,
                "source_url": release["source_url"],
                "source_sha256": release["source_sha256"],
                "official_source_verified": True,
                "historical_reconstruction": True,
                "old_t0_row_mutated": False,
                "current_revised_series_backfill": False,
                "future_points_used": False,
            }
    return values, provenance


class OfficialBLSArchiveSource:
    def __init__(self, *, timeout_sec: float = 12.0) -> None:
        self.timeout_sec = max(4.0, min(30.0, float(timeout_sec)))

    def _client(self) -> httpx.Client:
        from .macro_transport_refinement import BROWSER_USER_AGENT, macro_proxy_url
        return httpx.Client(
            timeout=self.timeout_sec, follow_redirects=True,
            proxy=macro_proxy_url(), trust_env=False,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
        )

    @staticmethod
    def _validated_response(response: httpx.Response) -> str:
        response.raise_for_status()
        if not _official_bls_url(str(response.url)):
            raise ValueError("BLS_HISTORICAL_REDIRECT_REJECTED")
        text = response.text
        if len(text) < 200:
            raise ValueError("BLS_HISTORICAL_RESPONSE_TOO_SHORT")
        return text

    def schedule(self, year: int) -> list[BLSReleaseSpec]:
        url = BLS_SCHEDULE_TEMPLATE.format(year=int(year))
        with self._client() as client:
            response = client.get(url)
            html = self._validated_response(response)
        return parse_bls_schedule(html, year=int(year))

    def archive(self, spec: BLSReleaseSpec) -> tuple[float, str, dict[str, Any]]:
        if not _official_bls_url(spec.source_url):
            raise ValueError("BLS_HISTORICAL_UNOFFICIAL_ARCHIVE_URL")
        with self._client() as client:
            response = client.get(spec.source_url)
            html = self._validated_response(response)
        payload = (
            parse_cpi_archive(html, expected_period=spec.period)
            if spec.family == "CPI"
            else parse_nfp_archive(html, expected_period=spec.period)
        )
        return time.time(), html, payload


def observation_span(runtime: Any) -> tuple[float, float] | None:
    if not _table_exists(runtime, "g1s_observations"):
        return None
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT MIN(captured_ts),MAX(captured_ts) FROM g1s_observations"
        ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    start, end = float(row[0]), float(row[1])
    return (start, end) if math.isfinite(start) and math.isfinite(end) else None


class BLSHistoricalBootstrapRuntime:
    """Low-frequency archive materializer; no request-path network work."""

    def __init__(self, store: BLSHistoricalReleaseStore, *, poll_sec: float = 86400.0,
                 startup_delay_sec: float = 180.0) -> None:
        self.store = store
        self.source = OfficialBLSArchiveSource()
        self.poll_sec = max(6*3600.0, float(poll_sec))
        self.startup_delay_sec = max(30.0, float(startup_delay_sec))
        self._stop = threading.Event(); self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock(); self.running = False
        self.last_started_at: float | None = None
        self.last_finished_at: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="bls-historical-bootstrap", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def refresh(self, *, now: float | None = None) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "IN_PROGRESS", "research_only": True}
            self.running = True
        self.last_started_at = time.time()
        errors: dict[str, str] = {}
        stored: list[dict[str, Any]] = []
        skipped = 0
        try:
            span = observation_span(self.store.runtime)
            if span is None:
                result = {
                    "status": "NO_OBSERVATIONS", "stored": [], "skipped": 0,
                    "errors": {}, "research_only": True, "production_authority": False,
                }
                self.last_result = result; self.last_error = None
                return result
            now_ts = time.time() if now is None else float(now)
            start_ts = max(0.0, span[0]-HISTORICAL_LOOKBACK_BUFFER_SEC)
            end_ts = min(span[1], now_ts)
            start_year = datetime.fromtimestamp(start_ts, tz=ZoneInfo("UTC")).year
            end_year = datetime.fromtimestamp(end_ts, tz=ZoneInfo("UTC")).year
            years = list(range(start_year, end_year+1))
            if len(years) > MAX_BOOTSTRAP_CALENDAR_YEARS:
                raise RuntimeError("BLS_HISTORICAL_OBSERVATION_SPAN_TOO_WIDE")
            specs: list[BLSReleaseSpec] = []
            for year in years:
                try:
                    specs.extend(self.source.schedule(year))
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    errors[f"schedule:{year}"] = f"{type(exc).__name__}:{str(exc)[:180]}"
            dedup = {
                (item.family, item.period, item.published_at): item for item in specs
                if start_ts <= item.published_at <= end_ts+1e-6
            }
            for spec in sorted(dedup.values(), key=lambda item: item.published_at):
                if self.store.has(spec):
                    skipped += 1
                    continue
                try:
                    fetched_at, html, payload = self.source.archive(spec)
                    stored.append(self.store.ingest(
                        spec, html=html, payload=payload, fetched_at=fetched_at))
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    errors[f"{spec.family}:{spec.period}"] = (
                        f"{type(exc).__name__}:{str(exc)[:180]}")
            result = {
                "status": "PARTIAL" if errors else "OK",
                "observation_span": {"first_t0": span[0], "latest_t0": span[1]},
                "bootstrap_window": {"start_ts": start_ts, "end_ts": end_ts},
                "calendar_years": years,
                "candidate_release_n": len(dedup),
                "stored": stored, "skipped": skipped, "errors": errors,
                "source_kind": "OFFICIAL_BLS_ARCHIVED_RELEASE_COPY",
                "current_revised_series_backfill": False,
                "old_t0_rows_mutated": False,
                "research_only": True, "production_authority": False,
            }
            self.last_result = result
            self.last_error = _json(errors) if errors else None
            return result
        finally:
            self.last_finished_at = time.time()
            with self._lock:
                self.running = False

    def _run(self) -> None:
        if self._stop.wait(self.startup_delay_sec):
            return
        while not self._stop.is_set():
            self.refresh()
            self._wake.wait(self.poll_sec); self._wake.clear()

    def status(self) -> dict[str, Any]:
        span = observation_span(self.store.runtime)
        return {
            "contract_version": BLS_HISTORICAL_BOOTSTRAP_VERSION,
            "running": self.running,
            "poll_sec": self.poll_sec,
            "startup_delay_sec": self.startup_delay_sec,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "observation_span": (
                {"first_t0": span[0], "latest_t0": span[1]} if span else None),
            "store": self.store.status(),
            "official_source_only": True,
            "release_timestamp_source": "BLS_RELEASE_CALENDAR",
            "value_source": "BLS_ARCHIVED_NEWS_RELEASE",
            "current_revised_series_backfill": False,
            "old_t0_rows_mutated": False,
            "research_only": True,
            "production_authority": False,
        }
