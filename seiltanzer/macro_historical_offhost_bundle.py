"""Exact-SHA off-host transport for official historical BLS/ISM pages."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .macro_offhost_bundle import (
    _canonical_json,
    _sha256,
    _without,
    current_repository_sha,
    write_bundle,
)


CONTRACT_VERSION = "official-macro-historical-offhost-v2-ical"
DEFAULT_WINDOW_DAYS = 120
MAX_WINDOW_DAYS = 180
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
        BLS_ICAL_URL,
        OfficialBLSArchiveSource,
        parse_bls_ical,
    )
    from .macro_ism_historical_bootstrap import (
        FAMILIES as ISM_FAMILIES,
        OfficialISMHistoricalSource,
        _periods_for_window,
    )

    stamp = time.time() if now is None else float(now)
    start_ts = stamp - days * 86400.0
    bls_source = OfficialBLSArchiveSource()
    with bls_source._client() as client:
        calendar = bls_source._validated_response(client.get(BLS_ICAL_URL))
    schedules: dict[str, dict[str, Any]] = {
        "official_ical": _record({
            "format": "ICAL",
            "source_url": BLS_ICAL_URL,
            "content": calendar,
            "source_sha256": _sha256(calendar),
        })
    }
    specs = parse_bls_ical(calendar)

    bls_records: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    dedup = {
        (item.family, item.period, item.published_at): item for item in specs
        if start_ts <= item.published_at <= stamp + 1e-6
    }
    for spec in sorted(dedup.values(), key=lambda item: item.published_at):
        try:
            fetched_at, html, payload = bls_source.archive(spec)
            bls_records.append(_record({
                "spec": asdict(spec),
                "fetched_at": fetched_at,
                "html": html,
                "source_sha256": _sha256(html),
                "payload": payload,
            }))
        except Exception as exc:
            errors[f"BLS:{spec.family}:{spec.period}"] = (
                f"{type(exc).__name__}:{str(exc)[:180]}"
            )

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

    bundle = {
        "contract_version": CONTRACT_VERSION,
        "expected_sha": expected_sha,
        "acceptance_run_id": acceptance_run_id,
        "created_at": time.time(),
        "window": {"start_ts": start_ts, "end_ts": stamp, "days": days},
        "bls_schedules": schedules,
        "bls_records": bls_records,
        "ism_records": ism_records,
        "errors": errors,
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
        BLSReleaseSpec,
        FAMILIES as BLS_FAMILIES,
        _official_bls_url,
        parse_bls_ical,
        parse_cpi_archive,
        parse_nfp_archive,
    )
    from .macro_ism_historical_bootstrap import (
        FAMILIES as ISM_FAMILIES,
        _official_ism_url,
        parse_ism_historical_roundup,
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

    schedule_specs: set[tuple[str, str, float, str]] = set()
    schedules = bundle.get("bls_schedules")
    if not isinstance(schedules, dict) or not schedules:
        raise ValueError("HISTORICAL_OFFHOST_BLS_SCHEDULES_MISSING")
    for value in schedules.values():
        if value.get("record_sha256") != _sha256(_without(value, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        if not _official_bls_url(str(value.get("source_url") or "")):
            raise ValueError("HISTORICAL_OFFHOST_BLS_SOURCE_INVALID")
        if value.get("format") != "ICAL":
            raise ValueError("HISTORICAL_OFFHOST_BLS_CALENDAR_FORMAT_INVALID")
        content = str(value.get("content") or "")
        if len(content) < 200 or len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_SIZE_INVALID")
        if value.get("source_sha256") != _sha256(content):
            raise ValueError("HISTORICAL_OFFHOST_SOURCE_HASH_MISMATCH")
        for spec in parse_bls_ical(content):
            schedule_specs.add((spec.family, spec.period, spec.published_at, spec.source_url))

    bls_records = bundle.get("bls_records")
    if not isinstance(bls_records, list) or len(bls_records) > 50:
        raise ValueError("HISTORICAL_OFFHOST_BLS_RECORDS_INVALID")
    for record in bls_records:
        if record.get("record_sha256") != _sha256(_without(record, "record_sha256")):
            raise ValueError("HISTORICAL_OFFHOST_RECORD_HASH_MISMATCH")
        raw_spec = record.get("spec") or {}
        spec = BLSReleaseSpec(**raw_spec)
        if spec.family not in BLS_FAMILIES or (
            spec.family, spec.period, spec.published_at, spec.source_url
        ) not in schedule_specs:
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
    errors = dict(bundle.get("errors") or {})
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
        "research_only": True, "production_authority": False,
    }


def install_historical_offhost_transport() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .macro_bls_historical_bootstrap import BLSHistoricalBootstrapRuntime
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

    BLSHistoricalBootstrapRuntime.refresh = bls_refresh
    ISMHistoricalBootstrapRuntime.refresh = ism_refresh
    _INSTALLED = True


def transport_status() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "families": ["CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES"],
        "official_sources_only": True,
        "canonical_parser_exact_sha": True,
        "bundle_and_record_hashes_required": True,
        "network_fetch_on_production": False,
        "synthetic_data_used": False,
        "research_only": True,
        "production_authority": False,
    }
