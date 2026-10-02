"""Bounded local, exact-code-generation reads of off-host source facts.

No HTTP, fitting, fabricated models or proxy promotion on the review path.
Unsupported records stay in the source audit, not in scoring/prompt inputs.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path

from .canonical_market_context import canonical_instrument_code
from .edge_family_adapters import FAMILIES, _meta, build_edge_family_evidence

CONTRACT = "edge-family-source-bundle-v1"
PUBLICATION = "active-edge-exact-sha-publication-v1"
POLICY = "g1s-manual-trader-high-risk-edge-policy-v1"
MAX_BYTES = 1_000_000
MAX_SELECTED_BYTES = 8_000
MAX_AGE_SEC = 8 * 60 * 60


@lru_cache(maxsize=4)
def _read(path, modified_ns, size):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("SOURCE_BUNDLE_EXCEEDS_BOUND")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("SOURCE_BUNDLE_NOT_OBJECT")
    return payload


def load_family_source_context(engine, snapshot, expected_sha):
    """Return frozen admitted facts and a small availability audit, never models."""
    audit = {"contract_version": CONTRACT, "available": False,
             "reason": "SOURCE_BUNDLE_UNAVAILABLE", "network_calls": False,
             "runtime_fitting": False, "models_loaded": 0, "rejected_sources": []}
    result = {"edge_family_sources": {}, "edge_family_source_bundle_audit": audit}
    strategy = snapshot.get("strategy") or {}
    instrument = canonical_instrument_code(strategy.get("instrument") or snapshot.get("instrument"))
    cutoff = snapshot.get("captured_ts")
    if (not isinstance(cutoff, (int, float)) or isinstance(cutoff, bool)
            or not math.isfinite(cutoff) or cutoff <= 0):
        audit["reason"] = "SOURCE_SNAPSHOT_TIME_UNAVAILABLE"
        return result
    if (not isinstance(expected_sha, str) or len(expected_sha) != 40
            or any(char not in "0123456789abcdef" for char in expected_sha)):
        audit["reason"] = "RUNTIME_SHA_UNAVAILABLE"
        return result
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    path = data_dir / "research" / "edge_family_sources_latest.json"
    try:
        stat = path.stat()
        if not 0 < stat.st_size <= MAX_BYTES:
            raise ValueError("SOURCE_BUNDLE_EXCEEDS_BOUND")
        if not 0 <= cutoff - stat.st_mtime <= MAX_AGE_SEC:
            raise ValueError("SOURCE_BUNDLE_FILE_STALE_OR_AFTER_SNAPSHOT")
        payload = _read(str(path), stat.st_mtime_ns, stat.st_size)
        if (payload.get("contract_version") != CONTRACT
                or payload.get("edge_policy") != POLICY
                or payload.get("production_authority") is not False
                or payload.get("publication_contract_version") != PUBLICATION
                or payload.get("published_for_sha") != expected_sha):
            raise ValueError("SOURCE_BUNDLE_CODE_GENERATION_OR_CONTRACT_MISMATCH")
        capture = payload.get("captured_ts")
        if not isinstance(capture, (int, float)) or isinstance(capture, bool) or not 0 <= cutoff - capture <= MAX_AGE_SEC:
            raise ValueError("SOURCE_BUNDLE_CAPTURE_STALE_OR_AFTER_SNAPSHOT")
        row = (payload.get("instruments") or {}).get(instrument)
        if not isinstance(row, dict):
            raise ValueError("SOURCE_BUNDLE_INSTRUMENT_UNAVAILABLE")
        sources = row.get("edge_family_sources") or {}
        selected = {}
        for family in FAMILIES:
            records = sources.get(family) or []
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list) or len(records) > 128:
                raise ValueError("SOURCE_FAMILY_RECORD_BOUND_EXCEEDED")
            for record in records:
                if not isinstance(record, dict):
                    continue
                # An immutable bundle cannot know something received after its
                # own capture, even if that fact predates the later review.
                meta, reason = _meta(record, family=family, instrument=instrument,
                                     cutoff=capture, allow_global=family in {"macro", "event"})
                if meta is not None:
                    meta, reason = _meta(record, family=family, instrument=instrument,
                                         cutoff=cutoff, allow_global=family in {"macro", "event"})
                if meta is None:
                    audit["rejected_sources"].append({"family": family,
                        "source_id": str(record.get("source_id") or "")[:128], "reason": reason})
                    continue
                # Preserve only causally valid scoring facts. Unsupported future
                # schedules are audit-only; they cannot poison canonical snapshots.
                from .decision_research import validate_no_future_timestamps
                try:
                    validate_no_future_timestamps({"source": record}, capture, tolerance_sec=0)
                except ValueError:
                    audit["rejected_sources"].append({"family": family,
                        "source_id": str(record.get("source_id") or "")[:128],
                        "reason": "FUTURE_FACT_OR_UNSUPPORTED_PLANNED_SCHEDULE"})
                    continue
                selected.setdefault(family, []).append(deepcopy(record))
        if len(json.dumps(selected, allow_nan=False).encode()) > MAX_SELECTED_BYTES:
            raise ValueError("SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET")
        readiness = row.get("readiness") or {}
        current = build_edge_family_evidence({"instrument": instrument,
            "captured_ts": cutoff, "edge_family_sources": selected})["families"]
        audit["families"] = {family: {
            "collected_available": bool((readiness.get(family) or {}).get("available")),
            "available": bool(current[family].get("available")),
            "forecast_available": False,
            "reason": str(current[family].get("reason") or "NO_ADMISSIBLE_SOURCE")[:200],
            "needs_data": [str(item)[:100] for item in
                           (readiness.get(family) or {}).get("needs_data", [])[:4]],
            "loaded_record_n": len(selected.get(family, [])),
        } for family in FAMILIES}
        audit["rejected_sources"] = audit["rejected_sources"][:16]
        audit.update(available=True, reason="EXACT_SHA_LOCAL_SOURCE_FACTS",
                     instrument=instrument, bundle_captured_ts=capture,
                     source_record_n=sum(len(records) for records in selected.values()))
        result["edge_family_sources"] = selected
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        audit["reason"] = str(exc)[:160] if isinstance(exc, ValueError) else "SOURCE_BUNDLE_UNAVAILABLE"
        result["edge_family_sources"] = {}
    return result
