"""Compact G.1S/G.1-M.1 evidence verdict required by the phase acceptance spec."""
from __future__ import annotations

from typing import Any

from .g1_short_horizon_runtime import HORIZONS, ShortHorizonRuntime, _finite


FINAL_REPORT_VERSION = "g1s-final-evidence-report-v2"
ECONOMIC_VALIDATION_MIN_TRADES = 20


def _best_probability_item(report: dict[str, Any]) -> dict[str, Any] | None:
    best = None
    best_delta = None
    for item in report.get("items", []):
        oos = item.get("oos") or {}
        model_brier = _finite(oos.get("brier"))
        baselines = oos.get("baselines") or {}
        baseline_values = [
            _finite(value.get("brier"))
            for value in baselines.values()
            if isinstance(value, dict)
        ]
        baseline_values = [value for value in baseline_values if value is not None]
        if model_brier is None or not baseline_values:
            continue
        delta = min(baseline_values) - model_brier
        if best_delta is None or delta > best_delta:
            best_delta = delta
            calibration = item.get("calibration_oos") or {}
            best = {
                "model_id": item.get("model_id"),
                "model_family": item.get("model_family"),
                "feature_set": item.get("feature_set"),
                "horizon_minutes": item.get("horizon_minutes"),
                "raw_n": oos.get("raw_n"),
                "effective_n": oos.get("effective_n"),
                "brier": model_brier,
                "delta_brier_vs_best_baseline": delta,
                "raw_oos_verdict": item.get("does_model_beat_baseline_oos"),
                "selected_probability_representation": calibration.get(
                    "selected_probability_representation"
                ),
                "best_representation_oos_verdict": calibration.get(
                    "does_best_probability_representation_beat_baselines_oos"
                ),
                "candidate_blockers": item.get("oos_candidate_blockers")
                or oos.get("candidate_blockers")
                or [],
            }
    return best


def _management_economics(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    local = getattr(runtime.engine, "management_local", None)
    if local is None:
        return {
            "status": "INSUFFICIENT",
            "reason": "G1M_LOCAL_RUNTIME_UNAVAILABLE",
            "unique_trades": 0,
            "mean_mva_vs_hold_r": None,
        }
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT w.trade_id,AVG(o.production_mva_vs_hold_r) AS trade_mean_mva
            FROM g1m_local_windows w JOIN g1m_local_outcomes o USING(window_id)
            WHERE w.evidence_eligible=1
            GROUP BY w.trade_id ORDER BY w.trade_id
        """).fetchall()
    values = [
        float(row["trade_mean_mva"])
        for row in rows
        if _finite(row["trade_mean_mva"]) is not None
    ]
    if len(values) < ECONOMIC_VALIDATION_MIN_TRADES:
        status = "INSUFFICIENT"
    elif sum(values) / len(values) < -1e-12:
        status = "CONTRADICTED"
    else:
        status = "NOT_WORSE"
    return {
        "status": status,
        "unique_trades": len(values),
        "required_unique_trades": ECONOMIC_VALIDATION_MIN_TRADES,
        "mean_mva_vs_hold_r": sum(values) / len(values) if values else None,
        "unit_of_independence": "unique_trade_mean_across_local_horizons",
        "terminal_edge_separate": True,
    }


def _trade_economics(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    report = runtime.trade_relevance()
    n = int(report.get("unique_trades_with_pre_entry_prediction") or 0)
    delta = _finite(report.get("delta_brier_vs_0_5"))
    winners = _finite(report.get("mean_p_move_with_trade_on_winning_trades"))
    nonwinners = _finite(report.get("mean_p_move_with_trade_on_nonwinning_trades"))
    if n < ECONOMIC_VALIDATION_MIN_TRADES or delta is None:
        status = "INSUFFICIENT"
    elif delta < -1e-12:
        status = "CONTRADICTED"
    elif winners is not None and nonwinners is not None and winners + 1e-12 < nonwinners:
        status = "CONTRADICTED"
    else:
        status = "NOT_WORSE"
    return {
        "status": status,
        "unique_trades": n,
        "required_unique_trades": ECONOMIC_VALIDATION_MIN_TRADES,
        "delta_brier_vs_0_5": delta,
        "mean_p_on_winning_trades": winners,
        "mean_p_on_nonwinning_trades": nonwinners,
        "pre_entry_predictions_only": True,
        "real_trades_are_validation_not_training": True,
    }


def _combine_statistical(
    raw_probability: str,
    continuous: str,
    best_probability_representation: str,
) -> str:
    """Fail closed while allowing calibrated probability to rescue/replace raw.

    Raw directional performance remains visible evidence, but the probability
    gate is the best causally selected RAW/CALIBRATED representation.  Requiring
    Platt itself to improve an already-good raw forecast would be an invalid
    statistical veto.
    """
    if continuous == "NO" or best_probability_representation == "NO":
        return "NO"
    if continuous == "YES" and best_probability_representation == "YES":
        return "YES"
    return "INSUFFICIENT"


def _combine_economic(trade: str, management: str) -> str:
    if "CONTRADICTED" in {trade, management}:
        return "CONTRADICTED"
    if trade == "NOT_WORSE" and management == "NOT_WORSE":
        return "NOT_WORSE"
    return "INSUFFICIENT"


def _overall(statistical: str, economic: str) -> str:
    if statistical == "NO" or economic == "CONTRADICTED":
        return "NO"
    if statistical == "YES" and economic == "NOT_WORSE":
        return "YES"
    return "INSUFFICIENT"


def _final_report(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    probability = runtime.prospective_oos()
    continuous = runtime.continuous_oos()
    calibration = runtime.calibration_oos()
    trade = _trade_economics(runtime)
    management = _management_economics(runtime)
    q = runtime.q_audit(limit=500)
    horizons = [runtime.horizon_report(horizon) for horizon in HORIZONS]

    raw_probability_verdict = str(
        probability.get("does_model_beat_baseline_oos") or "INSUFFICIENT"
    )
    probability_representation_verdict = str(
        calibration.get("does_best_probability_representation_beat_baselines_oos")
        or "INSUFFICIENT"
    )
    continuous_verdict = str(
        continuous.get("does_continuous_model_beat_baseline_oos") or "INSUFFICIENT"
    )
    calibration_value_added = str(
        calibration.get("does_calibration_add_value_oos") or "INSUFFICIENT"
    )
    statistical = _combine_statistical(
        raw_probability_verdict,
        continuous_verdict,
        probability_representation_verdict,
    )
    economic = _combine_economic(trade["status"], management["status"])
    overall = _overall(statistical, economic)

    return {
        "contract_version": FINAL_REPORT_VERSION,
        "samples_per_horizon": [
            {
                "horizon_minutes": item.get("horizon_minutes"),
                "raw_n": item.get("raw_resolved"),
                "effective_n": item.get("effective_n"),
                "state": item.get("state"),
                "oos_candidate_blockers": item.get("oos_candidate_blockers") or [],
            }
            for item in horizons
        ],
        "q_maturity": {
            "counts": q.get("counts") or {},
            "capture_blockers": q.get("capture_blockers") or {},
            "overdue_is_contract_failure": bool(q.get("overdue_is_contract_failure")),
        },
        "g1m_local": management,
        "real_trade_relevance": trade,
        "baselines": {
            "probability": "constant_0_5 + causal_base_rate + resolved_persistence + fixed_15m_momentum",
            "continuous": "zero_return + causal_historical_mean + fixed_ret15_persistence",
        },
        "learned_models": {
            "probability_model_count": len(probability.get("items") or []),
            "continuous_model_count": len(continuous.get("items") or []),
            "calibrated_model_count": len(calibration.get("items") or []),
        },
        "best_preliminary_probability_model": _best_probability_item(probability),
        "oos_status": {
            "raw_probability": raw_probability_verdict,
            "best_probability_representation": probability_representation_verdict,
            "continuous_primary": continuous_verdict,
            "calibration_value_added": calibration_value_added,
            "statistical_combined": statistical,
        },
        "economic_plausibility": economic,
        "performance": runtime.materializer_status(),
        "does_model_beat_baseline_oos": overall,
        "verdict_semantics": {
            "YES": "serious prospective OOS superiority of the selected probability representation and continuous target, plus non-worse real-trade and local-management evidence",
            "NO": "selected probability/continuous baseline failure or economic contradiction",
            "INSUFFICIENT": "one or more required evidence layers are not mature",
            "calibration": "Platt value-add is reported separately; RAW may remain selected when already superior to causal baselines",
        },
        "auto_promotion_allowed": False,
        "policy_promotion_allowed": False,
        "edge_claim_allowed": False,
        "production_authority_changed": False,
        "production_authority": False,
    }


def install_g1_short_horizon_final_report() -> None:
    if getattr(ShortHorizonRuntime, "_final_report_version", None) == FINAL_REPORT_VERSION:
        return
    ShortHorizonRuntime.final_report = _final_report
    ShortHorizonRuntime._final_report_version = FINAL_REPORT_VERSION
