"""Reliable official ISM report acquisition without the protected landing page.

The ISM "current reports" landing page can redirect non-browser HTTP clients to
its SSO site.  The monthly report documents themselves are public and have stable
official URLs.  Probe only the latest plausible report months and accept a page
only when the parser proves that its embedded report period matches the requested
month.  No value, date or report is synthesized when every candidate is absent.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from .macro_numeric_data import OfficialNumericMacroSource, parse_ism_report


ISM_RESILIENCE_VERSION = "ism-direct-official-v1"
_BASE = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports"
_INSTALLED = False


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _candidate_months(now: float | None = None, *, depth: int = 3) -> list[tuple[int, int, str]]:
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc) if now is not None else dt.datetime.now(dt.timezone.utc)
    out: list[tuple[int, int, str]] = []
    # A report released in month M describes month M-1.  Before that family's
    # release day the M-1 document may simply return 404, so fall back causally to
    # older public documents rather than guessing a value or release date.
    for back in range(1, max(2, int(depth)) + 1):
        year, month = _shift_month(stamp.year, stamp.month, -back)
        slug = dt.date(year, month, 1).strftime("%B").lower()
        out.append((year, month, slug))
    return out


def _direct_url(family: str, slug: str) -> str:
    section = "pmi" if family == "ISM_MANUFACTURING" else "services"
    return f"{_BASE}/{section}/{slug}/"


def fetch_latest_direct_ism(source: OfficialNumericMacroSource, *, now: float | None = None) -> tuple[float, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, Any]] = {}
    failures: dict[str, list[str]] = {}
    with source._client() as client:
        for family in ("ISM_MANUFACTURING", "ISM_SERVICES"):
            family_failures: list[str] = []
            for year, month, slug in _candidate_months(now):
                url = _direct_url(family, slug)
                try:
                    response = client.get(url)
                    if response.status_code == 404:
                        family_failures.append(f"{year:04d}-{month:02d}:HTTP404")
                        continue
                    response.raise_for_status()
                    parsed = parse_ism_report(response.text, family, str(response.url))
                    expected = f"{year:04d}-{month:02d}"
                    if parsed.get("period") != expected:
                        family_failures.append(
                            f"{expected}:PERIOD_MISMATCH:{parsed.get('period')}")
                        continue
                    parsed["acquisition"] = {
                        "version": ISM_RESILIENCE_VERSION,
                        "mode": "validated_direct_official_monthly_report",
                        "requested_url": url,
                        "final_url": str(response.url),
                        "period_validated": True,
                        "synthetic_fallback": False,
                    }
                    result[family] = parsed
                    break
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    family_failures.append(
                        f"{year:04d}-{month:02d}:{type(exc).__name__}:{str(exc)[:100]}")
            if family not in result:
                failures[family] = family_failures
    if set(result) != {"ISM_MANUFACTURING", "ISM_SERVICES"}:
        detail = ";".join(
            f"{family}=[{','.join(items)}]" for family, items in failures.items())
        raise ValueError("ISM_DIRECT_OFFICIAL_UNAVAILABLE:" + detail[:600])
    import time
    return time.time(), result


def install_ism_source_resilience() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def _fetch_ism(self: OfficialNumericMacroSource):
        return fetch_latest_direct_ism(self)

    OfficialNumericMacroSource.fetch_ism = _fetch_ism
