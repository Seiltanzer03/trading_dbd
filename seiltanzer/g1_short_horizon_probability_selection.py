"""Choose the best causally valid probability representation for G.1S evidence.

Calibration is valuable when it improves a probability forecast, but a raw model
that is already well calibrated must not be declared a failure merely because
Platt scaling adds no further benefit.  After the serious prospective OOS gate,
this layer accepts RAW or CALIBRATED only when that representation beats every
causal baseline on both primary probability metrics (Brier and log loss).

This remains research-only.  It does not promote a model or change production
trading authority.
"""
from __future__ import annotations

from typing import Any

from .g1_short_horizon_runtime import ShortHorizonRuntime, _finite


PROBABILITY_SELECTION_VERSION = "g1s-probability-representation-selection-v1"


def _baseline_primary_metrics(item: dict[str, Any]) -> tuple[list[float], list[float]]:
    briers: list[float] = []
    logs: list[float] = []
    for value in (item.get("baselines") or {}).values():
        if not isinstance(value, dict):
            continue
        brier = _finite(value.get("brier"))
        log_loss = _finite(value.get("log_loss"))
        if brier is not None:
            briers.append(float(brier))
        if log_loss is not None:
            logs.append(float(log_loss))
    return briers, logs


def _beats_all_baselines(
    brier: float | None,
    log_loss: float | None,
    baseline_briers: list[float],
    baseline_logs: list[float],
) -> bool:
    return bool(
        brier is not None
        and log_loss is not None
        and baseline_briers
        and baseline_logs
        and brier < min(baseline_briers)
        and log_loss < min(baseline_logs)
    )


def select_probability_representation(item: dict[str, Any]) -> dict[str, Any]:
    blockers = list(item.get("candidate_blockers") or [])
    raw_brier = _finite(item.get("raw_brier"))
    raw_log = _finite(item.get("raw_log_loss"))
    calibrated_brier = _finite(item.get("calibrated_brier"))
    calibrated_log = _finite(item.get("calibrated_log_loss"))
    baseline_briers, baseline_logs = _baseline_primary_metrics(item)

    raw_beats = _beats_all_baselines(raw_brier, raw_log, baseline_briers, baseline_logs)
    calibrated_beats = _beats_all_baselines(
        calibrated_brier, calibrated_log, baseline_briers, baseline_logs
    )
    calibration_improves_raw = bool(
        raw_brier is not None
        and raw_log is not None
        and calibrated_brier is not None
        and calibrated_log is not None
        and calibrated_brier <= raw_brier
        and calibrated_log <= raw_log
        and (calibrated_brier < raw_brier or calibrated_log < raw_log)
    )

    if blockers:
        verdict = "INSUFFICIENT"
        selected = None
    elif calibrated_beats and (not raw_beats or calibration_improves_raw):
        verdict = "YES"
        selected = "CALIBRATED"
    elif raw_beats:
        verdict = "YES"
        selected = "RAW"
    elif calibrated_beats:
        verdict = "YES"
        selected = "CALIBRATED"
    else:
        verdict = "NO"
        selected = None

    if blockers:
        calibration_value_added = "INSUFFICIENT"
    elif calibration_improves_raw and calibrated_beats:
        calibration_value_added = "YES"
    else:
        calibration_value_added = "NO"

    return {
        "contract_version": PROBABILITY_SELECTION_VERSION,
        "verdict": verdict,
        "selected_representation": selected,
        "raw_beats_causal_baselines": raw_beats,
        "calibrated_beats_causal_baselines": calibrated_beats,
        "calibration_improves_raw_primary_metrics": calibration_improves_raw,
        "calibration_value_added": calibration_value_added,
        "selection_rule": "beat_all_causal_baselines_on_brier_and_log_loss",
        "primary_metrics": ["brier", "log_loss"],
        "candidate_blockers": blockers,
        "production_authority": False,
        "edge_claim_allowed": False,
    }


def _enrich_calibration(report: dict[str, Any]) -> dict[str, Any]:
    report = dict(report)
    items = []
    for source in report.get("items") or []:
        item = dict(source)
        selection = select_probability_representation(item)
        item["probability_representation_selection"] = selection
        item["does_best_probability_representation_beat_baselines_oos"] = selection["verdict"]
        item["selected_probability_representation"] = selection["selected_representation"]
        items.append(item)
    report["items"] = items
    verdicts = [
        str(item.get("does_best_probability_representation_beat_baselines_oos"))
        for item in items
    ]
    report["does_best_probability_representation_beat_baselines_oos"] = (
        "YES" if "YES" in verdicts else ("NO" if "NO" in verdicts else "INSUFFICIENT")
    )
    value_added = [
        str((item.get("probability_representation_selection") or {}).get("calibration_value_added"))
        for item in items
    ]
    report["does_calibration_add_value_oos"] = (
        "YES" if "YES" in value_added else ("NO" if "NO" in value_added else "INSUFFICIENT")
    )
    report["probability_selection_contract_version"] = PROBABILITY_SELECTION_VERSION
    report["production_authority"] = False
    report["edge_claim_allowed"] = False
    return report


def install_g1_short_horizon_probability_selection() -> None:
    if getattr(ShortHorizonRuntime, "_probability_selection_version", None) == PROBABILITY_SELECTION_VERSION:
        return

    previous_calibration_oos = ShortHorizonRuntime.calibration_oos
    previous_probability_oos = ShortHorizonRuntime.prospective_oos
    previous_status = ShortHorizonRuntime.status

    def calibration_oos(self):
        return _enrich_calibration(previous_calibration_oos(self))

    def prospective_oos(self):
        report = previous_probability_oos(self)
        selection_report = calibration_oos(self)
        by_model = {
            str(item.get("model_id")): item
            for item in selection_report.get("items") or []
        }
        for item in report.get("items") or []:
            selected = by_model.get(str(item.get("model_id")))
            if selected is not None:
                item["calibration_oos"] = selected
        report["does_best_probability_representation_beat_baselines_oos"] = selection_report.get(
            "does_best_probability_representation_beat_baselines_oos", "INSUFFICIENT"
        )
        report["does_calibration_add_value_oos"] = selection_report.get(
            "does_calibration_add_value_oos", "INSUFFICIENT"
        )
        report["probability_selection_contract_version"] = PROBABILITY_SELECTION_VERSION
        report["production_authority"] = False
        report["edge_claim_allowed"] = False
        return report

    def status(self):
        report = previous_status(self)
        report["probability_representation_selection"] = {
            "contract_version": PROBABILITY_SELECTION_VERSION,
            "rule": "RAW_or_CALIBRATED_must_beat_all_causal_baselines_on_brier_and_log_loss",
            "calibration_value_add_is_not_mandatory_when_raw_is_already_superior": True,
            "serious_prospective_oos_gate_still_required": True,
            "production_authority": False,
            "auto_promotion": False,
        }
        return report

    ShortHorizonRuntime.calibration_oos = calibration_oos
    ShortHorizonRuntime.prospective_oos = prospective_oos
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._probability_selection_version = PROBABILITY_SELECTION_VERSION
