"""Deterministic official-source macro releases for prospective research.

CPI/NFP/ISM are numbers already published by authoritative sources, so an LLM
must not reinterpret or fabricate them.  This module fetches BLS/ISM, validates
provenance, computes transparent transformations, and stores immutable first-seen
release snapshots in the same research database.  A release is visible to a T0
only when ``available_at <= captured_ts``.

There is intentionally no market consensus here.  Without a licensed consensus
feed, previous-release changes are called changes/momentum, never "surprise".
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


NUMERIC_MACRO_CONTRACT_VERSION = "macro-numeric-official-v1"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
ISM_INDEX_URL = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/"
USER_AGENT = "Seiltanzer-Macro-Research/1.0"
SUPPORTED_FAMILIES = ("CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES")

# BLS series. SA indexes are used for month/month changes; NSA indexes for year/year.
BLS_SERIES = {
    "cpi_headline_sa": "CUSR0000SA0",
    "cpi_core_sa": "CUSR0000SA0L1E",
    "cpi_headline_nsa": "CUUR0000SA0",
    "cpi_core_nsa": "CUUR0000SA0L1E",
    "nfp_level_k": "CES0000000001",
    "unemployment_rate_pct": "LNS14000000",
    "ahe_usd": "CES0500000003",
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct_change(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return (new / old - 1.0) * 100.0


def _period_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _bls_period(row: dict[str, Any]) -> tuple[int, int] | None:
    period = str(row.get("period") or "")
    if not re.fullmatch(r"M(0[1-9]|1[0-2])", period):
        return None
    try:
        return int(row["year"]), int(period[1:])
    except (KeyError, TypeError, ValueError):
        return None


def parse_bls_payload(payload: dict[str, Any]) -> dict[str, dict[tuple[int, int], float]]:
    """Validate the public BLS response and return finite monthly observations."""
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS_REQUEST_NOT_SUCCEEDED")
    rows = ((payload.get("Results") or {}).get("series") or [])
    if not isinstance(rows, list):
        raise ValueError("BLS_SERIES_MISSING")
    by_id: dict[str, dict[tuple[int, int], float]] = {}
    for series in rows:
        if not isinstance(series, dict):
            continue
        series_id = str(series.get("seriesID") or "")
        values: dict[tuple[int, int], float] = {}
        for item in series.get("data") or []:
            if not isinstance(item, dict):
                continue
            key = _bls_period(item)
            number = _finite(item.get("value"))
            if key is not None and number is not None:
                values[key] = number
        if series_id and values:
            by_id[series_id] = values
    missing = [sid for sid in BLS_SERIES.values() if sid not in by_id]
    if missing:
        raise ValueError("BLS_REQUIRED_SERIES_MISSING:" + ",".join(missing))
    return by_id


def _latest_common(series_maps: list[dict[tuple[int, int], float]]) -> tuple[int, int]:
    common = set(series_maps[0])
    for mapping in series_maps[1:]:
        common.intersection_update(mapping)
    if not common:
        raise ValueError("NO_COMMON_RELEASE_PERIOD")
    return max(common)


def build_bls_releases(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Create CPI and employment release facts from one official BLS response."""
    data = parse_bls_payload(payload)
    hs = data[BLS_SERIES["cpi_headline_sa"]]
    cs = data[BLS_SERIES["cpi_core_sa"]]
    hn = data[BLS_SERIES["cpi_headline_nsa"]]
    cn = data[BLS_SERIES["cpi_core_nsa"]]
    cpi_period = _latest_common([hs, cs, hn, cn])
    year, month = cpi_period
    prev = (year - 1, 12) if month == 1 else (year, month - 1)
    prev2 = (prev[0] - 1, 12) if prev[1] == 1 else (prev[0], prev[1] - 1)
    year_ago = (year - 1, month)
    required_cpi = [
        (hs, prev), (cs, prev), (hn, year_ago), (cn, year_ago),
        (hs, prev2), (cs, prev2),
    ]
    if any(key not in mapping for mapping, key in required_cpi):
        raise ValueError("BLS_CPI_HISTORY_INCOMPLETE")
    headline_mom = _pct_change(hs[cpi_period], hs[prev])
    core_mom = _pct_change(cs[cpi_period], cs[prev])
    prev_headline_mom = _pct_change(hs[prev], hs[prev2])
    prev_core_mom = _pct_change(cs[prev], cs[prev2])
    cpi = {
        "family": "CPI",
        "period": _period_key(year, month),
        "headline_mom_pct": headline_mom,
        "core_mom_pct": core_mom,
        "headline_yoy_pct": _pct_change(hn[cpi_period], hn[year_ago]),
        "core_yoy_pct": _pct_change(cn[cpi_period], cn[year_ago]),
        "headline_mom_change_pp": (
            headline_mom - prev_headline_mom
            if headline_mom is not None and prev_headline_mom is not None else None
        ),
        "core_mom_change_pp": (
            core_mom - prev_core_mom
            if core_mom is not None and prev_core_mom is not None else None
        ),
        "series": {
            "headline_sa": BLS_SERIES["cpi_headline_sa"],
            "core_sa": BLS_SERIES["cpi_core_sa"],
            "headline_nsa": BLS_SERIES["cpi_headline_nsa"],
            "core_nsa": BLS_SERIES["cpi_core_nsa"],
        },
        "consensus_available": False,
        "surprise_computed": False,
    }

    nfp = data[BLS_SERIES["nfp_level_k"]]
    ur = data[BLS_SERIES["unemployment_rate_pct"]]
    wage = data[BLS_SERIES["ahe_usd"]]
    nfp_period = _latest_common([nfp, ur, wage])
    year, month = nfp_period
    prev = (year - 1, 12) if month == 1 else (year, month - 1)
    prev2 = (prev[0] - 1, 12) if prev[1] == 1 else (prev[0], prev[1] - 1)
    year_ago = (year - 1, month)
    required_nfp = [
        (nfp, prev), (nfp, prev2), (ur, prev),
        (wage, prev), (wage, year_ago),
    ]
    if any(key not in mapping for mapping, key in required_nfp):
        raise ValueError("BLS_NFP_HISTORY_INCOMPLETE")
    payroll_change = nfp[nfp_period] - nfp[prev]
    previous_payroll_change = nfp[prev] - nfp[prev2]
    nfp_release = {
        "family": "NFP",
        "period": _period_key(year, month),
        "payroll_change_k": payroll_change,
        "previous_payroll_change_k": previous_payroll_change,
        "unemployment_rate_pct": ur[nfp_period],
        "unemployment_change_pp": ur[nfp_period] - ur[prev],
        "average_hourly_earnings_mom_pct": _pct_change(wage[nfp_period], wage[prev]),
        "average_hourly_earnings_yoy_pct": _pct_change(wage[nfp_period], wage[year_ago]),
        "series": {
            "payroll_level_k": BLS_SERIES["nfp_level_k"],
            "unemployment_rate_pct": BLS_SERIES["unemployment_rate_pct"],
            "average_hourly_earnings_usd": BLS_SERIES["ahe_usd"],
        },
        "consensus_available": False,
        "surprise_computed": False,
    }
    return {"CPI": cpi, "NFP": nfp_release}


class _LinkAndTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(str(href))
        elif tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        clean = re.sub(r"\s+", " ", data).strip()
        if not clean:
            return
        self.text_parts.append(clean)
        if self._cell is not None:
            self._cell.append(clean)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _verified_ism_url(url: str, family: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "www.ismworld.org", "ismworld.org"
    }:
        raise ValueError("ISM_UNOFFICIAL_SOURCE")
    path = parsed.path.lower()
    expected = "/pmi/" if family == "ISM_MANUFACTURING" else "/services/"
    if expected not in path:
        raise ValueError("ISM_WRONG_REPORT_PATH")
    return url


def discover_ism_report_urls(index_html: str) -> dict[str, str]:
    parser = _LinkAndTableParser()
    parser.feed(index_html)
    output: dict[str, str] = {}
    for href in parser.links:
        absolute = urljoin(ISM_INDEX_URL, href)
        path = urlparse(absolute).path.lower()
        if re.search(r"/reports/ism-pmi-reports/pmi/[a-z]+/?$", path):
            output.setdefault("ISM_MANUFACTURING", _verified_ism_url(absolute, "ISM_MANUFACTURING"))
        if re.search(r"/reports/ism-pmi-reports/services/[a-z]+/?$", path):
            output.setdefault("ISM_SERVICES", _verified_ism_url(absolute, "ISM_SERVICES"))
    if set(output) != {"ISM_MANUFACTURING", "ISM_SERVICES"}:
        raise ValueError("ISM_CURRENT_REPORT_LINKS_NOT_FOUND")
    return output


def _number(text: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
    return _finite(match.group(0)) if match else None


def _normalize_index_name(value: str) -> str:
    clean = value.lower().replace("®", "")
    clean = re.sub(r"[^a-z]+", " ", clean).strip()
    aliases = {
        "manufacturing pmi": "pmi",
        "services pmi": "pmi",
        "business activity production": "business_activity",
        "business activity": "business_activity",
        "production": "production",
        "new orders": "new_orders",
        "employment": "employment",
        "supplier deliveries": "supplier_deliveries",
        "inventories": "inventories",
        "prices": "prices",
    }
    return aliases.get(clean, clean.replace(" ", "_"))


def parse_ism_report(html: str, family: str, source_url: str) -> dict[str, Any]:
    source_url = _verified_ism_url(source_url, family)
    parser = _LinkAndTableParser()
    parser.feed(html)
    full_text = " ".join(parser.text_parts)
    heading = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\s+ISM",
        full_text,
        flags=re.I,
    )
    if not heading:
        raise ValueError("ISM_REPORT_PERIOD_NOT_FOUND")
    month = _MONTHS[heading.group(1).lower()]
    year = int(heading.group(2))

    wanted = {"pmi", "new_orders", "employment", "prices", "supplier_deliveries", "inventories"}
    wanted.add("production" if family == "ISM_MANUFACTURING" else "business_activity")
    metrics: dict[str, dict[str, float]] = {}
    for row in parser.rows:
        if len(row) < 3:
            continue
        name = _normalize_index_name(row[0])
        if name not in wanted or name in metrics:
            continue
        current = _number(row[1])
        previous = _number(row[2])
        if current is None or previous is None:
            continue
        delta = _number(row[3]) if len(row) > 3 else None
        if delta is None:
            delta = current - previous
        metrics[name] = {
            "current": current,
            "previous": previous,
            "change_pp": delta,
        }
    if "pmi" not in metrics:
        # Narrative fallback is deterministic and source-bound; it never invents a value.
        label = "Manufacturing PMI" if family == "ISM_MANUFACTURING" else "Services PMI"
        m = re.search(
            rf"{label}[^.]*?registered\s+(\d+(?:\.\d+)?)\s+percent[^.]*?(?:increase|decrease|above|below)[^.]*?(\d+(?:\.\d+)?)\s+percentage point",
            full_text, flags=re.I,
        )
        if not m:
            raise ValueError("ISM_PMI_NOT_FOUND")
        current = float(m.group(1))
        change_abs = float(m.group(2))
        sentence = m.group(0).lower()
        sign = -1.0 if ("decrease" in sentence or "below" in sentence) else 1.0
        delta = sign * change_abs
        metrics["pmi"] = {"current": current, "previous": current - delta, "change_pp": delta}
    return {
        "family": family,
        "period": _period_key(year, month),
        "metrics": metrics,
        "source_url": source_url,
        "consensus_available": False,
        "surprise_computed": False,
    }


@dataclass(frozen=True)
class ReleaseWrite:
    family: str
    period: str
    source: str
    source_url: str
    fetched_at: float
    payload: dict[str, Any]


class NumericMacroStore:
    """Append-only first-seen official numeric release store."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self._conn = runtime._conn
        self._lock = runtime._lock
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_numeric_releases(
                    release_id TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    period TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    fetched_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    UNIQUE(family,period,source_sha256)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_macro_numeric_causal "
                "ON macro_numeric_releases(family,available_at,period)"
            )

    def ingest(self, write: ReleaseWrite) -> dict[str, Any]:
        if write.family not in SUPPORTED_FAMILIES:
            raise ValueError("UNSUPPORTED_NUMERIC_MACRO_FAMILY")
        parsed = urlparse(write.source_url)
        host = (parsed.hostname or "").lower()
        if write.family in {"CPI", "NFP"}:
            official = host in {"api.bls.gov", "bls.gov", "www.bls.gov"}
        else:
            official = host in {"ismworld.org", "www.ismworld.org"}
        if parsed.scheme != "https" or not official:
            raise ValueError("NUMERIC_MACRO_UNOFFICIAL_SOURCE")
        fetched_at = float(write.fetched_at)
        if not math.isfinite(fetched_at) or fetched_at <= 0:
            raise ValueError("NUMERIC_MACRO_FETCH_TIME_INVALID")
        payload = dict(write.payload)
        payload_sha = _sha(payload)
        release_id = "macro-num-" + uuid.uuid4().hex
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT release_id,available_at FROM macro_numeric_releases "
                "WHERE family=? AND period=? AND source_sha256=?",
                (write.family, write.period, payload_sha),
            ).fetchone()
            if existing:
                return {
                    "status": "CACHED", "release_id": existing[0],
                    "available_at": float(existing[1]), "family": write.family,
                    "period": write.period,
                }
            self._conn.execute(
                "INSERT INTO macro_numeric_releases(" 
                "release_id,family,period,source,source_url,fetched_at,available_at," 
                "payload_json,source_sha256,contract_version,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    release_id, write.family, write.period, write.source,
                    write.source_url, fetched_at, fetched_at, _json(payload),
                    payload_sha, NUMERIC_MACRO_CONTRACT_VERSION, time.time(),
                ),
            )
        return {
            "status": "STORED", "release_id": release_id,
            "available_at": fetched_at, "family": write.family,
            "period": write.period,
        }

    def latest_admissible(self, family: str, captured_ts: float) -> dict[str, Any]:
        if family not in SUPPORTED_FAMILIES:
            raise ValueError("UNSUPPORTED_NUMERIC_MACRO_FAMILY")
        with self._lock:
            row = self._conn.execute(
                "SELECT release_id,period,source,source_url,fetched_at,available_at," 
                "payload_json,source_sha256 FROM macro_numeric_releases "
                "WHERE family=? AND available_at<=? "
                "ORDER BY available_at DESC, created_ts DESC LIMIT 1",
                (family, float(captured_ts)),
            ).fetchone()
        if not row:
            return {"status": "UNAVAILABLE", "family": family, "reason": "NO_CAUSAL_RELEASE"}
        return {
            "status": "VALID", "family": family, "release_id": row[0],
            "period": row[1], "source": row[2], "source_url": row[3],
            "fetched_at": float(row[4]), "available_at": float(row[5]),
            "payload": json.loads(row[6]), "source_sha256": row[7],
            "official_source_verified": True,
            "contract_version": NUMERIC_MACRO_CONTRACT_VERSION,
        }

    def bundle(self, captured_ts: float) -> dict[str, Any]:
        releases = {family: self.latest_admissible(family, captured_ts) for family in SUPPORTED_FAMILIES}
        return {
            "contract_version": NUMERIC_MACRO_CONTRACT_VERSION,
            "captured_ts": float(captured_ts),
            "releases": releases,
            "available_families": [k for k, v in releases.items() if v.get("status") == "VALID"],
            "causal_rule": "available_at<=captured_ts",
            "research_only": True,
            "production_authority": False,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT family,COUNT(*),MAX(available_at) FROM macro_numeric_releases GROUP BY family"
            ).fetchall()
        by_family = {r[0]: {"row_n": int(r[1]), "latest_available_at": float(r[2])} for r in rows}
        return {
            "contract_version": NUMERIC_MACRO_CONTRACT_VERSION,
            "families": {family: by_family.get(family, {"row_n": 0, "latest_available_at": None}) for family in SUPPORTED_FAMILIES},
            "consensus_feed": False,
            "surprise_computed": False,
            "research_only": True,
            "production_authority": False,
        }


class OfficialNumericMacroSource:
    def __init__(self, *, timeout_sec: float = 10.0) -> None:
        self.timeout_sec = max(3.0, min(20.0, float(timeout_sec)))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json"},
        )

    def fetch_bls(self, now: float | None = None) -> tuple[float, dict[str, dict[str, Any]]]:
        stamp = time.time() if now is None else float(now)
        year = time.gmtime(stamp).tm_year
        body = {
            "seriesid": list(BLS_SERIES.values()),
            "startyear": str(year - 1),
            "endyear": str(year),
        }
        with self._client() as client:
            response = client.post(BLS_API_URL, json=body)
            response.raise_for_status()
            payload = response.json()
        fetched_at = time.time()
        return fetched_at, build_bls_releases(payload)

    def fetch_ism(self) -> tuple[float, dict[str, dict[str, Any]]]:
        with self._client() as client:
            index = client.get(ISM_INDEX_URL)
            index.raise_for_status()
            urls = discover_ism_report_urls(index.text)
            result: dict[str, dict[str, Any]] = {}
            for family, url in urls.items():
                response = client.get(url)
                response.raise_for_status()
                result[family] = parse_ism_report(response.text, family, str(response.url))
        return time.time(), result


def _candidate_vector(bundle: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    releases = bundle.get("releases") or {}
    cpi = releases.get("CPI") or {}
    if cpi.get("status") == "VALID":
        p = cpi.get("payload") or {}
        keys = {
            "headline_mom_pct": "macro.cpi_headline_mom_pct",
            "core_mom_pct": "macro.cpi_core_mom_pct",
            "headline_yoy_pct": "macro.cpi_headline_yoy_pct",
            "core_yoy_pct": "macro.cpi_core_yoy_pct",
            "headline_mom_change_pp": "macro.cpi_headline_mom_change_pp",
            "core_mom_change_pp": "macro.cpi_core_mom_change_pp",
        }
        for source, target in keys.items():
            value = _finite(p.get(source))
            if value is not None:
                out[target] = value
    nfp = releases.get("NFP") or {}
    if nfp.get("status") == "VALID":
        p = nfp.get("payload") or {}
        keys = {
            "payroll_change_k": "macro.nfp_payroll_change_k",
            "previous_payroll_change_k": "macro.nfp_previous_payroll_change_k",
            "unemployment_rate_pct": "macro.nfp_unemployment_rate_pct",
            "unemployment_change_pp": "macro.nfp_unemployment_change_pp",
            "average_hourly_earnings_mom_pct": "macro.nfp_wage_mom_pct",
            "average_hourly_earnings_yoy_pct": "macro.nfp_wage_yoy_pct",
        }
        for source, target in keys.items():
            value = _finite(p.get(source))
            if value is not None:
                out[target] = value
    for family, prefix in (
        ("ISM_MANUFACTURING", "macro.ism_manufacturing"),
        ("ISM_SERVICES", "macro.ism_services"),
    ):
        release = releases.get(family) or {}
        if release.get("status") != "VALID":
            continue
        metrics = (release.get("payload") or {}).get("metrics") or {}
        for name, row in metrics.items():
            if not isinstance(row, dict):
                continue
            current = _finite(row.get("current"))
            change = _finite(row.get("change_pp"))
            if current is not None:
                out[f"{prefix}_{name}"] = current
            if change is not None:
                out[f"{prefix}_{name}_change_pp"] = change
    return out


def research_context(store: NumericMacroStore, captured_ts: float) -> dict[str, Any]:
    bundle = store.bundle(captured_ts)
    bundle["candidate_vector"] = _candidate_vector(bundle)
    bundle["current_ml_feature_vector_reads_numeric_macro"] = False
    bundle["eligible_for_future_ml_research"] = bool(bundle["available_families"])
    bundle["historical_backfill_allowed"] = False
    bundle["consensus_available"] = False
    bundle["surprise_computed"] = False
    return bundle


class NumericMacroRuntime:
    """Low-frequency official fetcher; never runs in the AI request path."""

    def __init__(self, store: NumericMacroStore, *, poll_sec: float = 3600.0,
                 startup_delay_sec: float = 90.0) -> None:
        self.store = store
        self.source = OfficialNumericMacroSource()
        self.poll_sec = max(900.0, float(poll_sec))
        self.startup_delay_sec = max(30.0, float(startup_delay_sec))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_started_at: float | None = None
        self.last_finished_at: float | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="numeric-macro-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _ingest_bls(self) -> list[dict[str, Any]]:
        fetched_at, releases = self.source.fetch_bls()
        return [
            self.store.ingest(ReleaseWrite(
                family=family, period=payload["period"], source="U.S. Bureau of Labor Statistics",
                source_url=BLS_API_URL, fetched_at=fetched_at, payload=payload,
            ))
            for family, payload in releases.items()
        ]

    def _ingest_ism(self) -> list[dict[str, Any]]:
        fetched_at, releases = self.source.fetch_ism()
        return [
            self.store.ingest(ReleaseWrite(
                family=family, period=payload["period"], source="Institute for Supply Management",
                source_url=payload["source_url"], fetched_at=fetched_at, payload=payload,
            ))
            for family, payload in releases.items()
        ]

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "IN_PROGRESS", "research_only": True}
            self.running = True
        self.last_started_at = time.time()
        output: dict[str, Any] = {"BLS": None, "ISM": None}
        errors: dict[str, str] = {}
        try:
            try:
                output["BLS"] = self._ingest_bls()
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                errors["BLS"] = f"{type(exc).__name__}:{str(exc)[:180]}"
            try:
                output["ISM"] = self._ingest_ism()
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                errors["ISM"] = f"{type(exc).__name__}:{str(exc)[:180]}"
            self.last_error = _json(errors) if errors else None
            self.last_result = {
                "status": "PARTIAL" if errors else "OK",
                "sources": output,
                "errors": errors,
                "no_placeholders": True,
                "research_only": True,
                "production_authority": False,
            }
            return self.last_result
        finally:
            self.last_finished_at = time.time()
            with self._lock:
                self.running = False

    def _run(self) -> None:
        if self._stop.wait(self.startup_delay_sec):
            return
        while not self._stop.is_set():
            self.refresh()
            self._wake.wait(self.poll_sec)
            self._wake.clear()

    def status(self) -> dict[str, Any]:
        return {
            "contract_version": NUMERIC_MACRO_CONTRACT_VERSION,
            "running": self.running,
            "poll_sec": self.poll_sec,
            "startup_delay_sec": self.startup_delay_sec,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "store": self.store.status(),
            "sources": {
                "BLS": BLS_API_URL,
                "ISM": ISM_INDEX_URL,
            },
            "no_placeholders": True,
            "consensus_available": False,
            "research_only": True,
            "production_authority": False,
        }
