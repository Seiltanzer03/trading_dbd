"""Bound operational expert inputs without changing their causal semantics.

These profiles are consumed after snapshot caching and therefore are decision
inputs, even when an older report called them presentation-only. Oversized
operational payloads become explicitly unavailable; truncation never invents a
fresh timestamp, quality, role or independent information source.
"""
from __future__ import annotations

from copy import deepcopy
import json

PROFILE_KEYS = ("mathematical_edge", "active_edge_provisional_weight", "llm_edge_exploratory_weight")
SCALARS = (
    "contract_version", "available", "reason", "instrument", "regime", "market_regime",
    "model_version", "model_sha256", "status", "role", "direction_score",
    "weight_fraction", "max_weight_fraction", "quality", "quality_multiplier",
    "observed_ts", "latest_bar_end_ts", "captured_ts", "training_cutoff", "max_age_sec",
    "base_policy_eligible", "working_eligible", "formal_eligible", "horizon_minutes",
    "training_age_days", "automatic_execution_source", "hard_risk_modified",
    "independent_evidence_vote", "ranking_only", "net_economic_proof",
    "preferred_close_fraction", "eligible_policies", "eligible_extended_roles",
    "supported_regimes", "regime_contract_version",
    "evidence_family_ids", "source_ids", "applicability", "path_target_applicability",
)
PATH_KEYS = ("probability", "baseline_probability", "horizon_minutes", "target_semantics",
    "quality_multiplier", "training_age_days", "training_cutoff", "captured_ts", "observed_ts",
    "max_effective_weight_fraction", "test_n", "gain_mbit", "ranking_only",
    "net_economic_proof", "independent_evidence_vote", "intrabar_order_inferred",
    "generic_barriers_are_trade_stop_take")
PATH_NAMES = ("downside_excursion", "upside_excursion", "upper_before_lower", "early_first_touch")
FAMILY_ROOTS = ("edge_family_sources", "edge_family_models", "macro_context_v1", "macro_t0_context")
LINEAGE_KEYS = ("adverse_confirmation_families", "supportive_confirmation_families",
                "adverse_confirmations", "supportive_contradictions", "context_observations")
EXCLUDED_REASON = "EXCLUDED_BY_SNAPSHOT_BYTE_BUDGET"


def _bytes(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode())
    except (TypeError, ValueError):
        return float("inf")


def compact_operational_profile(value):
    if not isinstance(value, dict):
        return {}
    if value.get("reason") == EXCLUDED_REASON:
        return {"available": False, "quality": 0., "quality_multiplier": 0.,
                "reason": EXCLUDED_REASON, "contract_version": "operational-edge-budget-v1"}
    result = {key: deepcopy(value[key]) for key in SCALARS if key in value}
    heads = value.get("path_predictions")
    if isinstance(heads, dict):
        result["path_predictions"] = {name: {key: deepcopy(heads[name][key])
            for key in PATH_KEYS if key in heads[name]}
            for name in PATH_NAMES if isinstance(heads.get(name), dict)}
    # Preserve small explanatory counters used by existing reports too.
    for key, item in value.items():
        if key not in result and (item is None or isinstance(item, (bool, int, float))
                                or isinstance(item, str) and len(item) <= 256):
            result[key] = item
    if _bytes(result) > 6000:
        return {"available": False, "quality": 0., "quality_multiplier": 0.,
                "reason": EXCLUDED_REASON, "contract_version": "operational-edge-budget-v1"}
    # Small probability/validation views substantiate the displayed forecast.
    # Large diagnostics remain removable explanations, never operational votes.
    for key in ("probabilities", "baseline_probabilities", "feature_values", "diagnostics", "h2"):
        item = value.get(key)
        if isinstance(item, dict) and _bytes(item) <= 2000 and _bytes({**result, key: item}) <= 6000:
            result[key] = deepcopy(item)
    return result


def compact_operational_edges(snapshot):
    """Project profiles; preserve bounded exact family inputs or name exclusion."""
    manager = snapshot.get("policy_manager")
    if "edge_regime" in snapshot and _bytes(snapshot["edge_regime"]) > 4000:
        snapshot["edge_regime"] = {"regime": "UNKNOWN", "available": False,
                                   "reason": EXCLUDED_REASON}
        snapshot["market_regime"] = "UNKNOWN"
        if isinstance(manager, dict):
            manager["market_regime"] = "UNKNOWN"
    if isinstance(manager, dict):
        for key in PROFILE_KEYS:
            if key in manager:
                manager[key] = compact_operational_profile(manager[key])
    prior = snapshot.get("edge_family_budget_status")
    status = deepcopy(prior) if isinstance(prior, dict) else {
        "contract_version": "edge-family-byte-budget-v1", "excluded_roots": [], "retained_roots": []}
    retained = []
    # Exact bounded roots avoid altering model coefficients, validation or PIT
    # availability. Large inputs are excluded as a whole, never partly fitted.
    total = 0
    for key in FAMILY_ROOTS:
        if key not in snapshot:
            continue
        size = _bytes(snapshot[key])
        if size > 8000 or total+size > 16000:
            snapshot.pop(key)
            status.setdefault("excluded_roots", []).append(key)
        else:
            total += size
            retained.append(key)
    if status.get("excluded_roots") or prior:
        status["excluded_roots"] = sorted(set(status.get("excluded_roots", [])))
        status["retained_roots"] = retained
        status["reason"] = EXCLUDED_REASON if status["excluded_roots"] else "BOUNDED_OPERATIONAL_INPUTS_PRESERVED"
        snapshot["edge_family_budget_status"] = status


def compact_evidence_lineage(evidence):
    """Preserve observed identities used to verify LLM independence claims."""
    if not isinstance(evidence, dict):
        return {}
    result = {}
    marker = evidence.get("lineage_budget_status")
    if isinstance(marker, dict) and marker.get("reason") == EXCLUDED_REASON:
        result["lineage_budget_status"] = {"available": False, "reason": EXCLUDED_REASON,
                                          "partial_lineage_retained": False}
    for key in ("adverse_confirmation_families", "supportive_confirmation_families"):
        if isinstance(evidence.get(key), list):
            result[key] = deepcopy(evidence[key])
    for key in ("adverse_confirmations", "supportive_contradictions", "context_observations"):
        if isinstance(evidence.get(key), list):
            result[key] = [{field: row[field] for field in ("family", "available") if field in row}
                           for row in evidence[key] if isinstance(row, dict)]
    if _bytes(evidence.get("data_quality")) <= 2000:
        if "data_quality" in evidence:
            result["data_quality"] = deepcopy(evidence["data_quality"])
    if _bytes(result) > 6000:
        # Exact information sets matter; partial lineage would change weights.
        return {"lineage_budget_status": {"available": False, "reason": EXCLUDED_REASON,
                                          "partial_lineage_retained": False}}
    return result
