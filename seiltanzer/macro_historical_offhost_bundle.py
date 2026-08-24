"""Exact-SHA off-host transport for official historical macro pages."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .macro_offhost_bundle import (
    _canonical_json,
    _sha256,
    _without,
    current_repository_sha,
    write_bundle,
)


CONTRACT_VERSION = "official-macro-historical-offhost-v6-verified-partial"
DEFAULT_WINDOW_DAYS = 120
MAX_WINDOW_DAYS = 180
FOMC_WINDOW_DAYS = 365
DEFAULT_MAX_AGE_SEC = 45.0 * 60.0
HARD_MAX_AGE_SEC = 60.0 * 60.0
PATH_ENV = "SEILTANZER_OFFHOST_HISTORICAL_MACRO_BUNDLE"
_INSTALLED = False


def max_age_sec() -> float:
    raw = os.environ.get("MACRO_HISTORICAL_OFFHOST_MAX_AGE_SEC", "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_SEC
    return min(max(0.0, value), HARD_MAX_AGE_SEC)


def configured_path(runtime: Any) -> Path | None:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    value = getattr(runtime, "offhost_historical_bundle_path", None)
    return Path(value) if value else None


def _record(value: dict[str, Any]) -> dict[str, Any]:
    output = dict(value)
    output["record_sha256"] = _sha256(output)
    return output


def _historical_availability(
    *, bls_records: list[dict[str, Any]], ism_records: list[dict[str, Any]],
    fomc_records: list[dict[str, Any]], errors: dict[str, str],
) -> dict[str, str]:
    def status(count: int, prefixes: tuple[str, ...]) -> str:
        has_error = any(
            any(key.startswith(prefix) for prefix in prefixes) for key in errors
        )
        if count > 0:
            return (
                "VERIFIED_PARTIAL_REAL_HISTORY" if has_error
                else "VERIFIED_REAL_HISTORY"
            )
        return "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED"

    return {
        family: status(
            sum(
                1 for record in bls_records
                if (record.get("spec") or {}).get("family") == family
            ),
            (f"BLS:manifest:{family}", f"BLS:archive:{family}:"),
        )
        for family in ("CPI", "NFP")
    } | {
        family: status(
            sum(1 for record in ism_records if record.get("family") == family),
            (f"ISM:{family}:",),
        )
        for family in ("ISM_MANUFACTURING", "ISM_SERVICES")
    } | {
        "FOMC_STATEMENT_DETERMINISTIC": status(
            len(fomc_records), ("FOMC:",)
        )
    }


def build_bundle(
    *, expected_sha: str, acceptance_run_id: str, now: float | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("HISTORICAL_OFFHOST_EXPECTED_SHA_INVALID")
    if not re.fullmatch(r"[0-9]+", acceptance_run_id):
        raise ValueError("HISTORICAL_OFFHOST_ACCEPTANCE_RUN_ID_INVALID")
    days = int(window_days)
    if days <= 0 or days > MAX_WINDOW_DAYS:
        raise ValueError("HISTORICAL_OFFHOST_WINDOW_INVALID")

    from .macro_bls_historical_bootstrap import (
        BLS_ARCHIVE_INDEXES,
        OfficialBLSArchiveSource,
        _bls_archive_identity,
    )
    from .macro_ism_historical_bootstrap import (
        FAMILIES as ISM_FAMILIES,
        OfficialISMHistoricalSource,
        _periods_for_window,
    )
    from .macro_fomc_deterministic_bootstrap import (
        INDEX_TEMPLATE,
        OfficialFOMCArchiveSource,
        deterministic_statement_payload,
        extract_statement_text,
        parse_fomc_index,
    )

    stamp = time.time() if now is None else float(now)
    start_ts = stamp - days * 86400.0
    bls_source = OfficialBLSArchiveSource()
    bls_records: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    schedules: dict[str, dict[str, Any]] = {}
    manifest_links: list[tuple[str, str]] = []
    for family, source_url in BLS_ARCHIVE_INDEXES.items():
        try:
            index_html, links = bls_source.archive_index_manifest(family)
            schedules[family] = _record({
                "format": "HTML_ARCHIVE_INDEX",
                "family": family,
                "source_url": source_url,
                "content": index_html,
                "source_sha256": _sha256(index_html),
            })
            manifest_links.extend((family, link) for link in links)
        except Exception as exc:
            errors[f"BLS:manifest:{family}"] = (
                f"{type(exc).__name__}:{str(exc)[:180]}"
            )

    dedup: dict[tuple[str, str, float], dict[str, Any]] = {}
    for family, source_url in manifest_links:
        identity = _bls_archive_identity(source_url)
        if identity is None:
            errors[f"BLS:archive:{family}:invalid-url"] = (
                "ValueError:BLS_ATOM_ARCHIVE_LINK_INVALID"
            )
            continue
        archive_day = datetime.strptime(identity[1], "%m%d%Y").replace(
            tzinfo=ZoneInfo("America/New_York")
        ).timestamp()
        # The URL date is used only to avoid fetching feed history outside the
        # bounded bundle.  The archive's embargo header remains the sole causal
        # published_at authority and is verified after download.
        if archive_day < start_ts - 86400.0 or archive_day > stamp + 86400.0:
            continue
        try:
            spec, fetched_at, html, payload = bls_source.archive_from_manifest(
                family=family, source_url=source_url)
            if start_ts <= spec.published_at <= stamp + 1e-6:
                dedup[(spec.family, spec.period, spec.published_at)] = _record({
                    "spec": asdict(spec),
                    "fetched_at": fetched_at,
                    "html": html,
                    "source_sha256": _sha256(html),
                    "payload": payload,
                })
        except Exception as exc:
            errors[f"BLS:archive:{family}:{source_url.rsplit('/', 1)[-1]}"] = (
                f"{type(exc).__name__}:{str(exc)[:180]}"
            )
    bls_records = [dedup[key] for key in sorted(dedup, key=lambda item: item[2])]

    ism_source = OfficialISMHistoricalSource()
    ism_records: list[dict[str, Any]] = []
    periods = _periods_for_window(start_ts, stamp)
    for family in ISM_FAMILIES:
        for period in periods:
            try:
                fetched_at, html, parsed = ism_source.fetch(family, period)
                ism_records.append(_record({
                    "family": family,
                    "period": period,
                    "source_url": parsed["source_url"],
                    "fetched_at": fetched_at,
                    "html": html,
                    "source_sha256": _sha256(html),
                    "payload": parsed,
                }))
            except Exception as exc:
                errors[f"ISM:{family}:{period}"] = (
                    f"{type(exc).__name__}:{str(exc)[:180]}"
                )

    # Deterministic FOMC features need the immediately previous official
    # statement.  Fetch a bounded one-year window plus exactly one predecessor;
    # production re-runs the same canonical parser/store before the snapshot.
    fomc_start_ts = stamp - FOMC_WINDOW_DAYS * 86400.0
    fomc_start_year = datetime.fromtimestamp(
        fomc_start_ts, tz=ZoneInfo("UTC")).year
    fomc_end_year = datetime.fromtimestamp(stamp, tz=ZoneInfo("UTC")).year
    fomc_source = OfficialFOMCArchiveSource()
    fomc_schedules: dict[str, dict[str, Any]] = {}
    fomc_specs = []
    with fomc_source._client() as client:
        for year in range(fomc_start_year - 1, fomc_end_year + 1):
            url = INDEX_TEMPLATE.format(year=year)
            try:
                html = fomc_source._validated(client.get(url))
                fomc_schedules[str(year)] = _record({
                    "format": "HTML",
                    "year": year,
                    "source_url": url,
                    "content": html,
                    "source_sha256": _sha256(html),
                })
                fomc_specs.extend(parse_fomc_index(html))
            except Exception as exc:
                errors[f"FOMC:schedule:{year}"] = (
                    f"{type(exc).__name__}:{str(exc)[:180]}"
                )

    fomc_dedup = {spec.source_url: spec for spec in fomc_specs}
    in_window = sorted(
        (
            spec for spec in fomc_dedup.values()
            if fomc_start_ts <= spec.approximate_published_at <= stamp + 86400.0
        ),
        key=lambda item: item.date_code,
    )
    predecessors = [
        spec for spec in fomc_dedup.values()
        if spec.approximate_published_at < fomc_start_ts
    ]
    selected_fomc = list(in_window)
    if predecessors:
        selected_fomc.insert(
            0, max(predecessors, key=lambda item: item.approximate_published_at)
        )
    selected_fomc = list({spec.source_url: spec for spec in selected_fomc}.values())
    selected_fomc.sort(key=lambda item: item.date_code)

    fomc_records: list[dict[str, Any]] = []
    previous_spec = None
    previous_body = None
    for spec in selected_fomc:
        previous_url = previous_spec.source_url if previous_spec else None
        try:
            fetched_at, html = fomc_source.archive(spec)
            body = extract_statement_text(html)
            payload = deterministic_statement_payload(
                body, previous_body=previous_body)
            fomc_records.append(_record({
                "spec": asdict(spec),
                "previous_source_url": previous_url,
                "fetched_at": fetched_at,
                "html": html,
                "source_sha256": _sha256(html),
                "payload": payload,
            }))
            previous_body = body
        except Exception as exc:
            errors[f"FOMC:{spec.date_code}"] = (
                f"{type(exc).__name__}:{str(exc)[:180]}"
            )
            previous_body = None
        previous_spec = spec

    fomc_context_start_ts = min(
        (spec.approximate_published_at for spec in selected_fomc),
        default=fomc_start_ts,
    )

    bundle = {
        "contract_version": CONTRACT_VERSION,
        "expected_sha": expected_sha,
        "acceptance_run_id": acceptance_run_id,
        "created_at": time.time(),
        "window": {"start_ts": start_ts, "end_ts": stamp, "days": days},
        "bls_schedules": schedules,
        "bls_records": bls_records,
        "ism_records": ism_records,
        "fomc_window": {
            "start_ts": fomc_start_ts,
            "context_start_ts": fomc_context_start_ts,
            "end_ts": stamp,
            "days": FOMC_WINDOW_DAYS,
        },
        "fomc_schedules": fomc_schedules,
        "fomc_records": fomc_records,
        "errors": errors,
        "historical_availability": _historical_availability(
            bls_records=bls_records,
            ism_records=ism_records,
            fomc_records=fomc_records,
            errors=errors,
        ),
        "missing_is_zero": False,
        "official_sources_only": True,
        "canonical_parsers_only": True,
        "synthetic_data_used": False,
        "no_placeholders": True,
        "research_only": True,
        "production_authority": False,
    }
    bundle["bundle_sha256"] = _sha256(bundle)
    return bundle


def validate_bundle(
    bundle: dict[str, Any], *, expected_sha: str,
    acceptance_run_id: str | None = None, now: float | None = None,
) -> dict[str, Any]:
    from .macro_bls_historical_bootstrap import (
        BLS_ARCHIVE_INDEXES,
        BLSReleaseSpec,
        FAMILIES as BLS_FAMILIES,
        _official_bls_url,
        parse_bls_archive_spec,
        parse_bls_archive_index_urls,
        parse_cpi_archive,
        parse_nfp_archive,
    )
    from .macro_ism_historical_bootstrap import (
        FAMILIES as ISM_FAMILIES,
        _official_ism_url,
        parse_ism_historical_roundup,
    )
    from .macro_fomc_deterministic_bootstrap import (
        FOMCStatementSpec,
        INDEX_TEMPLATE,
        _official_fed_url,
        deterministic_statement_payload,
        extract_statement_text,
        parse_fomc_index,
        parse_release_timestamp,
    )

    if not isinstance(bundle, dict) or bundle.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("HISTORICAL_OFFHOST_CONTRACT_MISMATCH")
    if bundle.get("bundle_sha256") != _sha256(_without(bundle, "bundle_sha256")):
        raise ValueError("HISTORICAL_OFFHOST_BUNDLE_HASH_MISMATCH")
    if bundle.get("expected_sha") != expected_sha:
        raise ValueError("HISTORICAL_OFFHOST_EXACT_SHA_MISMATCH")
    if acceptance_run_id is not None and bundle.get("acceptance_run_id") != acceptance_run_id:
        raise ValueError("HISTORICAL_OFFHOST_ACCEPTANCE_OWNER_MISMATCH")
    if not all((
        bundle.get("official_sources_only") is True,
        bundle.get("canonical_parsers_only") is True,
        bundle.get("synthetic_data_used") is False,
        bundle.get("no_placeholders") is True,
        bundle.get("missing_is_zero") is False,
        bundle.get("research_only") is True,
        bundle.get("production_authority") is False,
    )):
        raise ValueError("HISTORICAL_OFFHOST_SAFETY_CONTRACT_MISMATCH")
    stamp = time.time() if now is None else float(now)
    age_limit = max_age_sec()
    created_at = float(bundle.get("created_at") or 0.0)
    if age_limit <= 0 or created_at > stamp + 120.0 or stamp - created_at > age_limit:
        raise ValueError("HISTORICAL_OFFHOST_BUNDLE_STALE")
    window = bundle.get("window") or {}
    start_ts = float(window.get("start_ts") or 0.0)
    end_ts = float(window.get("end_ts") or 0.0)
    if not (0 < start_ts <= end_ts <= stamp + 120.0):
        raise ValueError("HISTORICAL_OFFHOST_WINDOW_INVALID")
    if end_ts - start_ts > MAX_WINDOW_DAYS * 86400.0 + 1.0:
        raise ValueError("HISTORICAL_OFFHOST_WINDOW_TOO_WIDE")

    errors = bundle.get("errors")
    if (
        not isinstance(errors, dict) or len(errors) > 200
        or any(
            not isinstance(key, str)
            or not key.startswith(("BLS:", "ISM:", "FOMC:"))
            or not isinstance(value, str)
            or not value
            or len(value) > 240
            for key, value in errors.items()
        )
    ):
        raise ValueError("HISTORICAL_OFFHOST_ERRORS_INVALID")

    manifest_links: set[tuple[str, str]] = set()
    schedules = bundle.get("bls_schedules")
    if (
        not isinstance(schedules, dict)
        or not set(schedules).issubset(set(BLS_FAMILIES))
    ):
        raise ValueError("HISTORICAL_OFFHOST_BLS_SCHEDULES_MISSING")
    for family in BLS_FAMILIES:
        if family not in schedules and f"BLS:manifest:{family}" not in errors:
            raise ValueError("HISTORICAL_OFFHOST_BLS_SCHEDULES_MISSING")
    for family, value in schedules.items():
        if value.get("record_sha256") != _sha256(_without(value, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        if (
            value.get("family") != family
            or value.get("source_url") != BLS_ARCHIVE_INDEXES[family]
            or not _official_bls_url(str(value.get("source_url") or ""))
        ):
            raise ValueError("HISTORICAL_OFFHOST_BLS_SOURCE_INVALID")
        if value.get("format") != "HTML_ARCHIVE_INDEX":
            raise ValueError("HISTORICAL_OFFHOST_BLS_CALENDAR_FORMAT_INVALID")
        content = str(value.get("content") or "")
        if len(content) < 200 or len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_SIZE_INVALID")
        if value.get("source_sha256") != _sha256(content):
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_HASH_MISMATCH")
        manifest_links.update(
            (family, source_url)
            for source_url in parse_bls_archive_index_urls(
                content,
                family=family,
                source_url=BLS_ARCHIVE_INDEXES[family],
            )
        )

    bls_records = bundle.get("bls_records")
    if not isinstance(bls_records, list) or len(bls_records) > 50:
        raise ValueError("HISTORICAL_OFFHOST_BLS_RECORDS_INVALID")
    bls_counts = {
        family: sum(
            1 for record in bls_records
            if (record.get("spec") or {}).get("family") == family
        )
        for family in BLS_FAMILIES
    }
    for family, count in bls_counts.items():
        has_transport_error = any(
            key == f"BLS:manifest:{family}"
            or key.startswith(f"BLS:archive:{family}:")
            for key in errors
        )
        if count < 1 and not has_transport_error:
            raise ValueError("HISTORICAL_OFFHOST_BLS_RECORDS_MISSING")
    for record in bls_records:
        if record.get("record_sha256") != _sha256(_without(record, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        raw_spec = record.get("spec") or {}
        spec = BLSReleaseSpec(**raw_spec)
        if (
            spec.family not in BLS_FAMILIES
            or (spec.family, spec.source_url) not in manifest_links
        ):
            raise ValueError("HISTORICAL_OFFHOST_BLS_CALENDAR_MISMATCH")
        fetched_at = float(record.get("fetched_at") or 0.0)
        if fetched_at + 300.0 < spec.published_at or fetched_at > stamp + 120.0:
            raise ValueError("HISTORICAL_OFFHOST_FETCH_TIME_INVALID")
        if spec.published_at < start_ts - 1.0 or spec.published_at > end_ts + 1.0:
            raise ValueError("HISTORICAL_OFFHOST_BLS_WINDOW_MISMATCH")
        html = str(record.get("html") or "")
        if len(html) < 200 or len(html.encode("utf-8")) > 2_000_000:
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_SIZE_INVALID")
        if record.get("source_sha256") != _sha256(html):
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_HASH_MISMATCH")
        archive_spec = parse_bls_archive_spec(
            html, family=spec.family, source_url=spec.source_url)
        if archive_spec != spec:
            raise ValueError("HISTORICAL_OFFHOST_BLS_ARCHIVE_SPEC_MISMATCH")
        parsed = (
            parse_cpi_archive(html, expected_period=spec.period)
            if spec.family == "CPI"
            else parse_nfp_archive(html, expected_period=spec.period)
        )
        if _canonical_json(parsed) != _canonical_json(record.get("payload")):
            raise ValueError("HISTORICAL_OFFHOST_BLS_PAYLOAD_MISMATCH")

    ism_records = bundle.get("ism_records")
    if not isinstance(ism_records, list) or len(ism_records) > 30:
        raise ValueError("HISTORICAL_OFFHOST_ISM_RECORDS_INVALID")
    ism_counts = {
        family: sum(1 for record in ism_records if record.get("family") == family)
        for family in ISM_FAMILIES
    }
    for family, count in ism_counts.items():
        if count < 1 and not any(
            key.startswith(f"ISM:{family}:") for key in errors
        ):
            raise ValueError("HISTORICAL_OFFHOST_ISM_RECORDS_MISSING")
    for record in ism_records:
        if record.get("record_sha256") != _sha256(_without(record, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        family, period = str(record.get("family")), str(record.get("period"))
        source_url = str(record.get("source_url") or "")
        if family not in ISM_FAMILIES or not _official_ism_url(source_url):
            raise ValueError("HISTORICAL_OFFHOST_ISM_SOURCE_INVALID")
        fetched_at = float(record.get("fetched_at") or 0.0)
        if fetched_at <= 0.0 or fetched_at > stamp + 120.0:
            raise ValueError("HISTORICAL_OFFHOST_FETCH_TIME_INVALID")
        html = str(record.get("html") or "")
        if len(html) < 200 or len(html.encode("utf-8")) > 2_000_000:
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_SIZE_INVALID")
        if record.get("source_sha256") != _sha256(html):
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_HASH_MISMATCH")
        parsed = parse_ism_historical_roundup(
            html, family=family, period=period, source_url=source_url
        )
        if _canonical_json(parsed) != _canonical_json(record.get("payload")):
            raise ValueError("HISTORICAL_OFFHOST_ISM_PAYLOAD_MISMATCH")
        published_at = float(parsed.get("published_at") or 0.0)
        if published_at < start_ts - 90.0 * 86400.0 or published_at > end_ts + 86400.0:
            raise ValueError("HISTORICAL_OFFHOST_ISM_WINDOW_MISMATCH")
        if fetched_at + 300.0 < published_at:
            raise ValueError("HISTORICAL_OFFHOST_FETCH_TIME_INVALID")

    fomc_window = bundle.get("fomc_window") or {}
    fomc_start_ts = float(fomc_window.get("start_ts") or 0.0)
    fomc_context_start_ts = float(fomc_window.get("context_start_ts") or 0.0)
    fomc_end_ts = float(fomc_window.get("end_ts") or 0.0)
    if not (
        0 < fomc_context_start_ts <= fomc_start_ts <= fomc_end_ts <= stamp + 120.0
    ):
        raise ValueError("HISTORICAL_OFFHOST_FOMC_WINDOW_INVALID")
    if fomc_end_ts - fomc_start_ts > FOMC_WINDOW_DAYS * 86400.0 + 1.0:
        raise ValueError("HISTORICAL_OFFHOST_FOMC_WINDOW_TOO_WIDE")

    fomc_schedule_specs: dict[str, FOMCStatementSpec] = {}
    fomc_schedules = bundle.get("fomc_schedules")
    if not isinstance(fomc_schedules, dict) or not fomc_schedules:
        raise ValueError("HISTORICAL_OFFHOST_FOMC_SCHEDULES_MISSING")
    for raw_year, value in fomc_schedules.items():
        if value.get("record_sha256") != _sha256(_without(value, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        year = int(raw_year)
        if value.get("format") != "HTML" or int(value.get("year") or 0) != year:
            raise ValueError("HISTORICAL_OFFHOST_FOMC_CALENDAR_FORMAT_INVALID")
        expected_url = INDEX_TEMPLATE.format(year=year)
        if str(value.get("source_url") or "") != expected_url:
            raise ValueError("HISTORICAL_OFFHOST_FOMC_SOURCE_INVALID")
        content = str(value.get("content") or "")
        if len(content) < 200 or len(content.encode("utf-8")) > 1_500_000:
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_SIZE_INVALID")
        if value.get("source_sha256") != _sha256(content):
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_HASH_MISMATCH")
        for spec in parse_fomc_index(content):
            fomc_schedule_specs[spec.source_url] = spec

    fomc_records = bundle.get("fomc_records")
    if not isinstance(fomc_records, list) or len(fomc_records) > 40:
        raise ValueError("HISTORICAL_OFFHOST_FOMC_RECORDS_INVALID")
    if not fomc_records and not any(key.startswith("FOMC:") for key in errors):
        raise ValueError("HISTORICAL_OFFHOST_FOMC_RECORDS_MISSING")
    previous_html_by_url: dict[str, str] = {}
    for record in sorted(
        fomc_records, key=lambda row: str((row.get("spec") or {}).get("date_code") or "")
    ):
        if record.get("record_sha256") != _sha256(_without(record, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        spec = FOMCStatementSpec(**(record.get("spec") or {}))
        scheduled = fomc_schedule_specs.get(spec.source_url)
        if scheduled != spec or not _official_fed_url(spec.source_url):
            raise ValueError("HISTORICAL_OFFHOST_FOMC_CALENDAR_MISMATCH")
        html = str(record.get("html") or "")
        if len(html) < 200 or len(html.encode("utf-8")) > 1_500_000:
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_SIZE_INVALID")
        if record.get("source_sha256") != _sha256(html):
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_HASH_MISMATCH")
        published_at = parse_release_timestamp(html, date_code=spec.date_code)
        if published_at < fomc_context_start_ts - 86400.0 or published_at > fomc_end_ts + 86400.0:
            raise ValueError("HISTORICAL_OFFHOST_FOMC_WINDOW_MISMATCH")
        fetched_at = float(record.get("fetched_at") or 0.0)
        if fetched_at + 300.0 < published_at or fetched_at > stamp + 120.0:
            raise ValueError("HISTORICAL_OFFHOST_FETCH_TIME_INVALID")
        previous_url = record.get("previous_source_url")
        if previous_url is not None and str(previous_url) not in fomc_schedule_specs:
            raise ValueError("HISTORICAL_OFFHOST_FOMC_PREVIOUS_INVALID")
        body = extract_statement_text(html)
        previous_html = previous_html_by_url.get(str(previous_url))
        previous_body = extract_statement_text(previous_html) if previous_html else None
        parsed = deterministic_statement_payload(body, previous_body=previous_body)
        if _canonical_json(parsed) != _canonical_json(record.get("payload")):
            raise ValueError("HISTORICAL_OFFHOST_FOMC_PAYLOAD_MISMATCH")
        previous_html_by_url[spec.source_url] = html
    expected_availability = _historical_availability(
        bls_records=bls_records,
        ism_records=ism_records,
        fomc_records=fomc_records,
        errors=errors,
    )
    if bundle.get("historical_availability") != expected_availability:
        raise ValueError("HISTORICAL_OFFHOST_AVAILABILITY_MISMATCH")
    return bundle


def load_verified_bundle(runtime: Any) -> dict[str, Any]:
    path = configured_path(runtime)
    if path is None or not path.is_file():
        raise ValueError("HISTORICAL_OFFHOST_BUNDLE_MISSING")
    sha = current_repository_sha()
    if sha is None:
        raise ValueError("HISTORICAL_OFFHOST_RUNTIME_SHA_UNAVAILABLE")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("HISTORICAL_OFFHOST_BUNDLE_UNREADABLE") from exc
    return validate_bundle(value, expected_sha=sha)


def _bls_refresh(runtime: Any) -> dict[str, Any]:
    from .macro_bls_historical_bootstrap import (
        HISTORICAL_LOOKBACK_BUFFER_SEC,
        BLSReleaseSpec,
        observation_span,
    )

    bundle = load_verified_bundle(runtime)
    span = observation_span(runtime.store.runtime)
    if span is None:
        return {"status": "NO_OBSERVATIONS", "stored": [], "skipped": 0,
                "errors": {}, "research_only": True, "production_authority": False}
    stored = []
    errors = {
        key: value for key, value in (bundle.get("errors") or {}).items()
        if key.startswith("BLS:")
    }
    window = bundle["window"]
    if float(window["start_ts"]) > max(0.0, span[0] - HISTORICAL_LOOKBACK_BUFFER_SEC):
        errors["bundle_window"] = "BLS_REQUIRED_OBSERVATION_WINDOW_NOT_COVERED"
    if float(window["end_ts"]) + 120.0 < span[1]:
        errors["bundle_window"] = "BLS_LATEST_OBSERVATION_NOT_COVERED"
    for record in bundle["bls_records"]:
        spec = BLSReleaseSpec(**record["spec"])
        if spec.published_at > span[1] + 1e-6:
            continue
        try:
            stored.append(runtime.store.ingest(
                spec, html=record["html"], payload=record["payload"],
                fetched_at=record["fetched_at"],
            ))
        except Exception as exc:
            errors[f"INGEST:BLS:{spec.family}:{spec.period}"] = (
                f"{type(exc).__name__}:{str(exc)[:180]}"
            )
    return {
        "status": "PARTIAL" if errors else "OK",
        "stored": stored, "skipped": 0, "errors": errors,
        "transport": "OFF_HOST_OFFICIAL_SOURCE",
        "bundle_sha256": bundle["bundle_sha256"],
        "historical_availability": {
            family: bundle["historical_availability"][family]
            for family in ("CPI", "NFP")
        },
        "research_only": True, "production_authority": False,
    }


def _ism_refresh(runtime: Any) -> dict[str, Any]:
    from .macro_ism_historical_bootstrap import FAMILIES
    from .macro_bls_historical_bootstrap import (
        HISTORICAL_LOOKBACK_BUFFER_SEC,
        observation_span,
    )

    bundle = load_verified_bundle(runtime)
    span = observation_span(runtime.store.runtime)
    if span is None:
        return {"status": "NO_OBSERVATIONS", "stored": [], "skipped": 0,
                "errors": {}, "research_only": True, "production_authority": False}
    stored = []
    skipped = 0
    errors = {k: v for k, v in (bundle.get("errors") or {}).items() if k.startswith("ISM:")}
    window = bundle["window"]
    if float(window["start_ts"]) > max(0.0, span[0] - HISTORICAL_LOOKBACK_BUFFER_SEC):
        errors["bundle_window"] = "ISM_REQUIRED_OBSERVATION_WINDOW_NOT_COVERED"
    if float(window["end_ts"]) + 120.0 < span[1]:
        errors["bundle_window"] = "ISM_LATEST_OBSERVATION_NOT_COVERED"
    records = sorted(bundle["ism_records"], key=lambda row: (row["family"], row["period"]))
    for family in FAMILIES:
        first = True
        for record in (row for row in records if row["family"] == family):
            if runtime.store.has(family, record["period"]):
                skipped += 1
                first = False
                continue
            try:
                stored.append(runtime.store.ingest(
                    record["payload"], html=record["html"],
                    fetched_at=record["fetched_at"], require_previous=not first,
                ))
            except Exception as exc:
                errors[f"INGEST:ISM:{family}:{record['period']}"] = (
                    f"{type(exc).__name__}:{str(exc)[:180]}"
                )
            first = False
    return {
        "status": "PARTIAL" if errors else "OK",
        "stored": stored, "skipped": skipped, "errors": errors,
        "transport": "OFF_HOST_OFFICIAL_SOURCE",
        "bundle_sha256": bundle["bundle_sha256"],
        "historical_availability": {
            family: bundle["historical_availability"][family]
            for family in FAMILIES
        },
        "research_only": True, "production_authority": False,
    }


def _fomc_refresh(runtime: Any) -> dict[str, Any]:
    from .macro_fomc_deterministic_bootstrap import (
        FOMCStatementSpec,
        HISTORICAL_LOOKBACK_BUFFER_SEC,
        _observation_span,
    )

    bundle = load_verified_bundle(runtime)
    span = _observation_span(runtime.store.runtime)
    now_ts = time.time()
    required_start_ts = (
        max(0.0, span[0] - HISTORICAL_LOOKBACK_BUFFER_SEC)
        if span is not None else now_ts - HISTORICAL_LOOKBACK_BUFFER_SEC
    )
    errors = {
        key: value for key, value in (bundle.get("errors") or {}).items()
        if key.startswith("FOMC:")
    }
    fomc_window = bundle["fomc_window"]
    if float(fomc_window["start_ts"]) > required_start_ts:
        errors["bundle_window"] = "FOMC_REQUIRED_OBSERVATION_WINDOW_NOT_COVERED"
    if span is not None and float(fomc_window["end_ts"]) + 120.0 < span[1]:
        errors["bundle_window"] = "FOMC_LATEST_OBSERVATION_NOT_COVERED"

    stored = []
    skipped = 0
    records = sorted(
        bundle["fomc_records"], key=lambda row: row["spec"]["date_code"])
    for record in records:
        spec = FOMCStatementSpec(**record["spec"])
        if spec.approximate_published_at > now_ts + 86400.0:
            continue
        if runtime.store.has_source_url(spec.source_url):
            skipped += 1
            continue
        try:
            stored.append(runtime.store.ingest(
                spec,
                html=record["html"],
                previous_source_url=record.get("previous_source_url"),
                fetched_at=record["fetched_at"],
            ))
        except Exception as exc:
            errors[f"INGEST:FOMC:{spec.date_code}"] = (
                f"{type(exc).__name__}:{str(exc)[:180]}"
            )
    return {
        "status": "PARTIAL" if errors else "OK",
        "observation_span": (
            {"first_t0": span[0], "latest_t0": span[1]} if span else None
        ),
        "bootstrap_window": {
            "start_ts": required_start_ts,
            "end_ts": now_ts,
        },
        "candidate_release_n": len(records),
        "stored": stored,
        "skipped": skipped,
        "errors": errors,
        "transport": "OFF_HOST_OFFICIAL_SOURCE",
        "bundle_sha256": bundle["bundle_sha256"],
        "historical_availability": {
            "FOMC_STATEMENT_DETERMINISTIC": bundle[
                "historical_availability"
            ]["FOMC_STATEMENT_DETERMINISTIC"]
        },
        "llm_used": False,
        "source_kind": "OFFICIAL_FED_DATED_STATEMENT_PAGE",
        "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
        "old_t0_rows_mutated": False,
        "research_only": True,
        "production_authority": False,
    }


def install_historical_offhost_transport() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .macro_bls_historical_bootstrap import BLSHistoricalBootstrapRuntime
    from .macro_fomc_deterministic_bootstrap import FOMCDeterministicBootstrapRuntime
    from .macro_ism_historical_bootstrap import ISMHistoricalBootstrapRuntime

    def run(self: Any, callback: Any) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "IN_PROGRESS", "research_only": True}
            self.running = True
        self.last_started_at = time.time()
        try:
            try:
                result = callback(self)
            except Exception as exc:
                result = {
                    "status": "PARTIAL", "stored": [], "skipped": 0,
                    "errors": {"offhost_bundle": f"{type(exc).__name__}:{str(exc)[:180]}"},
                    "transport": "OFF_HOST_OFFICIAL_SOURCE",
                    "research_only": True, "production_authority": False,
                }
            self.last_result = result
            self.last_error = (
                _canonical_json(result["errors"]) if result.get("errors") else None
            )
            return result
        finally:
            self.last_finished_at = time.time()
            with self._lock:
                self.running = False

    def bls_refresh(self: Any, *, now: float | None = None) -> dict[str, Any]:
        del now
        return run(self, _bls_refresh)

    def ism_refresh(self: Any, *, now: float | None = None) -> dict[str, Any]:
        del now
        return run(self, _ism_refresh)

    def fomc_refresh(self: Any, *, now: float | None = None) -> dict[str, Any]:
        del now
        return run(self, _fomc_refresh)

    BLSHistoricalBootstrapRuntime.refresh = bls_refresh
    ISMHistoricalBootstrapRuntime.refresh = ism_refresh
    FOMCDeterministicBootstrapRuntime.refresh = fomc_refresh
    _INSTALLED = True


def transport_status() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "families": [
            "CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES",
            "FOMC_STATEMENT_DETERMINISTIC",
        ],
        "official_sources_only": True,
        "canonical_parser_exact_sha": True,
        "bundle_and_record_hashes_required": True,
        "network_fetch_on_production": False,
        "synthetic_data_used": False,
        "research_only": True,
        "production_authority": False,
    }
