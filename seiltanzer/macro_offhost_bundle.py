"""Verified off-host transport for official numeric macro releases.

The production host may be unable to reach otherwise-public official BLS/ISM
endpoints.  A GitHub runner may fetch those same official sources, run the exact
canonical parsers from the deployed SHA, and transfer one short-lived immutable
bundle over the existing authenticated deploy channel.  Production revalidates
the bundle SHA, exact code SHA, official hosts, timestamps and release payloads
before the normal append-only NumericMacroStore accepts anything.

This is transport only.  It adds no source, placeholder, consensus estimate,
historical reconstruction or production authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


CONTRACT_VERSION = "official-macro-offhost-bundle-v1"
HARD_MAX_AGE_SEC = 30.0 * 60.0
DEFAULT_MAX_AGE_SEC = 20.0 * 60.0
MAX_CLOCK_SKEW_SEC = 120.0
BUNDLE_PATH_ENV = "SEILTANZER_OFFHOST_MACRO_BUNDLE"
EXPECTED_FAMILIES = ("CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES")
OFFICIAL_HOSTS = {
    "CPI": frozenset({"api.bls.gov", "bls.gov", "www.bls.gov"}),
    "NFP": frozenset({"api.bls.gov", "bls.gov", "www.bls.gov"}),
    "ISM_MANUFACTURING": frozenset({"ismworld.org", "www.ismworld.org"}),
    "ISM_SERVICES": frozenset({"ismworld.org", "www.ismworld.org"}),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _without(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in mapping.items() if name != key}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bundle_max_age_sec() -> float:
    raw = os.environ.get("MACRO_OFFHOST_BUNDLE_MAX_AGE_SEC", "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_SEC
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_SEC
    if configured <= 0.0:
        return 0.0
    return min(configured, HARD_MAX_AGE_SEC)


def configured_bundle_path(runtime: Any | None = None) -> Path | None:
    override = os.environ.get(BUNDLE_PATH_ENV, "").strip()
    if override:
        return Path(override)
    candidate = getattr(runtime, "offhost_bundle_path", None)
    return Path(candidate) if candidate else None


def current_repository_sha() -> str | None:
    root = Path(__file__).resolve().parents[1]
    try:
        value = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _release_record(
    *,
    family: str,
    payload: dict[str, Any],
    source: str,
    source_url: str,
    fetched_at: float,
) -> dict[str, Any]:
    record = {
        "family": family,
        "period": str(payload.get("period") or ""),
        "source": source,
        "source_url": source_url,
        "fetched_at": float(fetched_at),
        "payload": payload,
        "provenance": {
            "transport": "OFF_HOST_OFFICIAL_SOURCE",
            "official_source_verified_by_builder": True,
            "canonical_parser_executed_by_exact_sha": True,
            "raw_or_parsed_values_synthesized": False,
            "placeholder_used": False,
            "acquisition": payload.get("acquisition"),
        },
    }
    record["canonical_release_sha256"] = _sha256(record)
    return record


def build_bundle(
    *, expected_sha: str, acceptance_run_id: str, now: float | None = None
) -> dict[str, Any]:
    """Fetch and canonically parse one complete official bundle off-host."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("OFFHOST_MACRO_EXPECTED_SHA_INVALID")
    if not re.fullmatch(r"[0-9]+", acceptance_run_id):
        raise ValueError("OFFHOST_MACRO_ACCEPTANCE_RUN_ID_INVALID")

    from .macro_ism_parser_refinement import install_ism_roundup_parser_refinement
    from .macro_ism_resilience import install_ism_source_resilience
    from .macro_numeric_data import BLS_API_URL, OfficialNumericMacroSource
    from .macro_transport_refinement import install_macro_transport_refinement

    install_macro_transport_refinement()
    install_ism_roundup_parser_refinement()
    install_ism_source_resilience()
    source = OfficialNumericMacroSource(timeout_sec=10.0)
    requested_at = time.time() if now is None else float(now)
    bls_fetched_at, bls = source.fetch_bls(now=requested_at)
    ism_fetched_at, ism = source.fetch_ism()
    releases: dict[str, dict[str, Any]] = {}
    for family in ("CPI", "NFP"):
        releases[family] = _release_record(
            family=family,
            payload=dict(bls[family]),
            source="U.S. Bureau of Labor Statistics",
            source_url=BLS_API_URL,
            fetched_at=bls_fetched_at,
        )
    for family in ("ISM_MANUFACTURING", "ISM_SERVICES"):
        payload = dict(ism[family])
        releases[family] = _release_record(
            family=family,
            payload=payload,
            source="Institute for Supply Management",
            source_url=str(payload.get("source_url") or ""),
            fetched_at=ism_fetched_at,
        )
    bundle = {
        "contract_version": CONTRACT_VERSION,
        "expected_sha": expected_sha,
        "acceptance_run_id": acceptance_run_id,
        "created_at": max(bls_fetched_at, ism_fetched_at),
        "families": list(EXPECTED_FAMILIES),
        "releases": releases,
        "official_sources_only": True,
        "canonical_parsers_only": True,
        "no_placeholders": True,
        "synthetic_data_used": False,
        "research_only": True,
        "production_authority": False,
    }
    bundle["bundle_sha256"] = _sha256(bundle)
    return bundle


def _validate_payload(family: str, payload: dict[str, Any]) -> None:
    if str(payload.get("family") or "") != family:
        raise ValueError("OFFHOST_MACRO_PAYLOAD_FAMILY_MISMATCH")
    if not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", str(payload.get("period") or "")):
        raise ValueError("OFFHOST_MACRO_PAYLOAD_PERIOD_INVALID")
    if payload.get("consensus_available") is not False:
        raise ValueError("OFFHOST_MACRO_CONSENSUS_NOT_ALLOWED")
    if payload.get("surprise_computed") is not False:
        raise ValueError("OFFHOST_MACRO_SURPRISE_NOT_ALLOWED")
    if family in {"CPI", "NFP"}:
        numeric = [
            value for key, value in payload.items()
            if key not in {"family", "period", "series", "consensus_available",
                           "surprise_computed"}
            and _finite(value) is not None
        ]
        if not numeric:
            raise ValueError("OFFHOST_MACRO_BLS_NUMERIC_FACTS_MISSING")
        return
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not isinstance(metrics.get("pmi"), dict):
        raise ValueError("OFFHOST_MACRO_ISM_PMI_MISSING")
    for name, row in metrics.items():
        if not isinstance(row, dict):
            raise ValueError("OFFHOST_MACRO_ISM_METRIC_INVALID")
        current = _finite(row.get("current"))
        previous = _finite(row.get("previous"))
        change = _finite(row.get("change_pp"))
        if current is None or previous is None or change is None:
            raise ValueError("OFFHOST_MACRO_ISM_METRIC_NONFINITE")
        # Official tables publish one-decimal values; tolerate only their possible
        # last-decimal rounding, never an unrelated or inferred delta.
        if abs((current - previous) - change) > 0.11:
            raise ValueError("OFFHOST_MACRO_ISM_CHANGE_MISMATCH")


def validate_bundle(
    bundle: dict[str, Any], *, expected_sha: str, acceptance_run_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Fail closed unless every release is fresh, official and hash-identical."""
    if not isinstance(bundle, dict) or bundle.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("OFFHOST_MACRO_CONTRACT_MISMATCH")
    if bundle.get("bundle_sha256") != _sha256(_without(bundle, "bundle_sha256")):
        raise ValueError("OFFHOST_MACRO_BUNDLE_HASH_MISMATCH")
    if bundle.get("expected_sha") != expected_sha:
        raise ValueError("OFFHOST_MACRO_EXACT_SHA_MISMATCH")
    if acceptance_run_id is not None and bundle.get("acceptance_run_id") != acceptance_run_id:
        raise ValueError("OFFHOST_MACRO_ACCEPTANCE_OWNER_MISMATCH")
    if bundle.get("official_sources_only") is not True or bundle.get("no_placeholders") is not True:
        raise ValueError("OFFHOST_MACRO_SOURCE_CONTRACT_MISMATCH")
    if bundle.get("canonical_parsers_only") is not True:
        raise ValueError("OFFHOST_MACRO_PARSER_CONTRACT_MISMATCH")
    if bundle.get("synthetic_data_used") is not False:
        raise ValueError("OFFHOST_MACRO_SYNTHETIC_DATA_FORBIDDEN")
    if bundle.get("research_only") is not True or bundle.get("production_authority") is not False:
        raise ValueError("OFFHOST_MACRO_AUTHORITY_MISMATCH")

    stamp = time.time() if now is None else float(now)
    max_age = bundle_max_age_sec()
    if max_age <= 0.0:
        raise ValueError("OFFHOST_MACRO_BUNDLE_DISABLED")
    created_at = _finite(bundle.get("created_at"))
    if created_at is None or created_at > stamp + MAX_CLOCK_SKEW_SEC:
        raise ValueError("OFFHOST_MACRO_BUNDLE_FUTURE_TIMESTAMP")
    if stamp - created_at > max_age:
        raise ValueError("OFFHOST_MACRO_BUNDLE_STALE")

    releases = bundle.get("releases")
    if bundle.get("families") != list(EXPECTED_FAMILIES):
        raise ValueError("OFFHOST_MACRO_FAMILY_MANIFEST_MISMATCH")
    if not isinstance(releases, dict) or set(releases) != set(EXPECTED_FAMILIES):
        raise ValueError("OFFHOST_MACRO_FAMILY_SET_MISMATCH")
    for family in EXPECTED_FAMILIES:
        record = releases[family]
        if not isinstance(record, dict):
            raise ValueError("OFFHOST_MACRO_RELEASE_INVALID")
        if record.get("canonical_release_sha256") != _sha256(
            _without(record, "canonical_release_sha256")
        ):
            raise ValueError("OFFHOST_MACRO_RELEASE_HASH_MISMATCH")
        if record.get("family") != family:
            raise ValueError("OFFHOST_MACRO_RELEASE_FAMILY_MISMATCH")
        provenance = record.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError("OFFHOST_MACRO_PROVENANCE_MISSING")
        if (
            provenance.get("transport") != "OFF_HOST_OFFICIAL_SOURCE"
            or provenance.get("official_source_verified_by_builder") is not True
            or provenance.get("canonical_parser_executed_by_exact_sha") is not True
            or provenance.get("raw_or_parsed_values_synthesized") is not False
            or provenance.get("placeholder_used") is not False
        ):
            raise ValueError("OFFHOST_MACRO_PROVENANCE_INVALID")
        parsed = urlparse(str(record.get("source_url") or ""))
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in OFFICIAL_HOSTS[family]:
            raise ValueError("OFFHOST_MACRO_UNOFFICIAL_SOURCE")
        fetched_at = _finite(record.get("fetched_at"))
        if fetched_at is None or fetched_at > stamp + MAX_CLOCK_SKEW_SEC:
            raise ValueError("OFFHOST_MACRO_RELEASE_FUTURE_TIMESTAMP")
        if stamp - fetched_at > max_age:
            raise ValueError("OFFHOST_MACRO_RELEASE_STALE")
        payload = record.get("payload")
        if not isinstance(payload, dict) or record.get("period") != payload.get("period"):
            raise ValueError("OFFHOST_MACRO_RELEASE_PAYLOAD_MISMATCH")
        _validate_payload(family, payload)
    return bundle


def load_verified_bundle(
    runtime: Any, *, now: float | None = None
) -> dict[str, Any]:
    path = configured_bundle_path(runtime)
    if path is None or not path.is_file():
        raise ValueError("OFFHOST_MACRO_BUNDLE_MISSING")
    expected_sha = current_repository_sha()
    if expected_sha is None:
        raise ValueError("OFFHOST_MACRO_RUNTIME_SHA_UNAVAILABLE")
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("OFFHOST_MACRO_BUNDLE_UNREADABLE") from exc
    return validate_bundle(bundle, expected_sha=expected_sha, now=now)


def ingest_offhost_families(
    runtime: Any, families: Iterable[str], *, upstream_error: Exception,
    now: float | None = None,
) -> list[dict[str, Any]]:
    from .macro_numeric_data import ReleaseWrite

    bundle = load_verified_bundle(runtime, now=now)
    output: list[dict[str, Any]] = []
    for family in families:
        record = bundle["releases"][family]
        payload = dict(record["payload"])
        transport_provenance = {
            "contract_version": CONTRACT_VERSION,
            "bundle_sha256": bundle["bundle_sha256"],
            "expected_sha": bundle["expected_sha"],
            "acceptance_run_id": bundle["acceptance_run_id"],
            "canonical_release_sha256": record["canonical_release_sha256"],
            "fetched_at": record["fetched_at"],
            "source_url": record["source_url"],
            "fallback_reason": "PRODUCTION_OFFICIAL_TRANSPORT_UNAVAILABLE",
            "upstream_error": f"{type(upstream_error).__name__}:{str(upstream_error)[:160]}",
            "synthetic_data_used": False,
            "production_authority": False,
        }
        stored = runtime.store.ingest(ReleaseWrite(
            family=family,
            period=str(record["period"]),
            source=str(record["source"]),
            source_url=str(record["source_url"]),
            fetched_at=float(record["fetched_at"]),
            payload=payload,
        ))
        stored["transport"] = "OFF_HOST_OFFICIAL_SOURCE"
        stored["bundle_sha256"] = bundle["bundle_sha256"]
        stored["transport_provenance"] = transport_provenance
        output.append(stored)
    return output


def write_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_canonical_json(bundle), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
