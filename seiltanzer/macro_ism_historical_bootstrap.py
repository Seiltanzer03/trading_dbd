"""Historical ISM PMI reconstruction from official point-in-time pages.

Scope is deliberately narrow: this module fills only the four already-canonical
EDE IDs for Manufacturing/Services headline PMI and month-over-month PMI change.
It does not promote every ISM subindex into the hypothesis space before the base
headline family has demonstrated useful prospective evidence.

Source/causality contract
-------------------------
ISM publishes the underlying Manufacturing and Services PMI reports at 10:00 ET.
Its dated same-day roundup and exact-period report pages reproduce released PMI
values. Historical values here therefore use:

* primary value source: dated official ``ismworld.org`` roundup page;
* bounded fallback: exact-period monthly report plus official release calendar;
* as-of: 10:00 America/New_York on the verified official publication date;
* delta: current PMI minus the immediately previous official release PMI;
* one family/month release ID = one EDE dependence unit;
* no consensus, market data, LLM, synthetic value, or current mutable report page.

These pages are official post-release reproductions, not byte-versioned copies
of first-release HTML. Provenance says that explicitly. Old T0 observation bytes
are never mutated; the store is an immutable research overlay.
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

from .macro_ism_resilience import _roundup_current_values
from .macro_numeric_data import _LinkAndTableParser, parse_ism_report


ISM_HISTORICAL_BOOTSTRAP_VERSION = "ism-official-point-in-time-v2-calendar-direct-fallback"
ISM_HOSTS = frozenset({"www.ismworld.org", "ismworld.org"})
ISM_MAGAZINE_BASE = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog"
)
ISM_DIRECT_REPORT_BASE = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/ism-pmi-reports"
)
ISM_RELEASE_CALENDAR_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/rob-report-calendar/"
)
FAMILIES = ("ISM_MANUFACTURING", "ISM_SERVICES")
FAMILY_SLUG = {
    "ISM_MANUFACTURING": "manufacturing",
    "ISM_SERVICES": "services",
}
FAMILY_LABEL = {
    "ISM_MANUFACTURING": "Manufacturing",
    "ISM_SERVICES": "Services",
}
FEATURE_IDS = {
    "ISM_MANUFACTURING": {
        "pmi": "macro.ism_manufacturing_pmi",
        "change_pp": "macro.ism_manufacturing_pmi_change_pp",
    },
    "ISM_SERVICES": {
        "pmi": "macro.ism_services_pmi",
        "change_pp": "macro.ism_services_pmi_change_pp",
    },
}
_MONTHS = {
    name.lower(): index for index, name in enumerate((
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ), start=1)
}
HISTORICAL_LOOKBACK_BUFFER_SEC = 100.0 * 86400.0
MAX_BOOTSTRAP_MONTHS = 36


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        clean = re.sub(r"\s+", " ", str(data)).strip()
        if clean:
            self.parts.append(clean)


@dataclass(frozen=True)
class ISMRoundupSpec:
    family: str
    period: str
    source_url: str


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _period(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def _period_parts(period: str) -> tuple[int, int]:
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", str(period))
    if not match:
        raise ValueError("ISM_HISTORICAL_PERIOD_INVALID")
    return int(match.group(1)), int(match.group(2))


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = int(year)*12 + int(month)-1 + int(delta)
    return index//12, index%12+1


def _previous_period(period: str) -> str:
    year, month = _period_parts(period)
    previous_year, previous_month = _shift_month(year, month, -1)
    return _period(previous_year, previous_month)


def _month_name(month: int) -> str:
    return datetime(2000, int(month), 1).strftime("%B")


def _month_slug(month: int) -> str:
    return _month_name(month).lower()


def _official_ism_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ISM_HOSTS


def _candidate_roundup_urls(family: str, period: str) -> tuple[str, ...]:
    year, month = _period_parts(period)
    release_year, release_month = _shift_month(year, month, 1)
    month_slug = _month_slug(month)
    family_slug = FAMILY_SLUG[family]
    directory = f"{ISM_MAGAZINE_BASE}/{release_year}/{release_year}-{release_month:02d}"
    # ISM rebranded Report On Business to ISM PMI Reports in late 2025.  Both
    # dated official slug families are attempted and then content-validated.
    slugs = (
        f"ism-pmi-reports-roundup-{month_slug}-{year}-{family_slug}",
        f"report-on-business-roundup-{month_slug}-{year}-{family_slug}-pmi",
        f"report-on-business-roundup-{month_slug}-{family_slug}-pmi",
    )
    return tuple(f"{directory}/{slug}/" for slug in slugs)


def _direct_report_url(family: str, period: str) -> str:
    _, month = _period_parts(period)
    section = "pmi" if family == "ISM_MANUFACTURING" else "services"
    return f"{ISM_DIRECT_REPORT_BASE}/{section}/{_month_slug(month)}/"


def _visible_text(html: str) -> str:
    parser = _VisibleTextParser(); parser.feed(html or "")
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def parse_ism_release_calendar(
    html: str, *, year: int, source_url: str = ISM_RELEASE_CALENDAR_URL,
) -> dict[tuple[str, str], float]:
    """Parse exact official release days into report-period 10:00 ET timestamps."""
    if source_url != ISM_RELEASE_CALENDAR_URL or not _official_ism_url(source_url):
        raise ValueError("ISM_RELEASE_CALENDAR_UNOFFICIAL_SOURCE")
    target_year = int(year)
    parser = _LinkAndTableParser()
    parser.feed(html or "")
    text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    if (
        "Release Dates for the ISM" not in text
        or f"{target_year} ISM" not in text
    ):
        raise ValueError("ISM_RELEASE_CALENDAR_TITLE_YEAR_MISMATCH")

    month_column: int | None = None
    family_columns: dict[str, int] = {}
    for row in parser.rows:
        tokens_by_column = [
            set(re.findall(r"[a-z]+", str(value).lower())) for value in row
        ]
        month_columns = [
            index for index, tokens in enumerate(tokens_by_column)
            if "month" in tokens
        ]
        if not month_columns:
            continue
        columns: dict[str, list[int]] = {family: [] for family in FAMILIES}
        for index, tokens in enumerate(tokens_by_column):
            if {"manufacturing", "pmi"} <= tokens:
                columns["ISM_MANUFACTURING"].append(index)
            if {"services", "pmi"} <= tokens:
                columns["ISM_SERVICES"].append(index)
        if (
            len(month_columns) != 1
            or any(len(columns[family]) > 1 for family in FAMILIES)
        ):
            raise ValueError("ISM_RELEASE_CALENDAR_HEADERS_DUPLICATED")
        if all(len(columns[family]) == 1 for family in FAMILIES):
            indexes = {month_columns[0], *(columns[family][0] for family in FAMILIES)}
            if len(indexes) != 3:
                raise ValueError("ISM_RELEASE_CALENDAR_HEADERS_AMBIGUOUS")
            month_column = month_columns[0]
            family_columns = {
                family: columns[family][0] for family in FAMILIES
            }
            break
    if month_column is None or set(family_columns) != set(FAMILIES):
        raise ValueError("ISM_RELEASE_CALENDAR_HEADERS_MISSING")

    output: dict[tuple[str, str], float] = {}
    month_pattern = "|".join(name.title() for name in _MONTHS)
    for row in parser.rows:
        if len(row) <= max(month_column, *family_columns.values()):
            continue
        label = re.sub(r"\s+", " ", str(row[month_column])).strip()
        match = re.fullmatch(
            rf"({month_pattern})\s+(20\d{{2}})", label, flags=re.I
        )
        if not match or int(match.group(2)) != target_year:
            continue
        release_month = _MONTHS[match.group(1).lower()]
        report_year, report_month = _shift_month(target_year, release_month, -1)
        period = _period(report_year, report_month)
        for family, index in family_columns.items():
            day_match = re.search(r"\d{1,2}", str(row[index]))
            if not day_match:
                raise ValueError("ISM_RELEASE_CALENDAR_DAY_MISSING")
            day = int(day_match.group(0))
            try:
                released = datetime(
                    target_year, release_month, day, 10, 0, 0,
                    tzinfo=ZoneInfo("America/New_York"),
                )
            except ValueError as exc:
                raise ValueError("ISM_RELEASE_CALENDAR_DAY_INVALID") from exc
            key = (family, period)
            if key in output:
                raise ValueError("ISM_RELEASE_CALENDAR_PERIOD_DUPLICATED")
            output[key] = float(released.timestamp())
    if not output:
        raise ValueError("ISM_RELEASE_CALENDAR_ROWS_MISSING")
    return output


def _publication_date(text: str) -> tuple[int, int, int] | None:
    # Roundup pages put the article date near the title.  Take the first full
    # month/day/year date after title normalization and validate its release month.
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),\s+(20\d{2})\b",
        text, flags=re.I)
    if not match:
        return None
    return int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2))


def _title_matches(text: str, family: str, period: str) -> bool:
    year, month = _period_parts(period)
    month_name = _month_name(month)
    label = FAMILY_LABEL[family]
    patterns = (
        rf"ISM(?:®)?\s*PMI(?:®)?\s*Reports\s+Roundup:\s*{month_name}\s+{label}",
        rf"Report\s+On\s+Business(?:®)?\s+Roundup:\s*{month_name}\s+{label}\s+PMI(?:®)?",
    )
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def parse_ism_historical_roundup(html: str, *, family: str, period: str,
                                 source_url: str) -> dict[str, Any]:
    """Parse only the released headline PMI from one dated official roundup."""
    if family not in FAMILIES:
        raise ValueError("ISM_HISTORICAL_FAMILY_INVALID")
    if not _official_ism_url(source_url):
        raise ValueError("ISM_HISTORICAL_UNOFFICIAL_SOURCE")
    year, month = _period_parts(period)
    path = urlparse(source_url).path.lower()
    if str(year) not in path or _month_slug(month) not in path:
        raise ValueError("ISM_HISTORICAL_URL_PERIOD_MISMATCH")
    family_slug = FAMILY_SLUG[family]
    if family_slug not in path:
        raise ValueError("ISM_HISTORICAL_URL_FAMILY_MISMATCH")

    text = _visible_text(html)
    if not _title_matches(text, family, period):
        raise ValueError("ISM_HISTORICAL_TITLE_PERIOD_FAMILY_MISMATCH")
    publication = _publication_date(text)
    if publication is None:
        raise ValueError("ISM_HISTORICAL_PUBLICATION_DATE_MISSING")
    release_year, release_month = _shift_month(year, month, 1)
    if publication[:2] != (release_year, release_month):
        raise ValueError("ISM_HISTORICAL_PUBLICATION_MONTH_MISMATCH")

    values = _roundup_current_values(text, family)
    pmi = _finite(values.get("pmi"))
    if pmi is None or not (0.0 <= pmi <= 100.0):
        raise ValueError("ISM_HISTORICAL_PMI_MISSING")
    local = datetime(
        publication[0], publication[1], publication[2], 10, 0, 0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    return {
        "family": family,
        "period": period,
        "pmi": pmi,
        "published_at": float(local.timestamp()),
        "publication_date": f"{publication[0]:04d}-{publication[1]:02d}-{publication[2]:02d}",
        "source_url": source_url,
        "release_time_rule": "10:00_America/New_York",
        "source_kind": "OFFICIAL_DATED_ROUNDUP_POST_RELEASE_REPRODUCTION",
        "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_FIRST_REPORT_HTML",
        "consensus_available": False,
        "surprise_computed": False,
        "llm_used": False,
    }


def parse_ism_historical_direct_report(
    html: str, *, family: str, period: str, source_url: str,
    calendar_html: str, calendar_source_url: str = ISM_RELEASE_CALENDAR_URL,
) -> dict[str, Any]:
    """Parse a still-public official monthly report with its official calendar."""
    if family not in FAMILIES:
        raise ValueError("ISM_HISTORICAL_FAMILY_INVALID")
    if not _official_ism_url(source_url):
        raise ValueError("ISM_HISTORICAL_UNOFFICIAL_SOURCE")
    expected_url = _direct_report_url(family, period)
    actual = urlparse(source_url)
    expected = urlparse(expected_url)
    if actual.path.rstrip("/") != expected.path.rstrip("/"):
        raise ValueError("ISM_HISTORICAL_DIRECT_URL_PERIOD_FAMILY_MISMATCH")

    parsed = parse_ism_report(html, family, source_url)
    if parsed.get("period") != period:
        raise ValueError("ISM_HISTORICAL_DIRECT_CONTENT_PERIOD_MISMATCH")
    year, month = _period_parts(period)
    release_year, _ = _shift_month(year, month, 1)
    calendar = parse_ism_release_calendar(
        calendar_html, year=release_year, source_url=calendar_source_url,
    )
    published_at = calendar.get((family, period))
    if published_at is None:
        raise ValueError("ISM_HISTORICAL_DIRECT_RELEASE_DATE_MISSING")
    pmi = _finite(((parsed.get("metrics") or {}).get("pmi") or {}).get("current"))
    if pmi is None or not (0.0 <= pmi <= 100.0):
        raise ValueError("ISM_HISTORICAL_PMI_MISSING")
    published = datetime.fromtimestamp(
        published_at, tz=ZoneInfo("America/New_York")
    )
    return {
        "family": family,
        "period": period,
        "pmi": pmi,
        "published_at": float(published_at),
        "publication_date": published.strftime("%Y-%m-%d"),
        "source_url": source_url,
        "release_time_rule": "10:00_America/New_York",
        "source_kind": "OFFICIAL_MONTHLY_REPORT_WITH_OFFICIAL_RELEASE_CALENDAR",
        "source_vintage_guarantee": "OFFICIAL_MONTH_URL_EXACT_PERIOD_NOT_FIRST_BYTE_VERSION",
        "release_calendar_source_url": calendar_source_url,
        "release_calendar_source_sha256": _sha(calendar_html),
        "consensus_available": False,
        "surprise_computed": False,
        "llm_used": False,
    }


class ISMHistoricalReleaseStore:
    """Append-only historical headline PMI store with strict predecessor deltas."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._conn = runtime._conn
        self._lock = runtime._lock
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_ism_historical_releases(
                    release_id TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    period TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    published_at REAL NOT NULL,
                    fetched_at REAL NOT NULL,
                    pmi REAL NOT NULL,
                    change_pp REAL,
                    previous_release_id TEXT,
                    source_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    UNIQUE(family,period,source_sha256)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_macro_ism_hist_causal "
                "ON macro_ism_historical_releases(family,published_at,period)")
            for action in ("UPDATE", "DELETE"):
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS macro_ism_historical_releases_immutable_{action.lower()}
                    BEFORE {action} ON macro_ism_historical_releases
                    BEGIN SELECT RAISE(ABORT,'immutable ISM historical release'); END""")

    def _by_period(self, family: str, period: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT release_id,pmi,published_at,source_url FROM macro_ism_historical_releases "
                "WHERE family=? AND period=? ORDER BY created_ts ASC LIMIT 1",
                (family, period),
            ).fetchone()
        if row is None:
            return None
        return {
            "release_id": str(row[0]), "pmi": float(row[1]),
            "published_at": float(row[2]), "source_url": str(row[3]),
        }

    def has(self, family: str, period: str) -> bool:
        return self._by_period(family, period) is not None

    def ingest(self, parsed: dict[str, Any], *, html: str,
               fetched_at: float | None = None,
               require_previous: bool = True) -> dict[str, Any]:
        family = str(parsed.get("family") or "")
        period = str(parsed.get("period") or "")
        if family not in FAMILIES:
            raise ValueError("ISM_HISTORICAL_FAMILY_INVALID")
        _period_parts(period)
        source_url = str(parsed.get("source_url") or "")
        if not _official_ism_url(source_url):
            raise ValueError("ISM_HISTORICAL_UNOFFICIAL_SOURCE")
        pmi = _finite(parsed.get("pmi"))
        published_at = _finite(parsed.get("published_at"))
        if pmi is None or not (0.0 <= pmi <= 100.0) or published_at is None:
            raise ValueError("ISM_HISTORICAL_PAYLOAD_INVALID")

        previous_period = _previous_period(period)
        previous = self._by_period(family, previous_period)
        if require_previous and previous is None:
            raise ValueError("ISM_HISTORICAL_PREVIOUS_RELEASE_MISSING")
        if previous is not None and previous["published_at"] >= published_at-1e-6:
            raise ValueError("ISM_HISTORICAL_RELEASE_ORDER_INVALID")
        change_pp = pmi-previous["pmi"] if previous is not None else None
        fetched = float(time.time() if fetched_at is None else fetched_at)
        if not math.isfinite(fetched) or fetched+300.0 < published_at:
            raise ValueError("ISM_HISTORICAL_FETCH_TIME_INVALID")
        source_sha = _sha((html or "").encode("utf-8"))
        release_id = "macro-ism-hist-" + _sha(
            f"{family}|{period}|{published_at:.6f}|{source_sha}")[:28]
        payload = {
            **parsed,
            "previous_period": previous_period,
            "previous_release_id": (previous or {}).get("release_id"),
            "previous_pmi": (previous or {}).get("pmi"),
            "change_pp": change_pp,
            "old_t0_rows_mutated": False,
        }
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT release_id FROM macro_ism_historical_releases "
                "WHERE family=? AND period=? AND source_sha256=? LIMIT 1",
                (family, period, source_sha),
            ).fetchone()
            if existing:
                return {
                    "status": "CACHED", "release_id": str(existing[0]),
                    "family": family, "period": period, "published_at": published_at,
                }
            self._conn.execute(
                "INSERT INTO macro_ism_historical_releases("
                "release_id,family,period,source_url,published_at,fetched_at,pmi,change_pp,"
                "previous_release_id,source_sha256,payload_json,contract_version,created_ts"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (release_id, family, period, source_url, published_at, fetched, pmi,
                 change_pp, (previous or {}).get("release_id"), source_sha,
                 _json(payload), ISM_HISTORICAL_BOOTSTRAP_VERSION, time.time()),
            )
        return {
            "status": "STORED", "release_id": release_id,
            "family": family, "period": period, "published_at": published_at,
            "change_pp": change_pp,
        }

    def latest_admissible(self, family: str, captured_ts: float) -> dict[str, Any]:
        return _latest_release(self.runtime, family, captured_ts)

    def status(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT family,COUNT(*),MIN(published_at),MAX(published_at) "
                "FROM macro_ism_historical_releases GROUP BY family").fetchall()
        materialized = {
            str(row[0]): {
                "row_n": int(row[1]),
                "first_published_at": float(row[2]),
                "latest_published_at": float(row[3]),
            } for row in rows
        }
        return {
            "contract_version": ISM_HISTORICAL_BOOTSTRAP_VERSION,
            "families": {family: materialized.get(family, {
                "row_n": 0, "first_published_at": None, "latest_published_at": None,
            }) for family in FAMILIES},
            "feature_ids": [
                feature_id for mapping in FEATURE_IDS.values()
                for feature_id in mapping.values()
            ],
            "causal_rule": "published_at<=T0",
            "release_time_rule": "10:00_America/New_York_on_official_release_date",
            "source_kind": "OFFICIAL_ISM_POINT_IN_TIME_SOURCE",
            "source_vintage_guarantee": "OFFICIAL_SOURCE_EXACT_PERIOD_NOT_FIRST_BYTE_VERSION",
            "current_mutable_report_backfill": False,
            "old_t0_rows_mutated": False,
            "research_only": True, "production_authority": False,
        }


def _table_exists(runtime: Any) -> bool:
    try:
        with runtime._lock:
            row = runtime._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='macro_ism_historical_releases'").fetchone()
        return row is not None
    except Exception:
        return False


def _latest_release(runtime: Any, family: str, captured_ts: float) -> dict[str, Any]:
    if family not in FAMILIES or not _table_exists(runtime):
        return {"status": "UNAVAILABLE", "family": family,
                "reason": "NO_HISTORICAL_ISM_TABLE"}
    cutoff = _finite(captured_ts)
    if cutoff is None:
        return {"status": "UNAVAILABLE", "family": family, "reason": "INVALID_T0"}
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT release_id,period,source_url,published_at,fetched_at,pmi,change_pp,"
            "previous_release_id,source_sha256,payload_json,contract_version "
            "FROM macro_ism_historical_releases WHERE family=? AND published_at<=? "
            "ORDER BY published_at DESC,created_ts DESC LIMIT 1",
            (family, float(cutoff)),
        ).fetchone()
    if row is None:
        return {"status": "UNAVAILABLE", "family": family,
                "reason": "NO_ISM_ROUNDUP_BEFORE_T0"}
    payload = json.loads(str(row[9]))
    return {
        "status": "VALID", "family": family,
        "release_id": str(row[0]), "period": str(row[1]),
        "source": "Institute for Supply Management",
        "source_url": str(row[2]), "published_at": float(row[3]),
        "available_at": float(row[3]), "fetched_at": float(row[4]),
        "pmi": float(row[5]),
        "change_pp": float(row[6]) if row[6] is not None else None,
        "previous_release_id": row[7], "source_sha256": str(row[8]),
        "payload": payload, "contract_version": str(row[10]),
        "official_source_verified": True,
        "historical_reconstruction": True,
        "source_kind": payload.get(
            "source_kind", "OFFICIAL_DATED_ROUNDUP_POST_RELEASE_REPRODUCTION"
        ),
        "source_vintage_guarantee": payload.get(
            "source_vintage_guarantee", "OFFICIAL_DATED_PAGE_NOT_FIRST_REPORT_HTML"
        ),
        "causal_rule": "published_at<=T0",
        "research_only": True, "production_authority": False,
    }


def feature_records_from_runtime(runtime: Any, *, instrument: str,
                                 t0: float, horizon: int) -> tuple[
                                     dict[str, Any], dict[str, dict[str, Any]]]:
    from .edge_discovery.feature_view import feature_value

    values: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        release = _latest_release(runtime, family, t0)
        if release.get("status") != "VALID":
            continue
        mapping = FEATURE_IDS[family]
        facts = {"pmi": release.get("pmi"), "change_pp": release.get("change_pp")}
        for source_name, feature_id in mapping.items():
            value = _finite(facts.get(source_name))
            if value is None:
                continue
            record = feature_value(
                instrument=instrument, t0=t0, horizon=horizon,
                feature_id=feature_id, value=value,
                asof=float(release["published_at"]),
                historical_available=True, live_available=True,
                training_eligible=True, dependency_group=f"macro_release:{family}",
            )
            if not record.training_eligible:
                continue
            values[feature_id] = record
            source_kind = str(release["source_kind"])
            point_in_time_kind = (
                "OFFICIAL_ISM_DATED_ROUNDUP_POINT_IN_TIME"
                if source_kind == "OFFICIAL_DATED_ROUNDUP_POST_RELEASE_REPRODUCTION"
                else "OFFICIAL_ISM_DIRECT_REPORT_WITH_RELEASE_CALENDAR_POINT_IN_TIME"
            )
            feature_provenance = {
                "provenance": point_in_time_kind,
                "release_id": release["release_id"],
                "release_family": family,
                "release_period": release["period"],
                "published_at": release["published_at"],
                "available_at": release["published_at"],
                "source_url": release["source_url"],
                "source_sha256": release["source_sha256"],
                "previous_release_id": release.get("previous_release_id"),
                "official_source_verified": True,
                "historical_reconstruction": True,
                "source_kind": source_kind,
                "source_vintage_guarantee": release["source_vintage_guarantee"],
                "current_mutable_report_backfill": False,
                "old_t0_row_mutated": False,
                "future_points_used": False,
                "llm_used": False,
            }
            payload = release.get("payload") or {}
            if payload.get("release_calendar_source_url"):
                feature_provenance["release_calendar_source_url"] = str(
                    payload["release_calendar_source_url"]
                )
                feature_provenance["release_calendar_source_sha256"] = str(
                    payload["release_calendar_source_sha256"]
                )
            provenance[feature_id] = feature_provenance
    return values, provenance


class OfficialISMHistoricalSource:
    def __init__(self, *, timeout_sec: float = 12.0) -> None:
        self.timeout_sec = max(4.0, min(30.0, float(timeout_sec)))
        self._calendar_cache: dict[int, tuple[str, str]] = {}

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
    def _validated(response: httpx.Response) -> str:
        response.raise_for_status()
        if not _official_ism_url(str(response.url)):
            raise ValueError("ISM_HISTORICAL_REDIRECT_REJECTED")
        if len(response.content) < 200:
            raise ValueError("ISM_HISTORICAL_RESPONSE_TOO_SHORT")
        if len(response.content) > 2_000_000:
            raise ValueError("ISM_HISTORICAL_RESPONSE_TOO_LARGE")
        return response.text

    def _release_calendar(
        self, client: httpx.Client, *, year: int,
    ) -> tuple[str, str]:
        cached = self._calendar_cache.get(int(year))
        if cached is not None:
            return cached
        response = client.get(ISM_RELEASE_CALENDAR_URL)
        html = self._validated(response)
        final_url = str(response.url)
        if final_url != ISM_RELEASE_CALENDAR_URL:
            raise ValueError("ISM_RELEASE_CALENDAR_REDIRECT_REJECTED")
        parse_ism_release_calendar(html, year=int(year), source_url=final_url)
        cached = (html, final_url)
        self._calendar_cache[int(year)] = cached
        return cached

    def fetch_with_evidence(
        self, family: str, period: str,
    ) -> tuple[float, str, dict[str, Any], tuple[dict[str, Any], ...]]:
        failures: list[str] = []
        with self._client() as client:
            for url in _candidate_roundup_urls(family, period):
                try:
                    response = client.get(url)
                    html = self._validated(response)
                    parsed = parse_ism_historical_roundup(
                        html, family=family, period=period,
                        source_url=str(response.url))
                    return time.time(), html, parsed, ()
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    failures.append(f"{type(exc).__name__}:{str(exc)[:120]}")

            direct_url = _direct_report_url(family, period)
            try:
                response = client.get(direct_url)
                html = self._validated(response)
                year, month = _period_parts(period)
                release_year, _ = _shift_month(year, month, 1)
                calendar_html, calendar_url = self._release_calendar(
                    client, year=release_year,
                )
                parsed = parse_ism_historical_direct_report(
                    html,
                    family=family,
                    period=period,
                    source_url=str(response.url),
                    calendar_html=calendar_html,
                    calendar_source_url=calendar_url,
                )
                return time.time(), html, parsed, ({
                    "format": "HTML_ISM_RELEASE_CALENDAR",
                    "year": release_year,
                    "source_url": calendar_url,
                    "content": calendar_html,
                },)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                failures.append(
                    f"DIRECT:{type(exc).__name__}:{str(exc)[:120]}"
                )
        raise RuntimeError(
            "ISM_HISTORICAL_OFFICIAL_SOURCE_UNAVAILABLE:"
            + "|".join(failures[-4:])
        )

    def fetch(self, family: str, period: str) -> tuple[float, str, dict[str, Any]]:
        fetched_at, html, parsed, _ = self.fetch_with_evidence(family, period)
        return fetched_at, html, parsed


def _observation_span(runtime: Any) -> tuple[float, float] | None:
    from .macro_bls_historical_bootstrap import observation_span
    return observation_span(runtime)


def _periods_for_window(start_ts: float, end_ts: float) -> list[str]:
    start = datetime.fromtimestamp(start_ts, tz=ZoneInfo("America/New_York"))
    end = datetime.fromtimestamp(end_ts, tz=ZoneInfo("America/New_York"))
    # A release in month M describes month M-1. Start two report months earlier
    # so the first in-window report can obtain an exact predecessor for change_pp.
    start_year, start_month = _shift_month(start.year, start.month, -2)
    end_year, end_month = _shift_month(end.year, end.month, -1)
    output: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        output.append(_period(year, month))
        year, month = _shift_month(year, month, 1)
        if len(output) > MAX_BOOTSTRAP_MONTHS:
            raise RuntimeError("ISM_HISTORICAL_WINDOW_TOO_WIDE")
    return output


class ISMHistoricalBootstrapRuntime:
    """Daily dated-roundup materializer; never fetches inside an EDE row read."""

    def __init__(self, store: ISMHistoricalReleaseStore, *, poll_sec: float = 86400.0,
                 startup_delay_sec: float = 210.0) -> None:
        self.store = store
        self.source = OfficialISMHistoricalSource()
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
            target=self._run, name="ism-historical-bootstrap", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def refresh(self, *, now: float | None = None) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "IN_PROGRESS", "research_only": True}
            self.running = True
        self.last_started_at = time.time()
        stored: list[dict[str, Any]] = []
        skipped = 0
        errors: dict[str, str] = {}
        try:
            span = _observation_span(self.store.runtime)
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
            periods = _periods_for_window(start_ts, end_ts)
            for family in FAMILIES:
                first_for_family = True
                for period in periods:
                    if self.store.has(family, period):
                        skipped += 1
                        first_for_family = False
                        continue
                    try:
                        fetched_at, html, parsed = self.source.fetch(family, period)
                        stored.append(self.store.ingest(
                            parsed, html=html, fetched_at=fetched_at,
                            require_previous=not first_for_family))
                        first_for_family = False
                    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                        errors[f"{family}:{period}"] = (
                            f"{type(exc).__name__}:{str(exc)[:180]}")
                        # Do not advance the predecessor chain after failure. A
                        # later period must not freeze a delta across a missing month.
                        first_for_family = False
            result = {
                "status": "PARTIAL" if errors else "OK",
                "observation_span": {"first_t0": span[0], "latest_t0": span[1]},
                "bootstrap_window": {"start_ts": start_ts, "end_ts": end_ts},
                "periods": periods, "stored": stored, "skipped": skipped,
                "errors": errors,
                "source_kind": "OFFICIAL_ISM_POINT_IN_TIME_SOURCE",
                "release_time_rule": "10:00_America/New_York_on_official_release_date",
                "current_mutable_report_backfill": False,
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
        span = _observation_span(self.store.runtime)
        return {
            "contract_version": ISM_HISTORICAL_BOOTSTRAP_VERSION,
            "running": self.running, "poll_sec": self.poll_sec,
            "startup_delay_sec": self.startup_delay_sec,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_error": self.last_error, "last_result": self.last_result,
            "observation_span": (
                {"first_t0": span[0], "latest_t0": span[1]} if span else None),
            "store": self.store.status(),
            "official_source_only": True,
            "source_kind": "OFFICIAL_ISM_POINT_IN_TIME_SOURCE",
            "release_time_rule": "10:00_America/New_York_on_official_release_date",
            "current_mutable_report_backfill": False,
            "old_t0_rows_mutated": False,
            "research_only": True, "production_authority": False,
        }
