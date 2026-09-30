"""Independent deterministic extended-action candidates, manual execution only."""
from .llm_shadow_working_action import build_working_action
from .extended_policy_evaluation import evaluate_extended_action

POLICIES = ("MOVE_TO_BE", "TIGHTEN_STOP", "TRAIL_GAMMA_FLIP", "REDUCE_TAKE",
            "EXTEND_TAKE", "SCALE_OUT_ON_SPIKE", "TIME_STOP")


def select_active_management(snapshot):
    """Select only quantified improvements; no candidate is also a valid result."""
    rows, candidates = [], []
    armed = {x.get("policy") for x in (snapshot.get("position_state") or {}).get(
        "armed_conditional_actions", [])}
    for policy in POLICIES:
        if policy in armed:
            rows.append({"policy": policy, "status": "already_armed"})
            continue
        proposal = {"policy": policy, "status": "ok", "confidence": .65,
            "confidence_semantics": "legacy_manual_registration_threshold_not_probability",
            "source": "deterministic_active_management", "automatic_execution_allowed": False}
        proposal["working_action"] = build_working_action(snapshot, proposal)
        assessment = evaluate_extended_action(snapshot, proposal["working_action"])
        proposal["quant_evaluation"] = assessment
        rows.append({"policy": policy, **assessment})
        if assessment["status"] == "eligible":
            candidates.append(proposal)
    snapshot["active_management_candidates"] = rows
    if not candidates:
        return None
    selected = max(candidates, key=lambda p: (
        p["quant_evaluation"]["paired_delta_ci95_lower_r"],
        p["quant_evaluation"]["worst_seed_cvar10_gross_r"]))
    selected["reason_ru"] = "Максимальный нижний предел расчётного прироста Expected среди допустимых расширенных действий; HOLD остаётся базовым сравнением."
    selected["production_authority"] = False
    selected["statistically_validated_advantage"] = False
    return selected
