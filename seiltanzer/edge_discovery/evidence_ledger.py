"""Frozen, append-only EDE evidence made available to causal AI snapshots.

The ledger is written only by the read-only production audit.  Runtime readers
select records by both their materialization time and their source-data cutoff,
so a later resolution can never leak into an older management snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from .maturity import data_maturity
from .registry import FEATURES
from .filters import CandidateTemplate, ConditionTemplate, fit_rule


CONTRACT_VERSION = "g1s-ede-frozen-evidence-v1.2.2"
DEPLOYMENT_RULE_VERSION = "ede-post-validation-causal-refit-v1"
HORIZONS = (15, 30, 60, 120, 240)
FAMILIES = (
    "PRICE", "VOLATILITY", "OPTIONS", "OPTION_DYNAMICS",
    "CROSS_ASSET", "REGIME",
)
MATURITY_RANK = {
    "INSUFFICIENT_DATA": 0,
    "DATA_READY_EARLY": 1,
    "DATA_READY_RESEARCH": 2,
    "DATA_READY_PROVISIONAL": 3,
    "DATA_READY_ROBUST": 4,
}
EDGE_RANK = {
    "INSUFFICIENT_DATA": 0,
    "RESEARCH_SIGNAL": 1,
    "PROVISIONAL_EDGE": 2,
    "ROBUST_EDGE": 3,
}

# Historical discovery templates used these short names. They remain valid
# validation/debug identities, but every deployable refit is canonicalized to
# the exact feature IDs stored by ProspectiveFeatureAdapter.
CANONICAL_FEATURE_ID = {
    "asset": "regime.asset",
    "asset_family": "regime.asset_family",
    "session_utc": "regime.session_utc",
    "rv15_over_rv60": "vol.rv15_over_rv60",
    "trend_efficiency_60": "price.trend_efficiency_60",
    "cross_confirmation": "cross.confirmation",
}


def evidence_ledger_path(engine: Any) -> Path:
    override = os.environ.get("SEILTANZER_EDE_EVIDENCE_LEDGER")
    if override:
        return Path(override)
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research" / "ede_frozen_evidence.jsonl"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def family_data_maturity(inventory: dict[str, Any]) -> dict[str, Any]:
    """Summarize each family exclusively from observations of that family.

    The most mature member feature is the conservative auditable basis for each
    family/horizon summary; counts are never mixed across features or families.
    """
    family_by_feature = {
        definition.feature_id: definition.family
        for definition in FEATURES
        if definition.research_scope == "G1S" and definition.training_eligibility
    }
    features: dict[str, list[dict[str, Any]]] = {name: [] for name in FAMILIES}
    for feature in inventory.get("features") or []:
        family = family_by_feature.get(str(feature.get("feature_id")))
        if family in features:
            features[family].append(feature)
    output: dict[str, Any] = {}
    for family in FAMILIES:
        horizons: dict[str, Any] = {}
        for horizon in HORIZONS:
            rows = []
            for feature in features[family]:
                row = (feature.get("by_horizon") or {}).get(str(horizon)) or {}
                resolved = int(row.get("resolved") or 0)
                effective = int(row.get("effective") or 0)
                blocks = int(row.get("temporal_blocks") or 0)
                status = data_maturity(
                    raw_n=resolved, effective_n=effective, temporal_blocks=blocks)
                rows.append((feature.get("feature_id"), row, status))
            basis_feature, basis, status = max(
                rows,
                key=lambda item: (
                    MATURITY_RANK[item[2]], int(item[1].get("resolved") or 0),
                    int(item[1].get("effective") or 0)),
                default=(None, {}, "INSUFFICIENT_DATA"))
            horizons[str(horizon)] = {
                "raw": int(basis.get("raw") or 0),
                "resolved": int(basis.get("resolved") or 0),
                "effective": int(basis.get("effective") or 0),
                "temporal_blocks": int(basis.get("temporal_blocks") or 0),
                "coverage_pct": round(float(basis.get("coverage_pct") or 0.0), 6),
                "data_maturity": status,
                "basis_feature_id": basis_feature,
            }
        summary = max(
            (row["data_maturity"] for row in horizons.values()),
            key=lambda name: MATURITY_RANK[name], default="INSUFFICIENT_DATA")
        output[family] = {
            "data_maturity": summary,
            "horizons": horizons,
            "feature_count": len(features[family]),
        }
    return output


def _candidate_families(candidate: dict[str, Any]) -> list[str]:
    family_by_feature = {
        definition.feature_id: definition.family for definition in FEATURES}
    family_by_feature.update({
        "asset": "REGIME", "asset_family": "REGIME", "session_utc": "REGIME",
        "rv15_over_rv60": "VOLATILITY", "trend_efficiency_60": "PRICE",
        "cross_confirmation": "CROSS_ASSET",
        "family_breadth_state": "CROSS_ASSET",
    })
    return sorted({
        family_by_feature.get(str(condition.get("feature_id")), "UNKNOWN")
        for condition in candidate.get("conditions") or []
    } - {"UNKNOWN"})


def _deployment_refit(candidate: dict[str, Any], rows: list[dict[str, Any]], *,
                      evidence_cutoff_ts: float, fitted_at: float,
                      dataset_sha256: str) -> dict[str, Any] | None:
    """Fit a live rule after validation without touching validation metrics."""
    template_conditions = candidate.get("template") or []
    canonical: list[ConditionTemplate] = []
    for condition in template_conditions:
        feature_id = CANONICAL_FEATURE_ID.get(
            str(condition.get("feature_id")), str(condition.get("feature_id")))
        # This virtual display category has no canonical immutable live feature.
        if feature_id == "family_breadth_state":
            return None
        canonical.append(ConditionTemplate(
            feature_id=feature_id, kind=str(condition.get("kind")),
            state=str(condition.get("state"))))
    if not canonical:
        return None
    horizon = int(candidate.get("horizon_minutes") or 0)
    training_rows = [
        row for row in rows
        if int(row.get("horizon_minutes") or 0) == horizon
        and float(row.get("captured_ts") or 0.0) <= evidence_cutoff_ts + 1e-6
        and row.get("resolved_ts") is not None
        and float(row["resolved_ts"]) <= evidence_cutoff_ts + 1e-6
    ]
    rule = fit_rule(CandidateTemplate(tuple(canonical)), training_rows)
    if rule is None:
        return None
    fitted = rule.as_dict()
    training_cutoff = max(
        (float(row["captured_ts"]) for row in training_rows), default=None)
    if training_cutoff is None or training_cutoff > evidence_cutoff_ts + 1e-6:
        return None
    return {
        "candidate_id": candidate.get("candidate_id"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "deployment_rule": fitted["conditions"],
        "feature_ids": [condition.feature_id for condition in canonical],
        "thresholds_or_categories": [{
            "feature_id": row["feature_id"], "kind": row["kind"],
            "state": row["state"], "lower": row.get("lower"),
            "upper": row.get("upper"),
        } for row in fitted["conditions"]],
        "fitted_at": float(fitted_at),
        "training_cutoff": training_cutoff,
        "evidence_cutoff_ts": float(evidence_cutoff_ts),
        "dataset_hash": str(dataset_sha256),
        "rule_version": DEPLOYMENT_RULE_VERSION,
        "provenance": "POST_VALIDATION_CAUSAL_REFIT",
        "validation_metrics_recomputed": False,
    }


def compact_primary_candidate(
        candidate: dict[str, Any], *, deployment_refit: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    maturity = str(candidate.get("edge_maturity") or "INSUFFICIENT_DATA")
    if maturity not in EDGE_RANK or maturity == "INSUFFICIENT_DATA":
        return None
    if candidate.get("aggregate_scope") != "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY":
        return None
    primary_folds = int(candidate.get("inner_primary_folds") or 0)
    if primary_folds <= 0 or not candidate.get("primary_only_aggregate"):
        return None
    folds_evaluated = int(candidate.get("folds_evaluated") or 0)
    q_value = _finite(candidate.get("q_value"))
    if maturity == "PROVISIONAL_EDGE" and (
            primary_folds < 2 or folds_evaluated < 2
            or q_value is None or q_value > 0.10):
        return None
    if maturity == "ROBUST_EDGE" and (
            primary_folds < 4 or folds_evaluated != 4
            or q_value is None or q_value > 0.10):
        return None
    comparison = candidate.get("global_ret5_comparison") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "horizon_minutes": int(candidate.get("horizon_minutes") or 0),
        "validation_conditions": candidate.get("conditions") or [],
        "deployment_refit": deployment_refit,
        "edge_maturity": maturity,
        "delta_brier": _finite(comparison.get("brier_delta")),
        "delta_logloss": _finite(comparison.get("logloss_delta")),
        "q_value": q_value,
        "primary_folds": primary_folds,
        "folds_evaluated": folds_evaluated,
        "folds_positive": int(candidate.get("folds_positive") or 0),
        "directional_evidence": (
            "SUPPORTS_PERSISTENCE" if candidate.get("where_it_helps")
            else "HURTS_PERSISTENCE" if candidate.get("where_it_hurts")
            else "MIXED"),
        "feature_families": _candidate_families(candidate),
        "aggregate_scope": "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY",
    }


def build_frozen_evidence(*, inventory: dict[str, Any], discovery: dict[str, Any],
                          dataset_sha256: str, evidence_cutoff_ts: float,
                          frozen_at: float,
                          prospective_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidates = []
    for horizon in discovery.get("horizons") or []:
        for candidate in horizon.get("candidates") or []:
            compact = compact_primary_candidate(candidate)
            if compact is None:
                continue
            refit = _deployment_refit(
                candidate, prospective_rows or [],
                evidence_cutoff_ts=evidence_cutoff_ts, fitted_at=frozen_at,
                dataset_sha256=dataset_sha256)
            compact["deployment_refit"] = refit
            candidates.append(compact)
    candidates.sort(key=lambda row: (
        -EDGE_RANK[row["edge_maturity"]],
        -(float(row.get("delta_brier") or 0.0)
          + float(row.get("delta_logloss") or 0.0)),
        str(row.get("candidate_id"))),
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "frozen_at": float(frozen_at),
        "evidence_cutoff_ts": float(evidence_cutoff_ts),
        "dataset_sha256": str(dataset_sha256),
        "family_data_maturity": family_data_maturity(inventory),
        "edge_candidates": candidates[:20],
        "edge_maturity": (
            candidates[0]["edge_maturity"] if candidates else "INSUFFICIENT_DATA"),
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }


def _identity(record: dict[str, Any]) -> str:
    raw = json.dumps({
        "contract_version": record.get("contract_version"),
        "dataset_sha256": record.get("dataset_sha256"),
        "evidence_cutoff_ts": record.get("evidence_cutoff_ts"),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def append_frozen_evidence(path: Path, record: dict[str, Any]) -> bool:
    """Append one immutable record; identical datasets are idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = _identity(record)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    existing = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if _identity(existing) == identity:
                    return False
    payload = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def latest_frozen_evidence(path: Path, cutoff_ts: float) -> dict[str, Any] | None:
    """Return the newest record wholly knowable at ``cutoff_ts``."""
    path = Path(path)
    if not path.exists():
        return None
    eligible: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
                frozen_at = float(record["frozen_at"])
                evidence_cutoff = float(record["evidence_cutoff_ts"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if frozen_at <= cutoff_ts + 1e-6 and evidence_cutoff <= cutoff_ts + 1e-6:
                eligible.append(record)
    return max(eligible, key=lambda row: float(row["frozen_at"]), default=None)
