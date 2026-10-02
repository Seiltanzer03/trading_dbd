"""Frozen, bounded additional experts with explicit replacement budget schemes.

Registry entries supply dimensionless action preferences, never risk admission,
execution authority, calibrated probabilities, or new percentage points.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re

CONTRACT = "unified-expert-registry-v1"
SCORE_SEMANTICS = "dimensionless_action_preferences_not_return_or_probability"
CORE_IDS = ("quantitative_base", "mathematical_edge", "active_edge", "historical_llm", "current_llm")
MAX_EXPERTS = 11
MAX_BYTES = 24_000
MAX_SCHEMES = 8
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _texts(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 8
            or any(not isinstance(item, str) or not 0 < len(item) <= 128 for item in value)):
        raise ValueError("INVALID_EXPERT_LINEAGE")
    return tuple(sorted(set(value)))


@dataclass(frozen=True)
class ExpertDefinition:
    expert_id: str
    label: str
    instrument: str
    model_version: str
    source_ids: tuple[str, ...]
    evidence_family_ids: tuple[str, ...]
    max_age_sec: float
    supported_regimes: tuple[str, ...]
    score_semantics: str = SCORE_SEMANTICS


def resolve_expert_registry(snapshot, core_schemes, candidates=()):
    """Validate once, freeze definitions, and resolve only complete sum-one budgets."""
    manager = snapshot.get("policy_manager") or {}
    raw = manager.get("expert_registry")
    audit = {"contract_version": CONTRACT, "available": False, "reason": "NO_REGISTERED_EXPERTS",
             "definitions": [], "rejected_experts": [], "rejected_schemes": [],
             "score_semantics": SCORE_SEMANTICS, "automatic_execution_allowed": False,
             "hard_risk_override": False}
    schemes = {name: dict(weights) for name, weights in core_schemes.items()}
    if raw is None:
        return [], schemes, audit
    try:
        payload = json.dumps(raw, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(payload.encode()) > MAX_BYTES or not isinstance(raw, dict) or raw.get("contract_version") != CONTRACT:
            raise ValueError("INVALID_REGISTRY_CONTRACT_OR_BYTE_BOUND")
        definitions = raw.get("definitions")
        if not isinstance(definitions, list) or not 1 <= len(definitions) <= MAX_EXPERTS:
            raise ValueError("INVALID_REGISTRY_EXPERT_COUNT")
        frozen, ids, origins = [], set(), set()
        for spec in definitions:
            if not isinstance(spec, dict):
                raise ValueError("INVALID_EXPERT_DEFINITION")
            identity = spec.get("expert_id")
            if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in CORE_IDS or identity in ids:
                raise ValueError("DUPLICATE_RESERVED_OR_INVALID_EXPERT_ID")
            ids.add(identity)
            instrument, model = spec.get("instrument"), spec.get("model_version")
            label = spec.get("label", identity)
            maximum = _number(spec.get("max_age_sec"))
            regimes = spec.get("supported_regimes", ["ALL"])
            if (not isinstance(instrument, str) or not 1 <= len(instrument) <= 32
                    or not isinstance(model, str) or not 1 <= len(model) <= 128
                    or not isinstance(label, str) or not 1 <= len(label) <= 128
                    or maximum is None or not 0 < maximum <= 86400
                    or not isinstance(regimes, list) or not 1 <= len(regimes) <= 6
                    or any(item not in {"ALL", "TREND", "RANGE", "STRESS", "EVENT", "UNKNOWN"} for item in regimes)
                    or spec.get("score_semantics") != SCORE_SEMANTICS):
                raise ValueError("INVALID_EXPERT_DEFINITION")
            definition = ExpertDefinition(identity, label, instrument, model,
                _texts(spec.get("source_ids")), _texts(spec.get("evidence_family_ids")),
                maximum, tuple(sorted(set(regimes))))
            origin = (definition.instrument, definition.model_version, definition.source_ids,
                      definition.evidence_family_ids)
            if origin in origins:
                raise ValueError("DUPLICATED_EXPERT_ORIGIN")
            origins.add(origin)
            frozen.append(definition)
        frozen.sort(key=lambda item: item.expert_id)
        definition_json = json.dumps([asdict(item) for item in frozen], sort_keys=True, separators=(",", ":"))
        audit["definitions"] = json.loads(definition_json)
        audit["definition_sha256"] = hashlib.sha256(definition_json.encode()).hexdigest()
        budget_specs = raw.get("schemes")
        if not isinstance(budget_specs, dict) or not 1 <= len(budget_specs) <= MAX_SCHEMES:
            raise ValueError("EXPLICIT_FULL_BUDGET_REQUIRED")
        registered_ids = set(CORE_IDS) | ids
        admitted_schemes = []
        for name, weights in budget_specs.items():
            if not isinstance(name, str) or not _ID.fullmatch(name) or name in {"quant100", "legacy_control"}:
                audit["rejected_schemes"].append({"scheme": str(name)[:48], "reason": "RESERVED_OR_INVALID_SCHEME"})
                continue
            parsed = {key: _number(value) for key, value in weights.items()} if isinstance(weights, dict) else {}
            if (set(parsed) != registered_ids or any(value is None or not 0 <= value <= 1 for value in parsed.values())
                    or not math.isclose(sum(parsed.values()), 1., abs_tol=1e-9, rel_tol=0)):
                audit["rejected_schemes"].append({"scheme": name, "reason": "FULL_SUM_ONE_BUDGET_REQUIRED"})
                continue
            schemes[name] = parsed
            admitted_schemes.append(name)
        if not admitted_schemes:
            raise ValueError("NO_VALID_REGISTERED_BUDGET")
        # Frozen assessments cannot rewrite definition identity or lineage.
        assessments = raw.get("assessments")
        if not isinstance(assessments, list) or len(assessments) > MAX_EXPERTS:
            raise ValueError("INVALID_REGISTRY_ASSESSMENTS")
        by_id = {}
        for item in assessments:
            if not isinstance(item, dict) or item.get("expert_id") not in ids or item["expert_id"] in by_id:
                raise ValueError("DUPLICATE_OR_UNREGISTERED_ASSESSMENT")
            by_id[item["expert_id"]] = item
        candidate_ids = {row.get("candidate_id") for row in candidates if isinstance(row, dict)}
        policies = {"HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT", "MOVE_TO_BE",
                    "TIGHTEN_STOP", "TRAIL_GAMMA_FLIP", "EXTEND_TAKE", "REDUCE_TAKE",
                    "TIME_STOP", "SCALE_OUT_ON_SPIKE"}
        rows = []
        from .edge_family_adapters import build_edge_family_evidence
        source_lineage = {}
        source_receipts = {}
        for family in build_edge_family_evidence(snapshot)['families'].values():
            for meta in family.get('feature_provenance', {}).values():
                for source_id in (meta.get('source_id'), *meta.get('supporting_source_ids', [])):
                    if source_id and meta.get('dependency_group'):
                        source_lineage.setdefault(source_id, set()).add(meta['dependency_group'])
                        source_receipts.setdefault(source_id, []).append(_number(meta.get('received_ts')))
        cutoff = _number(snapshot.get('captured_ts'))
        for definition in frozen:
            item = by_id.get(definition.expert_id) or {}
            quality, observed = _number(item.get("quality")), _number(item.get("observed_ts"))
            received = _number(item.get('received_ts'))
            bound_lineage = set().union(*(source_lineage.get(source, set()) for source in definition.source_ids))
            sources_bound = (all(source in source_lineage for source in definition.source_ids)
                             and set(definition.evidence_family_ids) == bound_lineage)
            sources_causal = bool(observed is not None and sources_bound and all(
                receipt is not None and 0 < receipt <= observed
                for source in definition.source_ids for receipt in source_receipts[source]))
            raw_scores = item.get("scores")
            scores = {key: _number(value) for key, value in raw_scores.items()} if isinstance(raw_scores, dict) else {}
            valid = bool(item.get("available") is True and item.get("instrument") == definition.instrument
                and item.get("model_version") == definition.model_version
                and quality is not None and 0 <= quality <= 1 and observed is not None
                and received is not None and cutoff is not None and 0 < observed <= received <= cutoff
                and sources_causal
                and 1 <= len(scores) <= 32
                and all(isinstance(key, str) and (key in policies or key in candidate_ids)
                        and value is not None and -1 <= value <= 1 for key, value in scores.items()))
            row = {"component_id": definition.expert_id, "label": definition.label,
                "registered_expert": True, "registry_definition_sha256": audit["definition_sha256"],
                "instrument": definition.instrument, "model_version": definition.model_version,
                "source_ids": list(definition.source_ids), "evidence_family_ids": list(definition.evidence_family_ids),
                "max_age_sec": definition.max_age_sec, "supported_regimes": list(definition.supported_regimes),
                "regime_contract_version": item.get("regime_contract_version"), "observed_ts": observed,
                "received_ts": received, "source_lineage_verified": sources_bound,
                "sources_available_before_assessment": sources_causal,
                "quality": quality if quality is not None else 0., "available": valid,
                "scores": scores if valid else {}, "score_semantics": SCORE_SEMANTICS,
                "reason": ("REGISTERED_FROZEN_ACTION_PREFERENCE" if valid else
                           "SOURCE_LINEAGE_NOT_BOUND_TO_FROZEN_FACTS" if not sources_bound else
                           "SOURCE_NOT_AVAILABLE_BEFORE_ASSESSMENT" if not sources_causal else
                           "INVALID_OR_MISSING_EXPERT_ASSESSMENT")}
            rows.append(row)
            if not valid:
                audit["rejected_experts"].append({"expert_id": definition.expert_id, "reason": row["reason"]})
        audit.update(available=True, reason="EXPLICIT_REGISTERED_EXPERT_BUDGET", registered_expert_ids=sorted(ids),
                     admitted_schemes=admitted_schemes, budgets={name: schemes[name] for name in admitted_schemes})
        return rows, schemes, audit
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        audit["reason"] = str(exc)[:160]
        return [], {name: dict(weights) for name, weights in core_schemes.items()}, audit
