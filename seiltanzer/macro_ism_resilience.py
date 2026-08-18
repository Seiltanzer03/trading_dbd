"""Reliable official ISM acquisition when the newest monthly page is gated.

ISM's newest full report can redirect non-browser clients to an SSO/CAPTCHA while
older monthly reports and its own same-day ``PMI Reports Roundup`` article remain
public.  We therefore try, in order:

1. the exact official monthly report;
2. the exact official ISM roundup for that report month, joined only with the
   immediately previous official monthly report to calculate transparent deltas.

Every accepted page is validated by official host + report family + exact period.
Missing components stay missing; no number/date/report is synthesized.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .macro_numeric_data import (
    OfficialNumericMacroSource,
    _LinkAndTableParser,
    parse_ism_report,
)


ISM_RESILIENCE_VERSION = "ism-official-resilience-v2"
_BASE = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports"
_ROUNDUP_BASE = (
    "https://www.ismworld.org/supply-management-news-and-reports/news-publications/"
    "inside-supply-management-magazine/blog"
)
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _month_slug(year: int, month: int) -> str:
    return dt.date(year, month, 1).strftime("%B").lower()


def _candidate_months(now: float | None = None, *, depth: int = 3) -> list[tuple[int, int, str]]:
    stamp = (
        dt.datetime.fromtimestamp(now, dt.timezone.utc)
        if now is not None else dt.datetime.now(dt.timezone.utc)
    )
    out: list[tuple[int, int, str]] = []
    # A release published in calendar month M normally describes M-1.  Before
    # release day the newest candidate simply will not validate and we fall back
    # to older *real* official documents, never a guessed current value.
    for back in range(1, max(2, int(depth)) + 1):
        year, month = _shift_month(stamp.year, stamp.month, -back)
        out.append((year, month, _month_slug(year, month)))
    return out


def _direct_url(family: str, slug: str) -> str:
    section = "pmi" if family == "ISM_MANUFACTURING" else "services"
    return f"{_BASE}/{section}/{slug}/"


def _roundup_url(family: str, year: int, month: int) -> str:
    release_year, release_month = _shift_month(year, month, 1)
    slug = _month_slug(year, month)
    suffix = "manufacturing" if family == "ISM_MANUFACTURING" else "services"
    return (
        f"{_ROUNDUP_BASE}/{release_year}/{release_year}-{release_month:02d}/"
        f"ism-pmi-reports-roundup-{slug}-{year}-{suffix}/"
    )


def _official_ism_roundup_url(url: str, family: str, year: int, month: int) -> bool:
    parsed = urlparse(str(url))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "www.ismworld.org", "ismworld.org"
    }:
        return False
    suffix = "manufacturing" if family == "ISM_MANUFACTURING" else "services"
    expected = f"ism-pmi-reports-roundup-{_month_slug(year, month)}-{year}-{suffix}"
    return expected in parsed.path.lower()


def _plain_text(html: str) -> str:
    parser = _LinkAndTableParser()
    parser.feed(html)
    return re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()


def _first_number(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = _finite(match.group(1))
            if value is not None:
                return value
    return None


def _roundup_current_values(text: str, family: str) -> dict[str, float]:
    """Extract only values explicitly stated by the official roundup prose."""
    patterns: dict[str, list[str]]
    if family == "ISM_MANUFACTURING":
        patterns = {
            "pmi": [
                r"composite\s+PMI[^.]{0,90}?registered\s+(\d+(?:\.\d+)?)\s*percent",
                r"Manufacturing\s+PMI[^.]{0,90}?registered\s+(\d+(?:\.\d+)?)\s*percent",
            ],
            "new_orders": [
                r"New\s+Orders\s*\((\d+(?:\.\d+)?)\s*percent\)",
                r"New\s+Orders\s+Index[^.]{0,90}?(\d+(?:\.\d+)?)\s*percent",
            ],
            "production": [
                r"Production\s+Index[^.]{0,90}?registered\s+(\d+(?:\.\d+)?)\s*percent",
                r"Production\s*\((\d+(?:\.\d+)?)\s*percent\)",
            ],
            "employment": [
                r"Employment\s+Index[^.]{0,140}?registered\s+(\d+(?:\.\d+)?)\s*percent",
                r"Employment\s+Index\s*\((\d+(?:\.\d+)?)\s*percent\)",
            ],
            "supplier_deliveries": [
                r"Supplier\s+Deliveries\s+Index[^.]{0,100}?(?:registered|increased|decreased|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "inventories": [
                r"Inventories\s+Index[^.]{0,100}?(?:registered|increased|decreased|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "prices": [
                r"Prices\s+Index[^.]{0,100}?(?:registered|increased|decreased|rose|fell|to)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
                r"Prices\s+Index\s*\((\d+(?:\.\d+)?)\s*percent\)",
            ],
        }
    else:
        patterns = {
            "pmi": [
                r"composite\s+PMI[^.]{0,100}?(?:increasing|increased|decreasing|decreased|registered)[^.]{0,40}?to\s+(\d+(?:\.\d+)?)\s*percent",
                r"Services\s+PMI[^.]{0,100}?(?:registered|at)\s+(\d+(?:\.\d+)?)\s*percent",
            ],
            "business_activity": [
                r"Business\s+Activity\s+Index[^.]{0,100}?(?:increased|decreased|registered|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "new_orders": [
                r"New\s+Orders\s+Index[^.]{0,100}?(?:elevated|increased|decreased|registered|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "employment": [
                r"Employment\s+Index\s*\((\d+(?:\.\d+)?)\s*percent\)",
                r"Employment\s+Index[^.]{0,100}?(?:registered|increased|decreased|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "supplier_deliveries": [
                r"Supplier\s+Deliveries\s+Index[^.]{0,100}?(?:decreased|increased|registered|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "inventories": [
                r"Inventories\s+Index[^.]{0,100}?(?:registered|increased|decreased|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
            "prices": [
                r"Prices\s+Index\s*\((\d+(?:\.\d+)?)\s*percent\)",
                r"Prices\s+Index[^.]{0,100}?(?:registered|increased|decreased|rose|fell)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*percent",
            ],
        }
    out: dict[str, float] = {}
    for name, candidates in patterns.items():
        value = _first_number(text, candidates)
        if value is not None and 0.0 <= value <= 100.0:
            out[name] = value
    return out


def parse_ism_roundup(
    html: str,
    family: str,
    source_url: str,
    *,
    year: int,
    month: int,
    previous_report: dict[str, Any],
) -> dict[str, Any]:
    """Parse official roundup and derive deltas only from previous official report."""
    if not _official_ism_roundup_url(source_url, family, year, month):
        raise ValueError("ISM_ROUNDUP_UNOFFICIAL_OR_WRONG_PATH")
    text = _plain_text(html)
    month_name = dt.date(year, month, 1).strftime("%B")
    label = "Manufacturing" if family == "ISM_MANUFACTURING" else "Services"
    title_re = rf"ISM.*PMI.*Reports\s+Roundup:\s*{month_name}\s+{label}"
    if not re.search(title_re, text, flags=re.I):
        raise ValueError("ISM_ROUNDUP_TITLE_PERIOD_FAMILY_MISMATCH")
    if not re.search(rf"{label}\s+PMI.*Report\s+for\s+{month_name}", text, flags=re.I):
        # Manufacturing prose uses "Report for July"; services does too. Requiring
        # this second independent phrase prevents a stale/cross-linked article from
        # being accepted solely because its page title looks right.
        raise ValueError("ISM_ROUNDUP_BODY_PERIOD_FAMILY_MISMATCH")

    current = _roundup_current_values(text, family)
    if "pmi" not in current or len(current) < 4:
        raise ValueError("ISM_ROUNDUP_INSUFFICIENT_NUMERIC_FACTS")
    previous_metrics = previous_report.get("metrics") or {}
    metrics: dict[str, dict[str, float]] = {}
    for name, value in current.items():
        prior_row = previous_metrics.get(name)
        prior = _finite(prior_row.get("current")) if isinstance(prior_row, dict) else None
        if prior is None:
            # Do not create a delta when the immediately previous official report
            # did not expose the same component to our deterministic parser.
            continue
        metrics[name] = {
            "current": value,
            "previous": prior,
            "change_pp": value - prior,
        }
    if "pmi" not in metrics or len(metrics) < 4:
        raise ValueError("ISM_ROUNDUP_PREVIOUS_REPORT_JOIN_INCOMPLETE")
    return {
        "family": family,
        "period": f"{year:04d}-{month:02d}",
        "metrics": metrics,
        "source_url": source_url,
        "consensus_available": False,
        "surprise_computed": False,
    }


def _fetch_previous_direct(
    client: httpx.Client,
    family: str,
    year: int,
    month: int,
) -> dict[str, Any]:
    prev_year, prev_month = _shift_month(year, month, -1)
    url = _direct_url(family, _month_slug(prev_year, prev_month))
    response = client.get(url)
    response.raise_for_status()
    parsed = parse_ism_report(response.text, family, str(response.url))
    expected = f"{prev_year:04d}-{prev_month:02d}"
    if parsed.get("period") != expected:
        raise ValueError(f"ISM_PREVIOUS_PERIOD_MISMATCH:{parsed.get('period')}!={expected}")
    return parsed


def _try_roundup(
    client: httpx.Client,
    family: str,
    year: int,
    month: int,
) -> dict[str, Any]:
    url = _roundup_url(family, year, month)
    response = client.get(url)
    response.raise_for_status()
    if not _official_ism_roundup_url(str(response.url), family, year, month):
        raise ValueError("ISM_ROUNDUP_REDIRECTED_OFFICIAL_PATH")
    previous = _fetch_previous_direct(client, family, year, month)
    parsed = parse_ism_roundup(
        response.text, family, str(response.url), year=year, month=month,
        previous_report=previous,
    )
    parsed["acquisition"] = {
        "version": ISM_RESILIENCE_VERSION,
        "mode": "validated_official_roundup_plus_previous_report",
        "requested_url": url,
        "final_url": str(response.url),
        "period_validated": True,
        "previous_period": previous.get("period"),
        "previous_source_url": previous.get("source_url"),
        "synthetic_fallback": False,
        "missing_components_remain_missing": True,
    }
    return parsed


def fetch_latest_direct_ism(
    source: OfficialNumericMacroSource,
    *,
    now: float | None = None,
) -> tuple[float, dict[str, dict[str, Any]]]:
    """Return newest causally fetchable official ISM release for both families."""
    result: dict[str, dict[str, Any]] = {}
    failures: dict[str, list[str]] = {}
    with source._client() as client:
        for family in ("ISM_MANUFACTURING", "ISM_SERVICES"):
            family_failures: list[str] = []
            for year, month, slug in _candidate_months(now):
                expected = f"{year:04d}-{month:02d}"
                direct_url = _direct_url(family, slug)
                try:
                    response = client.get(direct_url)
                    response.raise_for_status()
                    parsed = parse_ism_report(response.text, family, str(response.url))
                    if parsed.get("period") != expected:
                        raise ValueError(
                            f"ISM_DIRECT_PERIOD_MISMATCH:{parsed.get('period')}!={expected}")
                    parsed["acquisition"] = {
                        "version": ISM_RESILIENCE_VERSION,
                        "mode": "validated_direct_official_monthly_report",
                        "requested_url": direct_url,
                        "final_url": str(response.url),
                        "period_validated": True,
                        "synthetic_fallback": False,
                    }
                    result[family] = parsed
                    break
                except (httpx.HTTPError, ValueError, TypeError) as direct_exc:
                    family_failures.append(
                        f"{expected}:DIRECT:{type(direct_exc).__name__}:{str(direct_exc)[:90]}")

                # Current-month full pages may be SSO-gated.  ISM publishes a
                # public same-day roundup on the same official domain; use that
                # only if its title/body period and previous official report join
                # all validate.
                try:
                    parsed = _try_roundup(client, family, year, month)
                    result[family] = parsed
                    break
                except (httpx.HTTPError, ValueError, TypeError) as roundup_exc:
                    family_failures.append(
                        f"{expected}:ROUNDUP:{type(roundup_exc).__name__}:{str(roundup_exc)[:90]}")
            if family not in result:
                failures[family] = family_failures
    if set(result) != {"ISM_MANUFACTURING", "ISM_SERVICES"}:
        detail = ";".join(
            f"{family}=[{','.join(items)}]" for family, items in failures.items())
        raise ValueError("ISM_OFFICIAL_UNAVAILABLE:" + detail[:900])
    return time.time(), result


def install_ism_source_resilience() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def _fetch_ism(self: OfficialNumericMacroSource):
        return fetch_latest_direct_ism(self)

    OfficialNumericMacroSource.fetch_ism = _fetch_ism
