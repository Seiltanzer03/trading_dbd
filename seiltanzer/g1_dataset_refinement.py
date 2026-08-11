"""G.1A semantic refinements for measurement-validity and honest readiness.

Kept separate from the core registry so the eligibility/cut implementation stays
small and auditable. This module adds no model fitting or production authority.
"""
from __future__ import annotations

import json
from collections import Counter

from . import g1_dataset_runtime as _g1
from . import passive_learning as _pl
from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION, finite

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_EVALUATE = _g1._evaluate_row
_ORIGINAL_STATUS = _g1.g1_dataset_status
_REFINEMENT_VERSION = "g1a-readiness-refinement-v1"


def _loads(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _measurement_valid(row: dict) -> bool:
    """Technical measurement validity, deliberately distinct from evidence admission."""
    if row.get("feature_contract_version") != _pl.PASSIVE_SCHEMA_VERSION:
        return False
    if row.get("resolution_status") != "resolved":
        return False
    forecast = _loads(row.get("forecast_json"), {})
    features = _loads(row.get("features_json"), {})
    runtime = forecast.get("measurement_runtime_contract") or features.get(
        "measurement_runtime_contract"
    )
    if runtime != MEASUREMENT_RUNTIME_VERSION:
        return False
    captured = finite(row.get("captured_ts"))
    target = finite(row.get("target_ts"))
    if captured is None or target is None or target <= captured:
        return False
    outcome = _loads(row.get("outcome_json"), {})
    terminal = (outcome.get("terminal") or {}) if isinstance(outcome, dict) else {}
    terminal_ts = finite(terminal.get("terminal_price_ts"))
    terminal_price = finite(terminal.get("terminal_price"))
    terminal_age = finite(terminal.get("terminal_age_to_target_sec"))
    return bool(
        terminal.get("clean_label") is True
        and terminal.get("terminal_authoritative") is True
        and terminal.get("terminal_lookahead_used") is False
        and terminal_price is not None and terminal_price > 0
        and terminal_ts is not None and terminal_ts <= target + 1e-6
        and terminal_age is not None
        and -1e-6 <= terminal_age <= _g1.TERMINAL_MAX_AGE_SEC
        and finite(outcome.get("future_log_return")) is not None
    )


def evaluate_refined(row: dict) -> dict:
    decision = dict(_ORIGINAL_EVALUATE(row))
    base = list(decision.get("base_exclusion_reasons") or [])
    q_reasons = list(decision.get("q_exclusion_reasons") or [])
    kind = str(row.get("price_kind") or "").lower()
    origin = str(row.get("observation_origin") or "").lower()
    if kind == "demo":
        base.append("DEMO_DATA")
    if kind == "synthetic" or origin == "synthetic":
        base.append("SYNTHETIC_DATA")
    base = _g1._ordered_reasons(base)
    measurement_valid = _measurement_valid(row)
    forecast_eval = measurement_valid and not base
    if not forecast_eval:
        q_reasons.extend(base)
    q_reasons = _g1._ordered_reasons(q_reasons)
    q_eligible = forecast_eval and not q_reasons
    all_reasons = base if base else ([] if q_eligible else q_reasons)
    decision.update({
        "measurement_valid": measurement_valid,
        "forecast_eval_eligible": forecast_eval,
        "q_to_p_eligible": q_eligible,
        "terminal_q_eligible": q_eligible,
        "first_touch_q_eligible": False,
        "base_exclusion_reasons": base,
        "q_exclusion_reasons": [] if q_eligible else q_reasons,
        "all_reasons": all_reasons,
        "primary_reason": all_reasons[0] if all_reasons else None,
    })
    return decision


def status_refined(self: _ENGINE) -> dict:
    result = dict(_ORIGINAL_STATUS(self))
    with self._lock:
        joined = [dict(r) for r in self._conn.execute(
            "SELECT p.*,g.forecast_eval_eligible,g.primary_reason,g.measurement_runtime_contract "
            "FROM g1_dataset_membership g JOIN passive_market_observations p "
            "ON p.observation_id=g.observation_id WHERE g.dataset_contract_version=?",
            (_g1.G1_DATASET_CONTRACT_VERSION,),
        ).fetchall()]
    measurement_valid_n = sum(1 for row in joined if _measurement_valid(row))
    current_runtime_evaluated_n = sum(
        1 for row in joined
        if row.get("feature_contract_version") == _pl.PASSIVE_SCHEMA_VERSION
        and row.get("measurement_runtime_contract") == MEASUREMENT_RUNTIME_VERSION
    )
    primary_exclusions = Counter(
        str(row["primary_reason"])
        for row in joined
        if int(row.get("forecast_eval_eligible") or 0) != 1 and row.get("primary_reason")
    )
    contract_errors = sum(
        int(result.get(name) or 0)
        for name in (
            "dataset_contract_error_n", "dataset_membership_error_n", "dataset_cut_error_n",
            "source_mutation_error_n", "cohort_contract_error_n", "dependency_contract_error_n",
            "q_eligibility_error_n",
        )
    )
    result.update({
        "g1a_readiness_refinement_version": _REFINEMENT_VERSION,
        "measurement_valid_n": measurement_valid_n,
        "current_runtime_evaluated_n": current_runtime_evaluated_n,
        "raw_n": int(result.get("raw_task_membership_n") or 0),
        "exclusion_counts": dict(sorted(primary_exclusions.items())),
        "dataset_contract_runtime_validated": bool(
            current_runtime_evaluated_n > 0 and measurement_valid_n > 0 and contract_errors == 0
        ),
    })
    return result


def install_g1_dataset_refinement() -> None:
    if getattr(_ENGINE, "_g1_dataset_refinement", None) == _REFINEMENT_VERSION:
        return
    # _sync_membership resolves this module global dynamically, so new rows use
    # the refined pure decision without mutating already materialized records.
    _g1._evaluate_row = evaluate_refined
    _g1.g1_dataset_status = status_refined
    _ENGINE._g1_evaluate_row = staticmethod(evaluate_refined)
    _ENGINE.g1_dataset_status = status_refined
    _ENGINE._g1_dataset_refinement = _REFINEMENT_VERSION
