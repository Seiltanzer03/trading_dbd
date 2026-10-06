"""Pinned local forecast/expert input. Reading never trains or calls a provider."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .edge_family_adapters import FAMILIES
from .expert_registry import resolve_expert_registry

VERSION = "unified-edge-runtime-context-v1"
MAX_BYTES = 48_000


def load_unified_edge_context(path, *, expected_document_sha256, expected_sha, snapshot):
    audit = {"version": VERSION, "available": False, "reason": "UNCONFIGURED",
             "production_authority": False, "automatic_execution_allowed": False}
    if not path:
        return {}, audit
    if not isinstance(expected_document_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_document_sha256):
        return {}, {**audit, "reason": "DOCUMENT_HASH_UNAVAILABLE"}
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("CONTEXT_EXCEEDS_BYTE_BOUND")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_document_sha256:
            raise ValueError("DOCUMENT_HASH_MISMATCH")
        document = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_CONTEXT")))
        if (not isinstance(document, dict) or document.get("version") != VERSION
                or not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha)
                or document.get("deployment_sha") != expected_sha):
            raise ValueError("CONTEXT_SCHEMA_OR_DEPLOYMENT_SHA_INVALID")
        cutoff = snapshot.get("captured_ts")
        captured = document.get("captured_ts")
        if (isinstance(captured, bool) or not isinstance(captured, (float, int))
                or not isinstance(cutoff, (float, int)) or not 0 < captured <= cutoff
                or cutoff - captured > 86400):
            raise ValueError("CONTEXT_CLOCK_INVALID_OR_STALE")
        instrument = (snapshot.get("strategy") or {}).get("instrument") or snapshot.get("instrument")
        inputs = document.get("instruments")
        if not isinstance(inputs, dict) or len(inputs) > 32:
            raise ValueError("CONTEXT_INSTRUMENT_MATRIX_INVALID")
        selected = inputs.get(instrument)
        if not isinstance(selected, dict):
            raise ValueError("INSTRUMENT_CONTEXT_UNAVAILABLE")
        output = {}
        registry = selected.get("expert_registry")
        if registry is not None:
            from .unified_edge_ensemble import SCHEMES, collect_candidates
            _, _, registry_audit = resolve_expert_registry(
                {**snapshot, "policy_manager": {**(snapshot.get("policy_manager") or {}),
                                               "expert_registry": registry}},
                SCHEMES, collect_candidates(snapshot))
            audit["expert_registry"] = registry_audit
            if registry_audit.get("available") is True:
                output["expert_registry"] = registry
        models = selected.get("edge_family_models")
        if models is not None:
            if (not isinstance(models, dict) or not set(models).issubset(FAMILIES)
                    or any(not isinstance(model, (dict, list)) for model in models.values())):
                raise ValueError("FAMILY_MODEL_MATRIX_INVALID")
            # The existing adapter checks PIT/purged observed net-action
            # validation and actual current feature lineage before any vote.
            output["edge_family_models"] = models
        audit.update(available=bool(output), reason="PINNED_CONTEXT_LOADED_PENDING_PER_EXPERT_ADMISSION",
                     document_sha256=digest, deployment_sha=expected_sha,
                     captured_ts=captured, model_families=sorted(models or {}))
        return output, audit
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        return {}, {**audit, "reason": str(exc)[:160]}


def attach_unified_edge_context(engine, snapshot, *, expected_sha):
    if isinstance(expected_sha, str) and re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        snapshot['runtime_code_sha'] = expected_sha
    settings = engine.settings
    context, audit = load_unified_edge_context(
        getattr(settings, "unified_edge_context_path", ""),
        expected_document_sha256=getattr(settings, "unified_edge_context_sha256", ""),
        expected_sha=expected_sha, snapshot=snapshot)
    snapshot["unified_edge_context_audit"] = audit
    if "expert_registry" in context:
        snapshot.setdefault("policy_manager", {})["expert_registry"] = context["expert_registry"]
    if "edge_family_models" in context:
        # Preserve an existing explicit model configuration; do not overwrite
        # an independent model with a stale imported artifact.
        existing = snapshot.setdefault("edge_family_models", {})
        if isinstance(existing, dict):
            for family, models in context["edge_family_models"].items():
                existing.setdefault(family, models)
    from .position_execution_context import load_position_execution_context
    position_path = getattr(settings, "position_execution_context_path", "")
    position = load_position_execution_context(
        position_path, snapshot=snapshot,
        expected_deployment_sha=expected_sha,
        expected_document_sha256=getattr(settings, "position_execution_context_sha256", ""))
    snapshot["position_execution_context_audit"] = position["audit"]
    if position.get("available") is True:
        for root in ("trade_identity", "position_execution_units"):
            snapshot.setdefault(root, {}).update(position[root])
    from .execution_cost_context import load_execution_cost_context, _unavailable
    if position_path and position.get("available") is not True:
        # An explicitly selected independent position source failed admission.
        # Do not let legacy/unverified snapshot claims bypass that rejection.
        costs = _unavailable("EXECUTING_BROKER_POSITION_CONTEXT_UNAVAILABLE_OR_MISMATCH")
    else:
        costs = load_execution_cost_context(
            getattr(settings, "execution_cost_context_path", ""), snapshot=snapshot,
            expected_deployment_sha=expected_sha,
            expected_document_sha256=getattr(settings, "execution_cost_context_sha256", ""))
    snapshot["execution_cost_context_audit"] = costs
    if costs.get("complete_costs_available") is True:
        manager = snapshot.setdefault("policy_manager", {})
        manager["execution_cost_model"] = costs
        manager["execution_cost_repricing_required"] = True
        manager.setdefault("selection_rule", {})["execution_cost_model"] = costs
    return audit
