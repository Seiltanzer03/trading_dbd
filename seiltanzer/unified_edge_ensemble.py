"""One auditable, bounded ranking of already authorized management candidates.

Expert preferences are dimensionless. They never replace net economics, risk
admission, source authority, strategy events or manual execution confirmation.
A single shared comparison bank is priced once; ablations reuse frozen scores.
No provider call, training or network access occurs in this module.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

VERSION = "unified-edge-ensemble-v1"
BASE = {"HOLD": 0., "CLOSE_10": .1, "CLOSE_25": .25, "CLOSE_50": .5, "EXIT": 1.}
EXTENDED = ("MOVE_TO_BE", "TIGHTEN_STOP", "TRAIL_GAMMA_FLIP", "REDUCE_TAKE",
            "EXTEND_TAKE", "SCALE_OUT_ON_SPIKE", "TIME_STOP")
COMPONENTS = ("quantitative_base", "mathematical_edge", "active_edge",
              "historical_llm", "current_llm")
SCHEMES = {
    "balanced": dict(zip(COMPONENTS, (.40, .15, .15, .15, .15))),
    "llm20": dict(zip(COMPONENTS, (.40, .135, .135, .13, .20))),
    "quant100": dict(zip(COMPONENTS, (1., 0., 0., 0., 0.))),
}


def regime_label(snapshot):
    value = ((snapshot.get("policy_manager") or {}).get("market_regime")
             or snapshot.get("market_regime") or snapshot.get("regime"))
    if isinstance(value, dict):
        value = value.get("regime") or value.get("label")
    return value if isinstance(value, str) else None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _unit(value, default=0.):
    value = number(value)
    return min(1., max(0., value if value is not None else default))


def _score(value):
    value = number(value)
    return min(1., max(-1., value)) if value is not None else None


def candidate_id(policy, parameters=None):
    if not parameters:
        return policy
    data = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return policy + ":" + hashlib.sha256(data.encode()).hexdigest()[:16]


def _lineage(value):
    aliases = {"option_chain": "option_distribution", "price_bars": "price_path"}
    value = str(value)
    if value.endswith(":option_distribution"):
        return "option_distribution"
    if value.endswith(":price_path"):
        return "price_path"
    return aliases.get(value, value)


def _base_authorized(manager, policy):
    if policy == "HOLD":
        return True, "HOLD_FEASIBLE"
    gate = manager.get("gate") or {}
    overlay = gate.get("degraded_authority_overlay") or {}
    summary = overlay.get("candidate_summary") or {}
    if isinstance(summary, list):
        summary = {r.get("policy"): r for r in summary if isinstance(r, dict)}
    if (summary.get(policy) or {}).get("qualified") is True:
        return True, "FROZEN_CANDIDATE_CONFIRMATION_PASS"
    chosen = gate.get("execution_policy") or gate.get("policy")
    if chosen == policy and (gate.get("working_action_confirmed") is True
                             or gate.get("automatic_execution_allowed") is True):
        return True, "FROZEN_SELECTED_CONFIRMATION_PASS"
    return False, "ACTION_CONFIRMATION_NOT_PASSED"


def collect_candidates(snapshot):
    manager = snapshot.get("policy_manager") or {}
    policies = manager.get("policies") or {}
    rule = manager.get("selection_rule") or {}
    floor = number((manager.get("risk_constraint") or {}).get("net_cvar_floor_r"))
    if floor is None:
        floor = number(rule.get("cvar_floor_r"))
    hold = policies.get("HOLD") or {}
    hold_e = number(hold.get("expected_final_r_net", hold.get("expected_final_r")))
    hold_c = number(hold.get("cvar10_r_net", hold.get("cvar10_r")))
    rows = []
    for policy in BASE:
        raw = policies.get(policy) or {}
        expected = number(raw.get("expected_final_r_net", raw.get("expected_final_r")))
        cvar = number(raw.get("cvar10_r_net", raw.get("cvar10_r")))
        authorized, reason = _base_authorized(manager, policy)
        eligible = authorized
        if expected is None or cvar is None or hold_e is None or hold_c is None:
            eligible, reason = False, "NET_ECONOMICS_UNAVAILABLE"
        elif raw.get("outcomes_include_execution_costs") is not True:
            eligible, reason = False, "NET_COST_CONTRACT_UNAVAILABLE"
        elif floor is None:
            eligible, reason = False, "HARD_NET_CVAR_FLOOR_UNAVAILABLE"
        elif policy not in (rule.get("eligible") or []) or cvar < floor - 1e-8:
            eligible, reason = False, "HARD_CVAR_INFEASIBLE"
        rows.append({"candidate_id": policy, "policy": policy, "parameters": {},
                     "eligible": eligible, "reason": reason,
                     "expected_net_r": expected, "cvar10_net_r": cvar,
                     "delta_expected_r": expected - hold_e if expected is not None and hold_e is not None else None,
                     "delta_cvar_r": cvar - hold_c if cvar is not None and hold_c is not None else None,
                     "execution_cost_r": number(raw.get("execution_cost_r")),
                     "source": "authoritative_policy_paths"})
    seen = set(BASE)
    for raw in snapshot.get("active_management_candidates") or []:
        policy = raw.get("policy")
        if policy not in EXTENDED:
            continue
        parameters = deepcopy(raw.get("parameters") or {})
        try:
            identity = candidate_id(policy, parameters)
        except (ValueError, TypeError):
            parameters, identity = {}, policy
        if identity in seen:
            continue
        seen.add(identity)
        expected = number(raw.get("expected_variant_net_r"))
        cvar = number(raw.get("worst_seed_cvar10_net_r"))
        delta = number(raw.get("expected_delta_vs_hold_r"))
        eligible = raw.get("status") == "eligible"
        reason = raw.get("reason") or raw.get("status") or "UNAVAILABLE"
        if eligible and (expected is None or cvar is None or delta is None or floor is None):
            eligible, reason = False, "NET_ECONOMICS_UNAVAILABLE"
        if eligible and cvar < floor - 1e-8:
            eligible, reason = False, "HARD_CVAR_INFEASIBLE"
        # Until every evaluator shares the same scenario bank, use its paired
        # improvement against its own HOLD, anchored to the authoritative HOLD.
        # This is a model comparison, never a claim of realized profit.
        anchored = hold_e + delta if hold_e is not None and delta is not None else expected
        local_hold_c = number(raw.get("worst_seed_hold_cvar10_net_r"))
        cvar_delta = cvar - local_hold_c if cvar is not None and local_hold_c is not None else None
        rows.append({"candidate_id": identity, "policy": policy, "parameters": parameters,
                     "eligible": eligible, "reason": reason, "expected_net_r": anchored,
                     "cvar10_net_r": cvar, "delta_expected_r": delta, "delta_cvar_r": cvar_delta,
                     "execution_cost_r": number(raw.get("execution_cost_r")),
                     "quant_evaluation": deepcopy(raw), "source": raw.get("source") or "paired_extended_paths",
                     "economics_basis": "paired_delta_anchored_to_authoritative_hold",
                     "raw_expected_variant_net_r": expected})
    for policy in EXTENDED:
        if not any(row["policy"] == policy for row in rows):
            rows.append({"candidate_id": policy, "policy": policy, "parameters": {},
                         "eligible": False, "reason": "ACTION_PARAMETERS_OR_EVALUATION_UNAVAILABLE",
                         "expected_net_r": None, "cvar10_net_r": None,
                         "delta_expected_r": None, "delta_cvar_r": None, "execution_cost_r": None})
    price = (((manager.get("input_audit") or {}).get("rows") or {}).get("instrument_price") or {})
    barrier = (snapshot.get("trade_geometry") or {}).get("active_risk_barrier_breached")
    invalid_price = bool(price) and (price.get("available") is not True
        or price.get("production_authority") is False
        or str(price.get("source") or "").startswith(("Bybit ", "yfinance ")))
    terminal = (snapshot.get("position_state") or {}).get("strategy_terminal_event")
    if barrier or invalid_price or terminal:
        reason = ("MANDATORY_STRATEGY_EVENT" if barrier or terminal else "AUTHORITATIVE_PRICE_UNAVAILABLE")
        for row in rows:
            if row["policy"] != "HOLD":
                row.update(eligible=False, reason=reason)
    return rows


def _direction_scores(profile, candidates):
    direction = _score(profile.get("direction_score"))
    if direction is None:
        return {}
    allowed = profile.get("eligible_extended_roles")
    scores = {}
    for row in candidates:
        policy = row["policy"]
        if policy in BASE:
            if profile.get("base_policy_eligible") is not False:
                scores[row["candidate_id"]] = direction * (1. - 2. * BASE[policy])
        elif allowed is None or policy in allowed:
            scores[row["candidate_id"]] = direction if policy == "EXTEND_TAKE" else -direction
    return scores


def _llm_lineage(snapshot, llm, family_evidence=None):
    """LLM may cite observed families; it cannot invent independence labels."""
    manager = snapshot.get("policy_manager") or {}
    evidence = manager.get("evidence") or {}
    known = {"price_path", "option_distribution"}
    for key in ("adverse_confirmation_families", "supportive_confirmation_families"):
        known.update(_lineage(value) for value in evidence.get(key, []) if isinstance(value, str))
    for key in ("adverse_confirmations", "supportive_contradictions", "context_observations"):
        for row in evidence.get(key, []):
            if isinstance(row, dict) and row.get("family") and row.get("available") is True:
                known.add(_lineage(row["family"]))
    family_aliases = {"macro_events": ("macro", "event"), "cross_asset": ("intermarket",),
                      "live_tape": ("order_flow",)}
    expansion = {}
    for alias, families in family_aliases.items():
        expansion[alias] = {_lineage(origin) for family in families
            for row in [(family_evidence or {}).get(family) or {}]
            if row.get("available") is True for origin in row.get("evidence_family_ids", [])}
        known.update(expansion[alias])
    claimed = set()
    for value in llm.get("evidence_families", []):
        if isinstance(value, str):
            claimed.update(expansion.get(value) or {_lineage(value)})
    accepted = claimed.intersection(known)
    return sorted(accepted or {"price_path", "option_distribution"}), sorted(claimed - known)


def collect_components(snapshot, candidates, current_llm=None):
    manager = snapshot.get("policy_manager") or {}
    captured = number(snapshot.get("captured_ts"))
    from .edge_family_adapters import build_edge_family_evidence
    evidence = build_edge_family_evidence(snapshot)
    rows = [{"component_id": "quantitative_base", "available": True, "quality": 1.,
             "observed_ts": captured, "max_age_sec": 900., "scores": {},
             "source_ids": ["authoritative_execution_paths"],
             "evidence_family_ids": ["option_distribution", "price_path"],
             "model_version": manager.get("version"), "reason": "NET_ECONOMICS_VOICE"}]
    profiles = (("mathematical_edge", manager.get("mathematical_edge") or {}),
                ("active_edge", manager.get("active_edge_provisional_weight") or {}),
                ("historical_llm", manager.get("llm_edge_exploratory_weight") or {}))
    for identity, profile in profiles:
        families = profile.get("evidence_family_ids") or ["price_path"]
        observed = number(profile.get("latest_bar_end_ts", profile.get("observed_ts", captured)))
        quality = profile.get("quality_multiplier", profile.get("quality", 1.))
        rows.append({"component_id": identity, "available": profile.get("available") is True,
                     "quality": _unit(quality), "observed_ts": observed,
                     "max_age_sec": profile.get("max_age_sec", 900.),
                     "scores": _direction_scores(profile, candidates),
                     "source_ids": profile.get("source_ids") or [profile.get("model_sha256") or profile.get("contract_version") or identity],
                     "evidence_family_ids": families, "reason": profile.get("reason") or "MATCHED_WORKING_EDGE",
                     "instrument": profile.get("instrument"), "model_version": profile.get("model_sha256") or profile.get("contract_version"),
                     "supported_regimes": profile.get("supported_regimes"),
                     "regime_contract_version": profile.get("regime_contract_version"),
                     "score_semantics": "bounded_working_action_affinity_not_return"})
        if identity == "mathematical_edge":
            from .mathematical_action_preferences import mathematical_action_preferences
            path = mathematical_action_preferences(snapshot, candidates, profile)
            rows[-1]["path_target_applicability"] = path["applicability"]
            if profile.get("path_predictions") and not path.get("available") and not rows[-1]["available"]:
                rows[-1]["reason"] = path["reason"]
            if path.get("available"):
                target = rows[-1]
                legacy = target["scores"] if target["available"] else {}
                target["scores"] = {key: (legacy[key] + value) / 2 if key in legacy else value
                                    for key, value in path["scores"].items()} | {
                                    key: value for key, value in legacy.items() if key not in path["scores"]}
                target["quality"] = min(target["quality"], path["quality"]) if target["available"] else path["quality"]
                target["available"] = True
                target["reason"] = "MATCHED_PRICE_AND_PATH_ACTION_MODELS"
    llm = current_llm if isinstance(current_llm, dict) else {}
    scores = {str(k): _score(v) for k, v in (llm.get("policy_scores") or {}).items()}
    scores = {k: v for k, v in scores.items() if v is not None}
    if not scores and llm.get("policy") in set(BASE) | set(EXTENDED):
        # A single explicit choice is a sparse opinion. Unscored actions are
        # absent, not synthetic votes against those actions.
        scores = {llm["policy"]: 1.}
    llm_available = llm.get("status") == "ok" and bool(scores)
    llm_families, rejected_families = _llm_lineage(snapshot, llm, evidence.get("families"))
    lineage_status = (manager.get("evidence") or {}).get("lineage_budget_status") or {}
    rows.append({"component_id": "current_llm", "available": llm_available,
                 "quality": 1., "observed_ts": number(llm.get("captured_ts", captured)),
                 "max_age_sec": 900., "scores": scores,
                 "source_ids": ["current_snapshot_llm_interpretation"],
                 "evidence_family_ids": llm_families,
                 "unverified_claimed_family_ids": rejected_families,
                 "model_version": llm.get("model") or llm.get("contract_version"),
                 "reason": (("STRUCTURED_LLM_PREFERENCE" if llm_available else "CURRENT_LLM_UNAVAILABLE")
                            + (";EVIDENCE_LINEAGE_EXCLUDED_BY_SNAPSHOT_BYTE_BUDGET"
                               if lineage_status.get("available") is False else "")),
                 "self_confidence_used_for_weight": False})
    # Explicit model assessments can replace a legacy directional heuristic.
    # Family models consume an existing expert budget, never add new percentages.
    for spec in evidence.get("components") or []:
        if spec.get("component_id") not in COMPONENTS or spec.get("component_id") == "quantitative_base":
            continue
        target = next(row for row in rows if row["component_id"] == spec["component_id"])
        # Retain multiple model opinions inside their one component pool.
        target.setdefault("family_assessments", []).append(deepcopy(spec))
    for target in rows:
        admitted = [item for item in target.get("family_assessments", [])
                    if item.get("available") is True]
        if not admitted:
            continue
        opinions = ([target] if target.get("available") else []) + admitted
        buckets = {}
        for opinion in opinions:
            lineage = tuple(sorted(opinion.get("evidence_family_ids") or opinion.get("source_ids") or ["unknown_lineage"]))
            buckets.setdefault(lineage, []).append(opinion)
        keys = {str(key) for opinion in opinions for key in (opinion.get("scores") or {})}
        scores = {}
        for key in keys:
            samples = []
            for group in buckets.values():
                values = [_score((opinion.get("scores") or {}).get(key)) for opinion in group]
                values = [value for value in values if value is not None]
                if values:
                    samples.append(sum(values) / len(values))
            if samples:
                scores[key] = sum(samples) / len(samples)
        target["scores"] = scores
        target["available"] = bool(scores)
        target["reason"] = "VALIDATED_FAMILY_MODELS_WITHIN_EXISTING_COMPONENT_BUDGET"
        target["quality"] = min(_unit(item.get("quality"), 1.) for item in opinions)
        target["source_ids"] = sorted({str(source) for opinion in opinions for source in opinion.get("source_ids", [])})
        target["evidence_family_ids"] = sorted({str(family) for opinion in opinions for family in opinion.get("evidence_family_ids", [])})
        target["observed_ts"] = min((number(item.get("observed_ts")) for item in opinions
                                     if number(item.get("observed_ts")) is not None), default=None)
        target["max_age_sec"] = min((number(item.get("max_age_sec")) for item in opinions
                                     if number(item.get("max_age_sec")) is not None), default=900.)
    overrides = manager.get("unified_component_assessments") or []
    for spec in overrides:
        if spec.get("component_id") in COMPONENTS and spec.get("component_id") != "quantitative_base":
            index = COMPONENTS.index(spec["component_id"])
            rows[index] = {**rows[index], **deepcopy(spec)}
    return rows, evidence.get("families") or {}


def _prepare_components(specs, snapshot, nominal, excluded=None):
    captured = number(snapshot.get("captured_ts"))
    instrument = (snapshot.get("strategy") or {}).get("instrument") or snapshot.get("instrument")
    regime = regime_label(snapshot)
    rows = deepcopy(specs)
    for row in rows:
        identity = row["component_id"]
        row["evidence_family_ids"] = sorted({_lineage(value) for value in row.get("evidence_family_ids", [])})
        observed = number(row.get("observed_ts"))
        age = captured - observed if captured is not None and observed is not None else None
        maximum = number(row.get("max_age_sec"))
        freshness = max(0., 1. - age / maximum) if age is not None and maximum is not None and maximum > 0 and age >= 0 else 0.
        if identity == "quantitative_base" and captured is not None:
            freshness = 1.
        available = row.get("available") is True
        if row.get("instrument") and row["instrument"] != instrument:
            available, row["reason"] = False, "INSTRUMENT_MISMATCH"
        supported = row.get("supported_regimes")
        classifier = snapshot.get("edge_regime") or {}
        if (supported and "ALL" not in supported and classifier.get("available") is True
                and row.get("regime_contract_version") != classifier.get("contract_version")):
            available, row["reason"] = False, "REGIME_CONTRACT_UNVERIFIED"
        if supported and "ALL" not in supported and (not regime or regime not in supported):
            available, row["reason"] = False, "REGIME_NOT_SUPPORTED"
        if freshness <= 0:
            available, row["reason"] = False, "STALE_OR_UNTIMED_EVIDENCE"
        row.update({"nominal_weight": nominal.get(identity, 0.), "available": available,
                    "availability": "AVAILABLE" if available else "UNAVAILABLE",
                    "age_sec": age, "freshness_multiplier": freshness,
                    "quality": _unit(row.get("quality"), 1.), "dependence_multiplier": 1.,
                    "excluded": identity == excluded})
    # Common lineage is counted once within additional opinion budgets. Quant
    # remains the fallback; its compulsory risk calculation is never discounted.
    live = [row for row in rows if row["available"] and not row["excluded"]]
    for row in live:
        if row["component_id"] == "quantitative_base":
            continue
        families = set(row.get("evidence_family_ids") or [])
        sources = set(row.get("source_ids") or [])
        peers = [p["component_id"] for p in live if p is not row and (
            families.intersection(p.get("evidence_family_ids") or [])
            or sources.intersection(p.get("source_ids") or []))]
        row["dependence_multiplier"] = 1. / (1. + len(peers))
        row["shared_with_components"] = peers
    for row in rows:
        row["effective_weight"] = (row["nominal_weight"] * row["quality"] * row["freshness_multiplier"]
                                   * row["dependence_multiplier"] if row["available"] and not row["excluded"] else 0.)
    quant = next(row for row in rows if row["component_id"] == "quantitative_base")
    if quant["available"] and not quant["excluded"]:
        quant["effective_weight"] += max(0., 1. - sum(row["effective_weight"] for row in rows))
    return rows


def rank_candidates(candidates, specs, snapshot, nominal, excluded=None):
    rows = deepcopy(candidates)
    components = _prepare_components(specs, snapshot, nominal, excluded)
    feasible = [row for row in rows if row["eligible"]]
    hold = next((row for row in rows if row["policy"] == "HOLD"), {})
    hold_expected = number(hold.get("expected_net_r"))
    values = [row["expected_net_r"] for row in feasible if row["expected_net_r"] is not None]
    scale = max(.12, max(values) - min(values)) if values else .12
    band = number(((snapshot.get("policy_manager") or {}).get("selection_rule") or {}).get("indifference_band_r"))
    band = max(0., band if band is not None else .03)
    best_expected = max(values) if values else None
    for row in rows:
        quant_score = _score((row["expected_net_r"] - hold_expected) / scale) if row["expected_net_r"] is not None and hold_expected is not None else None
        contributions, missing_weight = [], 0.
        for component in components:
            identity = component["component_id"]
            preferences = component.get("scores") or {}
            score = quant_score if identity == "quantitative_base" else _score(preferences.get(row["candidate_id"], preferences.get(row["policy"])))
            weight = component["effective_weight"]
            if score is None and identity != "quantitative_base":
                missing_weight += weight
                weight = 0.
            contributions.append({"component_id": identity, "score": score, "effective_weight": weight,
                                  "contribution": weight * score if score is not None else 0.})
        quant = next(item for item in contributions if item["component_id"] == "quantitative_base")
        if excluded != "quantitative_base" and quant_score is not None:
            quant["effective_weight"] += missing_weight
            quant["contribution"] = quant_score * quant["effective_weight"]
        row["component_contributions"] = contributions
        row["score"] = round(sum(item["contribution"] for item in contributions), 8)
        row["score_scale_r"] = scale
        # Opinions cannot purchase an unlimited economic sacrifice. A qualified
        # risk overlay may pay a small explicit cost for a material tail gain.
        row["ranking_eligible"] = row["eligible"]
        if row["eligible"] and row["policy"] != "HOLD" and best_expected is not None:
            tail_benefit = (number(row.get("delta_cvar_r")) or 0.) >= .05
            delta = number(row.get("delta_expected_r"))
            material = delta is not None and (delta > band or (tail_benefit and delta >= -band))
            if not material:
                row["ranking_eligible"], row["ranking_reason"] = False, "NO_MATERIAL_NET_OR_BOUNDED_TAIL_BENEFIT"
            elif best_expected - row["expected_net_r"] > band + 1e-8:
                row["ranking_eligible"], row["ranking_reason"] = False, "EXPECTED_SACRIFICE_EXCEEDS_BUDGET"
    allowed = [row for row in rows if row["ranking_eligible"]]
    selected = max(allowed, key=lambda row: (row["score"], row["policy"] == "HOLD", -BASE.get(row["policy"], .05))) if allowed else None
    return selected, rows, components


def _comparison(scheme, selected):
    return {"scheme": scheme, "selected_candidate_id": selected.get("candidate_id") if selected else None,
            "selected_policy": selected.get("policy") if selected else None,
            "expected_net_r": selected.get("expected_net_r") if selected else None,
            "cvar10_net_r": selected.get("cvar10_net_r") if selected else None,
            "execution_cost_r": selected.get("execution_cost_r") if selected else None,
            "delta_expected_r": selected.get("delta_expected_r") if selected else None,
            "intervention": bool(selected and selected["policy"] != "HOLD"),
            "evidence_type": "MODEL_SCENARIOS_NOT_HISTORICAL_PROFIT"}


def build_unified_ensemble(snapshot, current_llm=None, scheme="balanced"):
    if scheme not in SCHEMES:
        raise ValueError("unknown unified weight scheme")
    candidates = collect_candidates(snapshot)
    from .unified_candidate_economics import price_unified_candidates
    economics = price_unified_candidates(snapshot, candidates)
    invalid_common = not economics.get("available") and (
        str(economics.get("reason") or "").startswith(("INVALID_", "FROZEN_", "DECLARED_"))
        or economics.get("reason") == "EXECUTION_COST_MODEL_UNAVAILABLE")
    if invalid_common:
        for row in candidates:
            row.update(eligible=False, reason="INVALID_COMMON_ECONOMICS:" + str(economics.get("reason")))
    if economics.get("available"):
        for row in candidates:
            priced = (economics.get("candidates") or {}).get(row["candidate_id"]) or {}
            if priced.get("available") is not True:
                if row["eligible"]:
                    row.update(eligible=False, reason=priced.get("reason") or "COMMON_ECONOMICS_UNAVAILABLE")
                continue
            row["original_economics"] = {key: row.get(key) for key in (
                "expected_net_r", "cvar10_net_r", "delta_expected_r", "delta_cvar_r", "economics_basis")}
            row.update({key: value for key, value in priced.items() if key not in (
                "available", "reason", "original_candidate_eligible")})
            if row["eligible"] and priced.get("hard_risk_pass") is not True:
                row.update(eligible=False, reason="COMMON_SCENARIO_HARD_CVAR_INFEASIBLE")
            if row["eligible"] and row["policy"] in EXTENDED:
                band = number(((snapshot.get("policy_manager") or {}).get("selection_rule") or {}).get("indifference_band_r"))
                band = max(0., band if band is not None else .03)
                if (number(priced.get("paired_delta_ci95_lower_r")) or 0.) <= band:
                    row.update(eligible=False, reason="COMMON_SCENARIO_NO_ROBUST_MATERIAL_BENEFIT")
    specs, families = collect_components(snapshot, candidates, current_llm)
    selected, candidates, components = rank_candidates(candidates, specs, snapshot, SCHEMES[scheme])
    counterfactuals = []
    for identity in COMPONENTS:
        without, _, _ = rank_candidates(candidates, specs, snapshot, SCHEMES[scheme], identity)
        counterfactuals.append({"excluded_component_id": identity, **_comparison("without_" + identity, without),
                               "risk_and_cost_evaluation_preserved": True})
    comparisons = []
    for name, nominal in SCHEMES.items():
        choice, _, _ = rank_candidates(candidates, specs, snapshot, nominal)
        comparisons.append(_comparison(name, choice))
    legacy = ((snapshot.get("policy_manager") or {}).get("management_decision") or {}).get("policy")
    legacy_row = next((row for row in candidates if row["policy"] == legacy), None)
    comparisons.append(_comparison("legacy_control", legacy_row))
    return {"contract_version": VERSION, "available": selected is not None,
            "instrument": (snapshot.get("strategy") or {}).get("instrument") or snapshot.get("instrument"),
            "regime": regime_label(snapshot),
            "regime_context": {key: value for key, value in (snapshot.get("edge_regime") or {}).items()
                               if key in {"contract_version", "available", "reason", "quality", "price_regime",
                                          "threshold_status", "training_proof", "profit_proof"}},
            "scheme": scheme, "selected_candidate_id": selected.get("candidate_id") if selected else None,
            "selected_policy": selected.get("policy") if selected else None,
            "reason": "WEIGHTED_AUTHORIZED_CANDIDATES" if selected else "NO_AUTHORIZED_CANDIDATE",
            "candidates": candidates, "components": components, "edge_families": families,
            "counterfactuals": counterfactuals, "scheme_comparisons": comparisons,
            "hard_risk_override": False, "automatic_execution_allowed": False,
            "score_semantics": "dimensionless_preferences_not_expected_return_or_probability",
            "economics_scope": ("one frozen option-driver comparison bank, piecewise-linear execution; "
                                "original source/risk/action gates remain mandatory"
                                if economics.get("available") else
                                "authoritative base and paired extended economics; common bank unavailable"),
            "shared_scenario_bank": economics.get("shared_scenario_bank") is True,
            "common_economics_reason": economics.get("reason"),
            "scenario_bank": economics.get("bank"),
            "authoritative_bank_reused": economics.get("authoritative_bank_reused") is True,
            "historical_profit_proven": False,
            "regime_weight_semantics": "validated_applicability_only; unknown regime uses stated priors"}
