"""Production transport hardening for official BLS/ISM acquisition.

The production VPS can receive transient transport/provider failures from otherwise
public official endpoints while the same deterministic fetch succeeds elsewhere.
Keep source URLs, parsers, provenance checks and payloads unchanged.  The primary
path always attempts the official upstream first.  For BLS only, a failed upstream
request may reuse a bounded, already-materialized official CPI/NFP snapshot when
both required families are still causally admissible and recently fetched.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx


TRANSPORT_VERSION = "macro-official-transport-v4"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
BLS_OFFICIAL_HOSTS = frozenset({"api.bls.gov", "bls.gov", "www.bls.gov"})
BLS_CACHE_FALLBACK_HARD_MAX_AGE_SEC = 24.0 * 60.0 * 60.0
BLS_CACHE_FALLBACK_ENV = "MACRO_BLS_CACHE_FALLBACK_MAX_AGE_SEC"
BLS_CACHE_REQUIRED_FAMILIES = ("CPI", "NFP")
BLS_TRANSPORT_ATTEMPTS_PER_ROUTE = 2
BLS_TRANSPORT_RETRY_BACKOFF_SEC = 1.0
_INSTALLED = False


def macro_proxy_url() -> str | None:
    """Resolve transport only; never expose the proxy value in public status."""
    return (
        os.environ.get("MACRO_HTTP_PROXY", "").strip()
        or os.environ.get("OPENROUTER_PROXY", "").strip()
        or None
    )


def bls_cache_fallback_max_age_sec() -> float:
    """Return a configurable TTL that can only tighten the 24h safety cap.

    A non-positive value disables fallback. Invalid configuration fails back to the
    conservative built-in cap rather than accidentally making cache reuse unbounded.
    """
    raw = os.environ.get(BLS_CACHE_FALLBACK_ENV, "").strip()
    if not raw:
        return BLS_CACHE_FALLBACK_HARD_MAX_AGE_SEC
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        return BLS_CACHE_FALLBACK_HARD_MAX_AGE_SEC
    if configured <= 0.0:
        return 0.0
    return min(configured, BLS_CACHE_FALLBACK_HARD_MAX_AGE_SEC)


def _verified_cached_bls_rows(
    runtime: Any,
    *,
    now_ts: float,
    upstream_error: Exception,
) -> list[dict[str, Any]] | None:
    """Build transparent fallback metadata from existing official BLS releases.

    This function never inserts, mutates or synthesizes a macro release. The store's
    ``latest_admissible`` query enforces available_at <= T0 and returns only persisted
    release facts; the additional checks below enforce complete CPI+NFP coverage,
    official BLS provenance and a bounded fetch age.
    """
    max_age_sec = bls_cache_fallback_max_age_sec()
    if max_age_sec <= 0.0:
        return None

    cached_rows: list[dict[str, Any]] = []
    for family in BLS_CACHE_REQUIRED_FAMILIES:
        # Keep this positional to match NumericMacroStore.latest_admissible(family, captured_ts).
        release = runtime.store.latest_admissible(family, now_ts)
        if not release or release.get("status") != "VALID":
            return None
        if release.get("official_source_verified") is not True:
            return None

        source_url = str(release.get("source_url") or "")
        parsed_source = urlparse(source_url)
        source_host = (parsed_source.hostname or "").lower()
        if parsed_source.scheme != "https" or source_host not in BLS_OFFICIAL_HOSTS:
            return None

        try:
            fetched_at = float(release.get("fetched_at"))
            available_at = float(release.get("available_at"))
        except (TypeError, ValueError):
            return None
        if fetched_at <= 0.0 or fetched_at > now_ts:
            return None
        if available_at > now_ts:
            return None

        cache_age_sec = now_ts - fetched_at
        if cache_age_sec > max_age_sec:
            return None

        cached_rows.append(
            {
                "status": "CACHED",
                "release_id": str(release.get("release_id") or ""),
                "family": family,
                "period": release.get("period"),
                "available_at": available_at,
                "fetched_at": fetched_at,
                "cache_age_sec": round(cache_age_sec, 3),
                "official_source_verified": True,
                "fallback_reason": "BLS_UPSTREAM_FAILED",
                "upstream_error": f"{type(upstream_error).__name__}:{str(upstream_error)[:160]}",
            }
        )

    return cached_rows


def macro_transport_status() -> dict[str, Any]:
    max_age_sec = bls_cache_fallback_max_age_sec()
    return {
        "contract_version": TRANSPORT_VERSION,
        "proxy_configured": macro_proxy_url() is not None,
        "proxy_source": (
            "MACRO_HTTP_PROXY" if os.environ.get("MACRO_HTTP_PROXY", "").strip()
            else "OPENROUTER_PROXY" if os.environ.get("OPENROUTER_PROXY", "").strip()
            else None
        ),
        "official_source_urls_unchanged": True,
        "payload_or_parser_fallback_added": False,
        "bls_transport": {
            "direct_official_first": True,
            "configured_proxy_fallback": macro_proxy_url() is not None,
            "attempts_per_route": BLS_TRANSPORT_ATTEMPTS_PER_ROUTE,
            "retry_backoff_sec": BLS_TRANSPORT_RETRY_BACKOFF_SEC,
            "request_timeout_unchanged": True,
        },
        "bls_cache_fallback": {
            "enabled": max_age_sec > 0.0,
            "max_age_sec": max_age_sec,
            "hard_max_age_sec": BLS_CACHE_FALLBACK_HARD_MAX_AGE_SEC,
            "required_families": list(BLS_CACHE_REQUIRED_FAMILIES),
            "official_only": True,
            "valid_and_available_only": True,
            "upstream_attempted_first": True,
            "release_materialization": False,
        },
        "research_only": True,
        "production_authority": False,
    }


def _fetch_bls_official_with_failover(
    source: Any,
    *,
    now: float | None = None,
) -> tuple[float, dict[str, dict[str, Any]]]:
    """Fetch the unchanged official BLS payload over bounded transport routes.

    The shared production proxy can itself exhaust BLS's anonymous request quota.
    Try the official endpoint directly first, then the configured transport proxy.
    Every successful response still passes the canonical BLS parser; no cached,
    synthetic or alternate-source payload can enter through this function.
    """
    from .macro_numeric_data import BLS_API_URL, BLS_SERIES, build_bls_releases

    stamp = time.time() if now is None else float(now)
    year = time.gmtime(stamp).tm_year
    body = {
        "seriesid": list(BLS_SERIES.values()),
        "startyear": str(year - 1),
        "endyear": str(year),
    }
    proxy = macro_proxy_url()
    routes: list[tuple[str, str | None]] = [("DIRECT_OFFICIAL", None)]
    if proxy:
        routes.append(("CONFIGURED_PROXY", proxy))

    errors: list[str] = []
    for route_name, route_proxy in routes:
        for attempt in range(1, BLS_TRANSPORT_ATTEMPTS_PER_ROUTE + 1):
            try:
                with httpx.Client(
                    timeout=source.timeout_sec,
                    follow_redirects=True,
                    proxy=route_proxy,
                    trust_env=False,
                    headers={
                        "User-Agent": BROWSER_USER_AGENT,
                        "Accept": "application/json",
                        "Accept-Language": "en-US,en;q=0.8",
                        "Cache-Control": "no-cache",
                    },
                ) as client:
                    response = client.post(BLS_API_URL, json=body)
                    response.raise_for_status()
                    payload = response.json()
                releases = build_bls_releases(payload)
                return time.time(), releases
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                errors.append(
                    f"{route_name}[{attempt}]:{type(exc).__name__}:"
                    f"{str(exc)[:120]}"
                )
                if attempt < BLS_TRANSPORT_ATTEMPTS_PER_ROUTE:
                    time.sleep(BLS_TRANSPORT_RETRY_BACKOFF_SEC * attempt)

    raise ValueError("BLS_OFFICIAL_TRANSPORT_EXHAUSTED:" + "|".join(errors))


def install_macro_transport_refinement() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .macro_numeric_data import NumericMacroRuntime, OfficialNumericMacroSource

    def refined_client(self: OfficialNumericMacroSource) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_sec,
            follow_redirects=True,
            proxy=macro_proxy_url(),
            trust_env=False,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
                "Cache-Control": "no-cache",
            },
        )

    original_ingest_bls = NumericMacroRuntime._ingest_bls

    def fetch_bls_with_failover(
        self: OfficialNumericMacroSource,
        now: float | None = None,
    ) -> tuple[float, dict[str, dict[str, Any]]]:
        return _fetch_bls_official_with_failover(self, now=now)

    def ingest_bls_with_official_cache(self: NumericMacroRuntime) -> list[dict[str, Any]]:
        try:
            return original_ingest_bls(self)
        except Exception as exc:
            cached_rows = _verified_cached_bls_rows(
                self,
                now_ts=time.time(),
                upstream_error=exc,
            )
            if cached_rows is None:
                raise
            return cached_rows

    setattr(ingest_bls_with_official_cache, "_macro_bls_official_cache_v3", True)
    OfficialNumericMacroSource._client = refined_client
    OfficialNumericMacroSource.fetch_bls = fetch_bls_with_failover
    NumericMacroRuntime._ingest_bls = ingest_bls_with_official_cache
    _INSTALLED = True
