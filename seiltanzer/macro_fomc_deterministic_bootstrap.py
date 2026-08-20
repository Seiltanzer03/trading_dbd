"""Deterministic, point-in-time FOMC statement features for EDE research.

Historical FOMC *LLM semantic* scores are intentionally not reconstructed here.
Doing that later with a model that may know subsequent history would create a
hard-to-audit leakage channel.  Instead this module derives only measurements
that are mechanically present in the official dated Federal Reserve statement:

- target-rate midpoint and range width;
- rate change versus the immediately previous official statement;
- dissent share from the published vote;
- deterministic statement text change versus the previous statement.

The same parser/store is usable for future captures, so historical and live
values share one exact feature definition.  Historical rows are read overlays;
old ``g1s_observations`` are never mutated.

Causality / source contract:
- HTTPS ``federalreserve.gov`` only;
- statement date comes from its official dated URL/index;
- release *time* is parsed from the statement's own ``For release at`` line;
- a feature is visible only when ``published_at <= T0``;
- one statement is one EDE dependence unit regardless of repeated market T0s;
- no LLM, consensus, market data, future document, or synthetic value is used.

The Federal Reserve exposes dated historical statement pages rather than a
versioned revision feed.  Therefore old pages are labelled historical
reconstructions from the official dated page, not cryptographically proven
first-published HTML vintages.  This distinction stays explicit in provenance.
"""
from __future__ import annotations

import difflib
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

from .fomc_official_source import discover_statement_urls, extract_statement_text


FOMC_DETERMINISTIC_CONTRACT_VERSION = "fomc-deterministic-point-in-time-v1"
FED_BASE = "https://www.federalreserve.gov"
FED_HOSTS = frozenset({"www.federalreserve.gov", "federalreserve.gov"})
INDEX_TEMPLATE = FED_BASE + "/newsevents/pressreleases/{year}-press-fomc.htm"
FOMC_FAMILY = "FOMC_STATEMENT"
HISTORICAL_LOOKBACK_BUFFER_SEC = 180.0 * 86400.0
MAX_BOOTSTRAP_CALENDAR_YEARS = 6

FOMC_DETERMINISTIC_FEATURES: dict[str, str] = {
    "target_mid_pct": "macro.fomc_target_mid_pct",
    "target_width_bp": "macro.fomc_target_width_bp",
    "target_change_bp": "macro.fomc_target_change_bp",
    "dissent_share": "macro.fomc_dissent_share",
    "statement_change": "macro.fomc_statement_change",
}

_DASH_TRANSLATION = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
})
_TARGET_RANGE_RE = re.compile(
    r"target\s+range\s+for\s+the\s+federal\s+funds\s+rate"
    r"(?:\s+(?:at|to|of))?\s+"
    r"([0-9]+(?:\s*[- ]\s*[0-9]+/[0-9]+|\.[0-9]+)?|[0-9]+/[0-9]+)"
    r"\s+to\s+"
    r"([0-9]+(?:\s*[- ]\s*[0-9]+/[0-9]+|\.[0-9]+)?|[0-9]+/[0-9]+)"
    r"\s+percent",
    re.I,
)
_RELEASE_TIME_RE = re.compile(
    r"For\s+release\s+at\s+(\d{1,2}:\d{2})\s*"
    r"([ap])\.?m\.?\s*(EST|EDT)",
    re.I,
)
_EXPLICIT_VOTE_RE = re.compile(
    r"(?:approved|approval).*?\bby\s+(?:a\s+)?(\d+)\s*[-–—]\s*(\d+)\s+vote",
    re.I | re.S,
)
_VOTING_FOR_RE = re.compile(
    r"Voting\s+for\s+the\s+monetary\s+policy\s+action\s+(?:were|was)\s+(.+?)"
    r"(?=\s+Voting\s+against|\s+Absent\s+and\s+not\s+voting|$)",
    re.I | re.S,
)
_VOTING_AGAINST_RE = re.compile(
    r"Voting\s+against(?:\s+this)?\s+(?:action|monetary\s+policy\s+action)\s+"
    r"(?:were|was)\s+(.+?)"
    r"(?=\s+Absent\s+and\s+not\s+voting|$)",
    re.I | re.S,
)


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
class FOMCStatementSpec:
    date_code: str
    source_url: str

    @property
    def approximate_published_at(self) -> float:
        # Candidate-window selection only.  Persisted causality always uses the
        # exact statement-page ``For release at`` time parsed by archive().
        local = datetime.strptime(self.date_code, "%Y%m%d").replace(
            hour=14, minute=0, second=0, microsecond=0,
            tzinfo=ZoneInfo("America/New_York"),
        )
        return float(local.timestamp())


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


def _official_fed_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in FED_HOSTS


def parse_fomc_index(index_html: str) -> list[FOMCStatementSpec]:
    return [
        FOMCStatementSpec(date_code=date_code, source_url=url)
        for date_code, url in reversed(discover_statement_urls(index_html or ""))
    ]


def parse_release_timestamp(page_html: str, *, date_code: str) -> float:
    parser = _VisibleTextParser(); parser.feed(page_html or "")
    text = " ".join(parser.parts)
    match = _RELEASE_TIME_RE.search(text)
    if not match:
        raise ValueError("FOMC_RELEASE_TIME_NOT_FOUND")
    clock = datetime.strptime(
        f"{match.group(1)} {match.group(2).upper()}M", "%I:%M %p")
    local = datetime.strptime(date_code, "%Y%m%d").replace(
        hour=clock.hour, minute=clock.minute, second=0, microsecond=0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    stated_zone = match.group(3).upper()
    actual_zone = str(local.tzname() or "").upper()
    if stated_zone != actual_zone:
        raise ValueError("FOMC_RELEASE_TIMEZONE_MISMATCH")
    return float(local.timestamp())


def _rate_number(raw: str) -> float:
    text = re.sub(r"\s+", " ", str(raw).translate(_DASH_TRANSLATION)).strip()
    mixed = re.fullmatch(r"(\d+)\s*[- ]\s*(\d+)/(\d+)", text)
    if mixed:
        denominator = int(mixed.group(3))
        if denominator == 0:
            raise ValueError("FOMC_RATE_DENOMINATOR_ZERO")
        return float(int(mixed.group(1)) + int(mixed.group(2))/denominator)
    fraction = re.fullmatch(r"(\d+)/(\d+)", text)
    if fraction:
        denominator = int(fraction.group(2))
        if denominator == 0:
            raise ValueError("FOMC_RATE_DENOMINATOR_ZERO")
        return float(int(fraction.group(1))/denominator)
    value = _finite(text)
    if value is None:
        raise ValueError("FOMC_RATE_PARSE_ERROR")
    return float(value)


def _target_range(body: str) -> tuple[float, float] | None:
    normalized = str(body).translate(_DASH_TRANSLATION)
    match = _TARGET_RANGE_RE.search(normalized)
    if not match:
        return None
    lower, upper = _rate_number(match.group(1)), _rate_number(match.group(2))
    if lower < 0.0 or upper < lower or upper > 30.0:
        raise ValueError("FOMC_TARGET_RANGE_INVALID")
    return float(lower), float(upper)


def _count_voters(segment: str) -> int:
    clean = re.sub(r"\s+", " ", segment).strip().strip(". ")
    if not clean:
        return 0
    # Official statements delimit voters with semicolons.  A final leading
    # "and" remains part of the last segment and does not alter the count.
    return len([part for part in clean.split(";") if part.strip()])


def _vote_counts(body: str) -> tuple[int, int] | None:
    normalized = str(body).translate(_DASH_TRANSLATION)
    explicit = _EXPLICIT_VOTE_RE.search(normalized)
    if explicit:
        votes_for, votes_against = int(explicit.group(1)), int(explicit.group(2))
        if votes_for + votes_against > 0:
            return votes_for, votes_against
    if re.search(r"\bunanimous(?:ly)?\b", normalized, flags=re.I):
        # We know the dissent share exactly even when total membership is not
        # parsed from the sentence.
        return 1, 0
    voting_for = _VOTING_FOR_RE.search(normalized)
    if not voting_for:
        return None
    votes_for = _count_voters(voting_for.group(1))
    voting_against = _VOTING_AGAINST_RE.search(normalized)
    votes_against = _count_voters(voting_against.group(1)) if voting_against else 0
    if votes_for <= 0:
        return None
    return votes_for, votes_against


def deterministic_statement_payload(body: str, *,
                                    previous_body: str | None = None) -> dict[str, Any]:
    """Derive only mechanically observable statement measurements."""
    target = _target_range(body)
    previous_target = _target_range(previous_body) if previous_body else None
    target_mid = ((target[0]+target[1])/2.0) if target else None
    target_width_bp = ((target[1]-target[0])*100.0) if target else None
    previous_mid = (
        (previous_target[0]+previous_target[1])/2.0 if previous_target else None)
    target_change_bp = (
        (target_mid-previous_mid)*100.0
        if target_mid is not None and previous_mid is not None else None)
    votes = _vote_counts(body)
    dissent_share = (
        votes[1]/(votes[0]+votes[1]) if votes and votes[0]+votes[1] > 0 else None)
    statement_change = None
    if previous_body:
        ratio = difflib.SequenceMatcher(
            None, str(previous_body), str(body), autojunk=False).ratio()
        statement_change = 1.0-float(ratio)
    payload = {
        "family": FOMC_FAMILY,
        "target_lower_pct": target[0] if target else None,
        "target_upper_pct": target[1] if target else None,
        "target_mid_pct": target_mid,
        "target_width_bp": target_width_bp,
        "target_change_bp": target_change_bp,
        "dissent_share": dissent_share,
        "statement_change": statement_change,
        "llm_used": False,
        "market_data_used": False,
        "future_document_used": False,
        "historical_reconstruction": True,
        "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
    }
    for key in (
        "target_lower_pct", "target_upper_pct", "target_mid_pct",
        "target_width_bp", "target_change_bp", "dissent_share", "statement_change",
    ):
        value = payload[key]
        if value is not None and _finite(value) is None:
            raise ValueError("FOMC_DETERMINISTIC_NONFINITE")
    if dissent_share is not None and not (0.0 <= dissent_share <= 1.0):
        raise ValueError("FOMC_DISSENT_SHARE_INVALID")
    if statement_change is not None and not (0.0 <= statement_change <= 1.0):
        raise ValueError("FOMC_STATEMENT_CHANGE_INVALID")
    return payload


class FOMCDeterministicReleaseStore:
    """Append-only official statement store shared by history and future T0 reads."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._conn = runtime._conn
        self._lock = runtime._lock
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_fomc_deterministic_releases(
                    release_id TEXT PRIMARY KEY,
                    date_code TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    published_at REAL NOT NULL,
                    fetched_at REAL NOT NULL,
                    body_text TEXT NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_release_id TEXT,
                    previous_source_url TEXT,
                    contract_version TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    UNIQUE(source_url,body_sha256)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_macro_fomc_det_causal "
                "ON macro_fomc_deterministic_releases(published_at,date_code)")
            for action in ("UPDATE", "DELETE"):
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS macro_fomc_deterministic_releases_immutable_{action.lower()}
                    BEFORE {action} ON macro_fomc_deterministic_releases
                    BEGIN SELECT RAISE(ABORT,'immutable deterministic FOMC release'); END""")

    def has_source_url(self, source_url: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM macro_fomc_deterministic_releases "
                "WHERE source_url=? LIMIT 1", (source_url,)).fetchone()
        return row is not None

    def _previous(self, source_url: str | None) -> dict[str, Any] | None:
        if not source_url:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT release_id,source_url,body_text,payload_json,published_at "
                "FROM macro_fomc_deterministic_releases WHERE source_url=? "
                "ORDER BY created_ts ASC LIMIT 1", (source_url,)).fetchone()
        if row is None:
            return None
        return {
            "release_id": str(row[0]), "source_url": str(row[1]),
            "body_text": str(row[2]), "payload": json.loads(str(row[3])),
            "published_at": float(row[4]),
        }

    def ingest(self, spec: FOMCStatementSpec, *, html: str,
               previous_source_url: str | None = None,
               fetched_at: float | None = None) -> dict[str, Any]:
        if not _official_fed_url(spec.source_url):
            raise ValueError("FOMC_DETERMINISTIC_UNOFFICIAL_SOURCE")
        published_at = parse_release_timestamp(html, date_code=spec.date_code)
        body = extract_statement_text(html)
        previous = self._previous(previous_source_url)
        if previous is not None and previous["published_at"] >= published_at-1e-6:
            raise ValueError("FOMC_PREVIOUS_RELEASE_ORDER_INVALID")
        payload = deterministic_statement_payload(
            body, previous_body=(previous or {}).get("body_text"))
        payload["previous_release_available"] = previous is not None
        payload["previous_release_id"] = (previous or {}).get("release_id")
        payload["previous_source_url"] = previous_source_url
        body_sha = _sha(body)
        release_id = "macro-fomc-det-" + _sha(
            f"{spec.date_code}|{spec.source_url}|{published_at:.6f}|{body_sha}")[:28]
        fetched = float(time.time() if fetched_at is None else fetched_at)
        if not math.isfinite(fetched) or fetched+300.0 < published_at:
            raise ValueError("FOMC_DETERMINISTIC_FETCH_TIME_INVALID")
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT release_id FROM macro_fomc_deterministic_releases "
                "WHERE source_url=? AND body_sha256=? LIMIT 1",
                (spec.source_url, body_sha),
            ).fetchone()
            if existing:
                return {
                    "status": "CACHED", "release_id": str(existing[0]),
                    "date_code": spec.date_code, "published_at": published_at,
                }
            self._conn.execute(
                "INSERT INTO macro_fomc_deterministic_releases("
                "release_id,date_code,source_url,published_at,fetched_at,body_text,"
                "body_sha256,payload_json,previous_release_id,previous_source_url,"
                "contract_version,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (release_id, spec.date_code, spec.source_url, published_at, fetched,
                 body, body_sha, _json(payload), (previous or {}).get("release_id"),
                 previous_source_url, FOMC_DETERMINISTIC_CONTRACT_VERSION, time.time()),
            )
        return {
            "status": "STORED", "release_id": release_id,
            "date_code": spec.date_code, "published_at": published_at,
            "previous_release_id": (previous or {}).get("release_id"),
        }

    def latest_admissible(self, captured_ts: float) -> dict[str, Any]:
        return _latest_release(self.runtime, captured_ts)

    def status(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*),MIN(published_at),MAX(published_at) "
                "FROM macro_fomc_deterministic_releases").fetchone()
        return {
            "contract_version": FOMC_DETERMINISTIC_CONTRACT_VERSION,
            "row_n": int(row[0] or 0),
            "first_published_at": float(row[1]) if row[1] is not None else None,
            "latest_published_at": float(row[2]) if row[2] is not None else None,
            "feature_ids": list(FOMC_DETERMINISTIC_FEATURES.values()),
            "llm_used": False,
            "causal_rule": "published_at<=T0",
            "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
            "old_t0_rows_mutated": False,
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


def _latest_release(runtime: Any, captured_ts: float) -> dict[str, Any]:
    if not _table_exists(runtime, "macro_fomc_deterministic_releases"):
        return {"status": "UNAVAILABLE", "reason": "NO_FOMC_DETERMINISTIC_TABLE"}
    cutoff = _finite(captured_ts)
    if cutoff is None:
        return {"status": "UNAVAILABLE", "reason": "INVALID_T0"}
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT release_id,date_code,source_url,published_at,fetched_at,body_sha256,"
            "payload_json,previous_release_id,previous_source_url,contract_version "
            "FROM macro_fomc_deterministic_releases WHERE published_at<=? "
            "ORDER BY published_at DESC,created_ts DESC LIMIT 1", (float(cutoff),)
        ).fetchone()
    if row is None:
        return {"status": "UNAVAILABLE", "reason": "NO_FOMC_STATEMENT_BEFORE_T0"}
    return {
        "status": "VALID", "family": FOMC_FAMILY,
        "release_id": str(row[0]), "date_code": str(row[1]),
        "source": "Federal Reserve Board", "source_url": str(row[2]),
        "published_at": float(row[3]), "available_at": float(row[3]),
        "fetched_at": float(row[4]), "body_sha256": str(row[5]),
        "payload": json.loads(str(row[6])),
        "previous_release_id": row[7], "previous_source_url": row[8],
        "contract_version": str(row[9]),
        "official_source_verified": True,
        "historical_reconstruction": True,
        "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
        "provenance": "OFFICIAL_FED_DATED_STATEMENT_DETERMINISTIC",
        "causal_rule": "published_at<=T0",
        "research_only": True, "production_authority": False,
    }


def feature_records_from_runtime(runtime: Any, *, instrument: str,
                                 t0: float, horizon: int) -> tuple[
                                     dict[str, Any], dict[str, dict[str, Any]]]:
    from .edge_discovery.feature_view import feature_value

    release = _latest_release(runtime, t0)
    if release.get("status") != "VALID":
        return {}, {}
    payload = release.get("payload") or {}
    values: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}
    asof = float(release["published_at"])
    for source_name, feature_id in FOMC_DETERMINISTIC_FEATURES.items():
        value = _finite(payload.get(source_name))
        if value is None:
            continue
        record = feature_value(
            instrument=instrument, t0=t0, horizon=horizon,
            feature_id=feature_id, value=value, asof=asof,
            historical_available=True, live_available=True,
            training_eligible=True,
            dependency_group="macro_release:FOMC_STATEMENT",
        )
        if not record.training_eligible:
            continue
        values[feature_id] = record
        provenance[feature_id] = {
            "provenance": "OFFICIAL_FED_DATED_STATEMENT_DETERMINISTIC",
            "release_id": release["release_id"],
            "release_family": FOMC_FAMILY,
            "release_period": release["date_code"],
            "published_at": asof, "available_at": asof,
            "source_url": release["source_url"],
            "body_sha256": release["body_sha256"],
            "previous_release_id": release.get("previous_release_id"),
            "official_source_verified": True,
            "historical_reconstruction": True,
            "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
            "llm_used": False, "future_points_used": False,
            "old_t0_row_mutated": False,
        }
    return values, provenance


class OfficialFOMCArchiveSource:
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
    def _validated(response: httpx.Response) -> str:
        response.raise_for_status()
        if not _official_fed_url(str(response.url)):
            raise ValueError("FOMC_ARCHIVE_REDIRECT_REJECTED")
        if len(response.content) < 200:
            raise ValueError("FOMC_ARCHIVE_RESPONSE_TOO_SHORT")
        if len(response.content) > 1_500_000:
            raise ValueError("FOMC_ARCHIVE_RESPONSE_TOO_LARGE")
        return response.text

    def schedule(self, year: int) -> list[FOMCStatementSpec]:
        url = INDEX_TEMPLATE.format(year=int(year))
        with self._client() as client:
            html = self._validated(client.get(url))
        return parse_fomc_index(html)

    def archive(self, spec: FOMCStatementSpec) -> tuple[float, str]:
        if not _official_fed_url(spec.source_url):
            raise ValueError("FOMC_ARCHIVE_UNOFFICIAL_URL")
        with self._client() as client:
            html = self._validated(client.get(spec.source_url))
        # Fail early if either the release timestamp or body cannot be extracted.
        parse_release_timestamp(html, date_code=spec.date_code)
        extract_statement_text(html)
        return time.time(), html


def _observation_span(runtime: Any) -> tuple[float, float] | None:
    from .macro_bls_historical_bootstrap import observation_span
    return observation_span(runtime)


class FOMCDeterministicBootstrapRuntime:
    """Hourly official-page materializer; no request-path network or LLM work."""

    def __init__(self, store: FOMCDeterministicReleaseStore, *,
                 poll_sec: float = 3600.0, startup_delay_sec: float = 150.0) -> None:
        self.store = store
        self.source = OfficialFOMCArchiveSource()
        self.poll_sec = max(900.0, float(poll_sec))
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
            target=self._run, name="fomc-deterministic-bootstrap", daemon=True)
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
            span = _observation_span(self.store.runtime)
            now_ts = time.time() if now is None else float(now)
            if span is None:
                start_ts = now_ts-HISTORICAL_LOOKBACK_BUFFER_SEC
                end_ts = now_ts
            else:
                start_ts = max(0.0, span[0]-HISTORICAL_LOOKBACK_BUFFER_SEC)
                end_ts = min(now_ts, max(span[1], now_ts))
            start_year = datetime.fromtimestamp(start_ts, tz=ZoneInfo("UTC")).year
            end_year = datetime.fromtimestamp(end_ts, tz=ZoneInfo("UTC")).year
            years = list(range(start_year, end_year+1))
            if len(years) > MAX_BOOTSTRAP_CALENDAR_YEARS:
                raise RuntimeError("FOMC_OBSERVATION_SPAN_TOO_WIDE")
            specs: list[FOMCStatementSpec] = []
            for year in years:
                try:
                    specs.extend(self.source.schedule(year))
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    errors[f"schedule:{year}"] = f"{type(exc).__name__}:{str(exc)[:180]}"
            dedup = {
                spec.source_url: spec for spec in specs
                if start_ts <= spec.approximate_published_at <= end_ts+86400.0
            }
            ordered = sorted(dedup.values(), key=lambda item: item.date_code)
            previous_spec: FOMCStatementSpec | None = None
            for spec in ordered:
                previous_url = previous_spec.source_url if previous_spec else None
                if self.store.has_source_url(spec.source_url):
                    skipped += 1
                    previous_spec = spec
                    continue
                try:
                    fetched_at, html = self.source.archive(spec)
                    stored.append(self.store.ingest(
                        spec, html=html, previous_source_url=previous_url,
                        fetched_at=fetched_at))
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    errors[spec.date_code] = f"{type(exc).__name__}:{str(exc)[:180]}"
                previous_spec = spec
            result = {
                "status": "PARTIAL" if errors else "OK",
                "observation_span": (
                    {"first_t0": span[0], "latest_t0": span[1]} if span else None),
                "bootstrap_window": {"start_ts": start_ts, "end_ts": end_ts},
                "calendar_years": years,
                "candidate_release_n": len(ordered),
                "stored": stored, "skipped": skipped, "errors": errors,
                "feature_ids": list(FOMC_DETERMINISTIC_FEATURES.values()),
                "llm_used": False,
                "source_kind": "OFFICIAL_FED_DATED_STATEMENT_PAGE",
                "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
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
            "contract_version": FOMC_DETERMINISTIC_CONTRACT_VERSION,
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
            "release_timestamp_source": "STATEMENT_FOR_RELEASE_AT_LINE",
            "llm_used": False,
            "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
            "old_t0_rows_mutated": False,
            "research_only": True,
            "production_authority": False,
        }
