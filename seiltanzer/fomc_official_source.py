"""Bounded official-source ingestion for the FOMC Data Factory.

The public research route never accepts arbitrary document text.  This module
fetches only Federal Reserve HTTPS pages, discovers the latest FOMC statement,
keeps the previous statement as raw reference text for semantic deltas, and sends
only the latest document to the LLM extractor.  No market/trading endpoint calls
this code.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from .macro_data_factory import MacroDataFactory, _normalize_text


OFFICIAL_SOURCE_CONTRACT_VERSION = "fomc-official-source-v1"
FED_BASE = "https://www.federalreserve.gov"
ALLOWED_HOSTS = {"www.federalreserve.gov", "federalreserve.gov"}
INDEX_PATH = "/newsevents/pressreleases/{year}-press-fomc.htm"
STATEMENT_RE = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm", re.I)
FETCH_TIMEOUT_SEC = 8.0
MAX_HTML_BYTES = 1_500_000


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "svg", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "svg", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            clean = re.sub(r"\s+", " ", html.unescape(data)).strip()
            if clean:
                self.parts.append(clean)


def _official_url(url: str) -> bool:
    parsed = urlparse(str(url))
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_HOSTS


def discover_statement_urls(index_html: str, *, base_url: str = FED_BASE) -> list[tuple[str, str]]:
    """Return unique (YYYYMMDD, official URL) rows newest first."""
    found: dict[str, str] = {}
    for match in STATEMENT_RE.finditer(index_html or ""):
        date_code = match.group(1)
        url = urljoin(base_url, match.group(0))
        if _official_url(url):
            found[date_code] = url
    return sorted(found.items(), key=lambda item: item[0], reverse=True)


def extract_statement_text(page_html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(page_html or "")
    text = " ".join(parser.parts)
    text = re.sub(r"\s+", " ", text).strip()

    starts = [
        "The Federal Open Market Committee approved the following statement",
        "The Federal Open Market Committee decided",
        "The Committee decided",
    ]
    start_positions = [text.find(marker) for marker in starts if text.find(marker) >= 0]
    if not start_positions:
        raise ValueError("FOMC_STATEMENT_BODY_NOT_FOUND")
    start = min(start_positions)
    end_candidates = [
        text.find("For media inquiries", start),
        text.find("Implementation Note", start),
        text.find("Last Update:", start),
    ]
    end_positions = [position for position in end_candidates if position > start]
    end = min(end_positions) if end_positions else len(text)
    body = _normalize_text(text[start:end])
    if "Committee" not in body:
        raise ValueError("FOMC_STATEMENT_BODY_INVALID")
    return body


def _published_at(date_code: str) -> float:
    # FOMC policy statements are released at 2:00 p.m. U.S. Eastern time.  The
    # discovered page itself is the authority for the date; zoneinfo handles DST.
    local = datetime.strptime(date_code, "%Y%m%d").replace(
        hour=14, minute=0, second=0, microsecond=0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    return local.timestamp()


def _bounded_get(client: httpx.Client, url: str) -> str:
    if not _official_url(url):
        raise RuntimeError("FOMC_SOURCE_HOST_REJECTED")
    response = client.get(url, headers={"User-Agent": "Seiltanzer-FOMC-Research/1.0"})
    response.raise_for_status()
    if not _official_url(str(response.url)):
        raise RuntimeError("FOMC_SOURCE_REDIRECT_REJECTED")
    raw = response.content
    if len(raw) > MAX_HTML_BYTES:
        raise RuntimeError("FOMC_SOURCE_PAGE_TOO_LARGE")
    return response.text


def fetch_recent_statements(*, limit: int = 2, now: float | None = None) -> list[dict[str, Any]]:
    """Fetch up to two latest official statements, spanning year boundary if needed."""
    now_ts = float(time.time() if now is None else now)
    year = datetime.fromtimestamp(now_ts, tz=ZoneInfo("UTC")).year
    discovered: dict[str, str] = {}
    with httpx.Client(timeout=FETCH_TIMEOUT_SEC, follow_redirects=True, trust_env=False) as client:
        for candidate_year in (year, year-1):
            index_url = urljoin(FED_BASE, INDEX_PATH.format(year=candidate_year))
            try:
                index_html = _bounded_get(client, index_url)
            except httpx.HTTPError as exc:
                if candidate_year == year:
                    raise RuntimeError(f"FOMC_INDEX_FETCH_ERROR:{type(exc).__name__}") from exc
                continue
            for date_code, url in discover_statement_urls(index_html):
                discovered[date_code] = url
            if len(discovered) >= max(2, int(limit)):
                break
        selected = sorted(discovered.items(), reverse=True)[:max(1, min(int(limit), 4))]
        if not selected:
            raise RuntimeError("FOMC_STATEMENT_LINK_NOT_FOUND")
        documents = []
        for date_code, url in selected:
            try:
                page = _bounded_get(client, url)
                body = extract_statement_text(page)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"FOMC_STATEMENT_FETCH_ERROR:{type(exc).__name__}") from exc
            documents.append({
                "family": "FOMC_STATEMENT",
                "source": "Federal Reserve Board",
                "source_url": url,
                "published_at": _published_at(date_code),
                "fetched_at": time.time(),
                "text": body,
                "numeric": {},
                "official_source_verified": True,
                "official_source_contract": OFFICIAL_SOURCE_CONTRACT_VERSION,
            })
    return documents


def seed_reference_document(factory: MacroDataFactory, document: dict[str, Any]) -> str:
    """Persist previous official text without an LLM extraction or semantic record."""
    normalized = _normalize_text(document.get("text"))
    family = str(document.get("family") or "").strip().upper()
    source = str(document.get("source") or "").strip()
    source_url = str(document.get("source_url") or "").strip()
    if family != "FOMC_STATEMENT" or source != "Federal Reserve Board" or not _official_url(source_url):
        raise ValueError("UNVERIFIED_REFERENCE_DOCUMENT")
    published_at = float(document["published_at"])
    fetched_at = float(document.get("fetched_at") or time.time())
    document_sha = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    document_id = "macro-doc-" + hashlib.sha256(
        f"{family}|{document_sha}".encode("utf-8")
    ).hexdigest()[:28]
    with factory._lock, factory._conn:
        factory._conn.execute(
            "INSERT OR IGNORE INTO macro_documents(document_id,family,source,source_url,"
            "published_at,fetched_at,normalized_text,document_sha256,retrospective_only,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,1,?)",
            (document_id, family, source, source_url, published_at, fetched_at,
             normalized, document_sha, time.time()),
        )
    return document_id


def refresh_latest_fomc(factory: MacroDataFactory, *,
                        fetcher: Callable[..., list[dict[str, Any]]] = fetch_recent_statements,
                        extractor=None) -> dict[str, Any]:
    documents = fetcher(limit=2)
    if not documents:
        return {
            "contract_version": OFFICIAL_SOURCE_CONTRACT_VERSION,
            "status": "UNAVAILABLE", "reason": "NO_OFFICIAL_DOCUMENT",
            "research_only": True, "production_authority": False,
        }
    documents = sorted(documents, key=lambda item: float(item["published_at"]), reverse=True)
    latest = documents[0]
    if len(documents) > 1:
        seed_reference_document(factory, documents[1])
    result = factory.extract_document(latest, extractor=extractor)
    return {
        **result,
        "official_source_verified": True,
        "official_source_contract": OFFICIAL_SOURCE_CONTRACT_VERSION,
        "previous_statement_seeded_as_reference_only": len(documents) > 1,
    }
