"""Admission boundary between discovery output and immutable validation registry.

A historical discovery label is not validation and never production authority.
Only already-qualified structured/ML discovery signals may enter the frozen
prospective lifecycle; diagnostics fail closed.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


ADMISSION_CONTRACT_VERSION = "g1s-ede-validation-admission-v1"
ADMISSIBLE_DISCOVERY_STATUSES = {"DISCOVERY_SIGNAL", "ML_DISCOVERY_SIGNAL"}


def admit_discovery_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict) or not candidate:
        raise ValueError("discovery candidate must be a non-empty mapping")
    source_status = str(candidate.get("status") or "")
    if source_status not in ADMISSIBLE_DISCOVERY_STATUSES:
        raise ValueError(
            f"candidate is not an admissible discovery signal: {source_status}")
    if candidate.get("production_authority") is not False:
        raise ValueError(
            "discovery candidate must explicitly have no production authority")
    if candidate.get("auto_promotion") is not False:
        raise ValueError(
            "discovery candidate must explicitly disable auto promotion")
    if not candidate.get("candidate_id"):
        raise ValueError("discovery candidate has no immutable candidate_id")
    if not candidate.get("target_id"):
        raise ValueError("universal discovery candidate has no target_id")
    if int(candidate.get("horizon_minutes") or 0) <= 0:
        raise ValueError("discovery candidate has no valid horizon")

    output = deepcopy(candidate)
    output["source_discovery_status"] = source_status
    output["source_discovery_contract"] = str(
        candidate.get("contract_version")
        or candidate.get("measurement_contract")
        or "UNKNOWN")
    output["admission_contract_version"] = ADMISSION_CONTRACT_VERSION
    output["status"] = "HISTORICAL_CANDIDATE"
    output["evidence_label"] = "HISTORICAL_WALK_FORWARD"
    output["prospective_confirmation"] = False
    output["production_authority"] = False
    output["auto_promotion"] = False
    return output
