"""Deterministic family ablation over the bounded prospective candidate map."""
from __future__ import annotations

from typing import Any

from .registry import FEATURES


ABLATION_CONTRACT_VERSION = "g1s-ede-family-ablation-v1.2"
_FAMILY = {item.feature_id: item.family for item in FEATURES}
_ALIASES = {
    "asset": "REGIME", "asset_family": "REGIME", "session_utc": "REGIME",
    "rv15_over_rv60": "VOLATILITY", "trend_efficiency_60": "PRICE",
    "cross_confirmation": "CROSS_ASSET", "family_breadth_state": "CROSS_ASSET",
}
_MATURITY_RANK = {
    "INSUFFICIENT_DATA": 0, "EARLY_CONTEXT": 1, "RESEARCH_SIGNAL": 2,
    "PROVISIONAL_EDGE": 3, "ROBUST_EDGE": 4,
}


def _families(candidate: dict[str, Any]) -> set[str]:
    return {
        _FAMILY.get(str(item.get("feature_id")), _ALIASES.get(
            str(item.get("feature_id")), "UNKNOWN"))
        for item in candidate.get("conditions") or []
    }


def _belongs(name: str, families: set[str]) -> bool:
    base = {"PRICE", "REGIME"}
    if name == "PRICE_ONLY":
        return bool(families) and families <= base
    if name == "PRICE_VOL":
        return "VOLATILITY" in families and families <= base | {"VOLATILITY"}
    if name == "PRICE_CROSS":
        return "CROSS_ASSET" in families and families <= base | {"CROSS_ASSET"}
    if name == "PRICE_OPTIONS":
        return "OPTIONS" in families and families <= base | {"OPTIONS"}
    if name == "PRICE_OPTION_DYNAMICS":
        return "OPTION_DYNAMICS" in families and families <= base | {"OPTION_DYNAMICS"}
    if name == "PRICE_OPTIONS_CROSS":
        return ("CROSS_ASSET" in families
                and bool(families & {"OPTIONS", "OPTION_DYNAMICS"})
                and families <= base | {"OPTIONS", "OPTION_DYNAMICS", "CROSS_ASSET"})
    return name == "FULL_AVAILABLE_CONTEXT"


def family_ablation(report: dict[str, Any]) -> dict[str, Any]:
    names = (
        "PRICE_ONLY", "PRICE_VOL", "PRICE_CROSS", "PRICE_OPTIONS",
        "PRICE_OPTION_DYNAMICS", "PRICE_OPTIONS_CROSS", "FULL_AVAILABLE_CONTEXT",
    )
    candidates = [candidate for horizon in report.get("horizons") or []
                  for candidate in horizon.get("candidates") or []]
    output: dict[str, Any] = {}
    for name in names:
        items = [item for item in candidates if _belongs(name, _families(item))]
        items.sort(key=lambda item: (
            -_MATURITY_RANK.get(str(item.get("edge_maturity")), 0),
            -float((item.get("edge_score") or {}).get("score") or -1e9),
            str(item.get("candidate_id"))))
        best = items[0] if items else None
        output[name] = {
            "candidate_count": len(items),
            "best_candidate_id": best.get("candidate_id") if best else None,
            "horizon_minutes": best.get("horizon_minutes") if best else None,
            "data_maturity": best.get("data_maturity") if best else "INSUFFICIENT_DATA",
            "edge_maturity": best.get("edge_maturity") if best else "INSUFFICIENT_DATA",
            "evidence_maturity": best.get("edge_maturity") if best else "INSUFFICIENT_DATA",
            "delta_brier": ((best.get("global_ret5_comparison") or {}).get("brier_delta")
                            if best else None),
            "delta_logloss": ((best.get("global_ret5_comparison") or {}).get("logloss_delta")
                              if best else None),
            "relative_brier_improvement": ((best.get("improvement") or {}).get("brier")
                                           if best else None),
            "relative_logloss_improvement": ((best.get("improvement") or {}).get("logloss")
                                             if best else None),
            "q": best.get("q_value") if best else None,
            "raw_n": best.get("raw_n") if best else 0,
            "effective_n": best.get("effective_n") if best else 0,
            "temporal_stability": ({
                "folds_positive": best.get("folds_positive"),
                "folds_evaluated": best.get("folds_evaluated"),
            } if best else None),
            "edge_claim_allowed": bool(
                best and best.get("edge_maturity") == "ROBUST_EDGE"),
        }
    option_groups = ("PRICE_OPTIONS", "PRICE_OPTION_DYNAMICS", "PRICE_OPTIONS_CROSS")
    output["options_incremental_edge_summary"] = {
        "positive_early_or_better": any(
            output[name]["edge_maturity"] in {
                "RESEARCH_SIGNAL", "PROVISIONAL_EDGE", "ROBUST_EDGE"}
            and float(output[name]["relative_brier_improvement"] or 0.0) > 0.0
            and float(output[name]["relative_logloss_improvement"] or 0.0) > 0.0
            for name in option_groups),
        "validated_edge_claim": any(
            output[name]["edge_maturity"] == "ROBUST_EDGE"
            for name in option_groups),
    }
    return {
        "contract_version": ABLATION_CONTRACT_VERSION,
        "groups": output,
        "same_primary_baseline": "GLOBAL_RET5_PERSISTENCE",
        "bounded_templates_only": True,
        "production_authority": False,
        "auto_promotion": False,
    }
