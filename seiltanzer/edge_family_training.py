"""Offline deterministic ridge admission for observed net-action datasets.

One frozen fit is evaluated in two subsequent untouched chronological blocks.
No providers, database writes, hyperparameter search, or runtime training occur.
Path counterfactual labels remain counterfactuals, never broker fills/profit.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math

import numpy as np

from .canonical_market_context import canonical_instrument_code
from .edge_family_adapters import FAMILIES, FEATURE_CONTRACT, MODEL_CONTRACT, MAX_AGE_SEC
from .execution_cost_context import COMPONENTS, VERSION as COST_VERSION
from .rollover_economics import frozen_rollover_schedule


VERSION = "edge-family-training-v1"
DATASET_VERSION = "edge-family-dataset-v1"
MAX_ROWS = 512 * 12 * 8
MAX_BYTES = 96 * 1024 * 1024
CONFIG = {"method": "ridge", "ridge_lambda": 1., "intercept_penalized": False,
          "scaling": "training_only_population_standard_deviation",
          "max_features": 32, "minimum_train_groups": 20,
          "validation_blocks": 2, "groups_per_validation_block": 10,
          "scoring_rule": "mean_squared_error", "baseline": "training_only_mean",
          "fit_policy": "one_frozen_fit_two_untouched_blocks"}
FEATURE_PREFIXES = {"macro": ("macro.",), "event": ("event.",),
    "order_flow": ("flow.",), "intermarket": ("intermarket.",),
    "positioning": ("positioning.",), "value_carry": ("value.", "carry."),
    "option": ("option.",), "session": ("session.",)}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _digest(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _identity(value):
    return ((isinstance(value, str) and bool(value.strip()))
            or (isinstance(value, int) and not isinstance(value, bool) and value > 0))


def _synthetic_evidence(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(item.get(key) is True for key in
                   ("synthetic", "demo", "is_demo", "synthetic_demo", "contract_fixture")):
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _costs_valid(row, captured, horizon):
    cost = row.get("cost_provenance")
    if (row.get("costs_verified") is not True or not isinstance(cost, dict)
            or cost.get("available") is not True
            or cost.get("complete_costs_available") is not True
            or cost.get("assumed") is not False
            or cost.get("version") != COST_VERSION
            or cost.get("reason") != "VERIFIED_COMPLETE_BROKER_COSTS"
            or cost.get("source_verified") is not True
            or type(cost.get("trade_id")) is not type(row["trade_id"])
            or cost.get("trade_id") != row["trade_id"]
            or cost.get("instrument") != row["instrument"]
            or cost.get("direction") not in ("long", "short")
            or any(not isinstance(cost.get(k), str) or not cost[k]
                   for k in ("source", "broker_id", "account_id", "currency"))
            or not _digest(cost.get("document_sha256"))
            or not _digest(cost.get("deployment_sha"), 40)
            or cost.get("quantity_basis") != "current_remaining_position"
            or _number(cost.get("horizon_minutes")) != horizon):
        return False
    start, end = (_number(cost.get(k)) for k in ("coverage_start_epoch", "coverage_end_epoch"))
    if start is None or end is None or start > captured or end < captured + horizon * 60:
        return False
    proof, units = cost.get("position_evidence"), cost.get("position_execution_units")
    if (not isinstance(proof, dict) or not isinstance(units, dict)
            or not isinstance(cost.get("broker_position_id"), str) or not cost["broker_position_id"]
            or proof.get("version") != "broker-position-context-v1"
            or proof.get("source_verified") is not True
            or proof.get("measurement_kind") != "executing_broker_position"
            or not isinstance(proof.get("source_id"), str) or not proof["source_id"]
            or any(not _digest(proof.get(k)) for k in ("evidence_sha256", "document_sha256"))
            or proof.get("deployment_sha") != cost["deployment_sha"]
            or any(units.get(k) != cost.get(k) for k in
                   ("currency", "quantity_basis", "quantity_units", "risk_currency_per_unit"))):
        return False
    po, pr, pa, entry, stop, fraction = (_number(proof.get(k)) for k in
        ("observed_ts", "received_ts", "max_age_sec", "entry", "original_stop", "remaining_position_fraction"))
    if (any(v is None for v in (po, pr, pa, entry, stop, fraction))
            or not 0 < po <= pr <= captured or not 0 < pa <= 60 or captured - po > pa
            or entry <= 0 or stop <= 0 or not 0 < fraction <= 1
            or (cost["direction"] == "long" and stop >= entry)
            or (cost["direction"] == "short" and stop <= entry)):
        return False
    observed, received, age, quantity, risk = (
        _number(cost.get(k)) for k in ("observed_ts", "received_ts", "max_age_sec",
                                      "quantity_units", "risk_currency_per_unit"))
    if (any(v is None for v in (observed, received, age, quantity, risk))
            or not 0 < observed <= received <= captured
            or not 0 < age <= 86400 or captured - observed > age
            or quantity <= 0 or risk <= 0):
        return False
    channels = cost.get("components")
    if not isinstance(channels, dict):
        return False
    for channel in ("immediate", "deferred"):
        supplied = channels.get(channel)
        if not isinstance(supplied, dict) or not isinstance(supplied.get("components_r"), dict):
            return False
        values = [_number(supplied["components_r"].get(k)) for k in COMPONENTS]
        total = _number(supplied.get("total_r"))
        missing = cost.get("missing_components")
        if (set(supplied["components_r"]) != set(COMPONENTS)
                or total is None or any(v is None or v < 0 for v in values)
                or not math.isclose(sum(values), total, rel_tol=0, abs_tol=1e-12)
                or _number(cost.get(channel + "_full_close_r")) != total
                or not isinstance(missing, dict) or missing.get(channel) != []):
            return False
        component_provenance = supplied.get("component_provenance")
        if not isinstance(component_provenance, dict) or set(component_provenance) != set(COMPONENTS):
            return False
        for name in COMPONENTS:
            item = component_provenance[name]
            if not isinstance(item, dict):
                return False
            measurement, ts = (_number(item.get(k)) for k in ("cost_currency_per_unit", "observed_ts"))
            if (measurement is None or measurement < 0 or ts is None or not 0 < ts <= observed
                    or captured - ts > age
                    or item.get("measurement_kind") not in ("executing_broker_quote", "measured_broker_fill")
                    or not isinstance(item.get("source_id"), str) or not item["source_id"]
                    or not _digest(item.get("evidence_sha256"))
                    or not math.isclose(measurement / risk, supplied["components_r"][name], rel_tol=0, abs_tol=1e-12)):
                return False
    rollover = cost.get("broker_rollover_audit")
    if (not _digest(cost.get("broker_rollover_schedule_sha256"))
            or not isinstance(rollover, dict)
            or rollover.get("version") != "broker-rollover-economics-v1"
            or rollover.get("available") is not True or rollover.get("applied") is not True
            or rollover.get("included_in_base_costs") is not False
            or rollover.get("reason") != "MEASURED_REMAINING_QUANTITY_ROLLOVER"
            or not isinstance(rollover.get("source_id"), str) or not rollover["source_id"]
            or rollover.get("currency") != cost["currency"]
            or rollover.get("same_timestamp_rule") not in ("fills_before_rollover", "rollover_before_fills")
            or not isinstance(rollover.get("event_n"), int) or isinstance(rollover["event_n"], bool)
            or not 0 <= rollover["event_n"] <= 256):
        return False
    roll_observed, roll_received = (_number(rollover.get(k)) for k in ("observed_ts", "received_ts"))
    if (roll_observed is None or roll_received is None
            or not 0 < roll_observed <= roll_received <= captured):
        return False
    schedule = cost.get("broker_rollover_schedule")
    if (not isinstance(schedule, dict) or _hash(schedule) != cost["broker_rollover_schedule_sha256"]
            or any(schedule.get(k) != cost.get(k) for k in ("broker_id", "account_id", "trade_id",
                "instrument", "direction", "currency", "quantity_basis", "quantity_units", "risk_currency_per_unit"))):
        return False
    try:
        _, audit = frozen_rollover_schedule({"captured_ts": captured, "instrument": row["instrument"],
                                            "broker_rollover_schedule": schedule}, horizon)
    except (ValueError, TypeError, OverflowError):
        return False
    if audit != rollover:
        return False
    return True


def _feature_reason(meta, instrument, family, captured, *, allow_global=False):
    if (not isinstance(meta, dict) or meta.get("source_verified") is not True
            or meta.get("context_only") or not isinstance(meta.get("source_id"), str) or not meta["source_id"]):
        return "FEATURE_VALUE_OR_PROVENANCE_INVALID"
    observed, received, published = (_number(meta.get(k)) for k in ("observed_ts", "received_ts", "published_at"))
    if (observed is None or received is None or not 0 < observed <= received <= captured
            or (meta.get("published_at") is not None and (published is None or not 0 < published <= received))):
        return "FEATURE_POINT_IN_TIME_CLOCK_INVALID"
    age, quality = (_number(meta.get(k)) for k in ("max_age_sec", "quality"))
    if (age is None or not 0 < age <= MAX_AGE_SEC[family] or captured - observed > age
            or quality is None or not 0 < quality <= 1
            or not isinstance(meta.get("dependency_group"), str) or not meta["dependency_group"]):
        return "FEATURE_FRESHNESS_OR_QUALITY_INVALID"
    supporting = meta.get("supporting_source_ids", [])
    if not isinstance(supporting, list) or any(not isinstance(v, str) or not v for v in supporting):
        return "FEATURE_VALUE_OR_PROVENANCE_INVALID"
    source_instrument = meta.get("source_instrument")
    if source_instrument != instrument and not (allow_global and meta.get("global_context") is True):
        mapping = meta.get("proxy_mapping")
        validated = _number(mapping.get("validated_at")) if isinstance(mapping, dict) else None
        if not (isinstance(source_instrument, str) and source_instrument
                and isinstance(mapping, dict) and mapping.get("validated") is True
                and canonical_instrument_code(mapping.get("source_instrument")) == source_instrument
                and canonical_instrument_code(mapping.get("target_instrument")) == instrument
                and isinstance(mapping.get("mapping_id"), str) and mapping["mapping_id"]
                and validated is not None and 0 < validated <= captured):
            return "FEATURE_INSTRUMENT_OR_MAPPING_INVALID"
    return None


def _row_reason(row, trained):
    if not isinstance(row, dict):
        return "ROW_SCHEMA_INVALID"
    if row.get("synthetic") is not False or _synthetic_evidence(row):
        return "SYNTHETIC_OR_UNDECLARED_ORIGIN"
    if any(not _identity(row.get(k)) for k in ("trade_id", "review_id")):
        return "ROW_IDENTITY_INVALID"
    captured, end, horizon, target = (_number(row.get(k)) for k in
        ("captured_ts", "label_end_ts", "horizon_minutes", "delta_net_r"))
    if (any(v is None for v in (captured, end, horizon, target))
            or not 0 < captured < end <= trained or horizon <= 0):
        return "NONFINITE_OR_FUTURE_LABEL_CLOCK"
    if (row.get("family_id") not in FAMILIES
            or not isinstance(row.get("instrument"), str) or not row["instrument"]
            or canonical_instrument_code(row["instrument"]) != row["instrument"]
            or not _digest(row.get("geometry_sha256"))
            or not isinstance(row.get("action"), str) or not row["action"]
            or row["action"] == "HOLD"):
        return "COHORT_IDENTITY_OR_GEOMETRY_INVALID"
    from .edge_family_geometry import portable_row_reason
    reason = portable_row_reason(row)
    if reason:
        return reason
    from .edge_family_action_binding import action_binding_valid, candidate_binding, validate_binding
    if not action_binding_valid(row['action'], row):
        return 'TIME_STOP_ACTION_BINDING_INVALID'
    if 'action_binding' in row:
        try:
            if candidate_binding(row.get('candidate'), captured) != validate_binding(row['action_binding']):
                return 'TIME_STOP_ACTION_BINDING_INVALID'
        except (ValueError, TypeError, OverflowError):
            return 'TIME_STOP_ACTION_BINDING_INVALID'
    if not _costs_valid(row, captured, horizon):
        return "COST_PROVENANCE_UNVERIFIED"
    features, provenance, windows = (row.get(k) for k in
        ("features", "feature_provenance", "feature_windows_sec"))
    if (not isinstance(features, dict) or not 1 <= len(features) <= 32
            or not isinstance(provenance, dict) or not isinstance(windows, dict)
            or not set(windows).issubset(features)):
        return "FEATURE_SCHEMA_INVALID_OR_OVER_BOUND"
    history_bindings = {}
    reaction_bindings = {}
    release_bindings = {}
    for feature, value in features.items():
        meta = provenance.get(feature)
        if (not isinstance(feature, str) or not feature
                or not feature.startswith(FEATURE_PREFIXES[row["family_id"]])
                or _number(value) is None
                or not isinstance(meta, dict) or meta.get("context_only")
                or not isinstance(meta.get("source_id"), str) or not meta["source_id"]):
            return "FEATURE_VALUE_OR_PROVENANCE_INVALID"
        reason = _feature_reason(meta, row["instrument"], row["family_id"], captured,
                                 allow_global=row["family_id"] in {"macro", "event"})
        if reason:
            return reason
        from .edge_family_event_reaction import REACTION_NAME, reaction_provenance_reason
        reaction = bool(REACTION_NAME.fullmatch(feature))
        reason = reaction_provenance_reason(feature, value, meta, captured, horizon, row['instrument'])
        if reason:
            return reason
        from .edge_family_event_novelty import FEATURE as NOVELTY_FEATURE, novelty_provenance_reason
        novelty = feature == NOVELTY_FEATURE
        reason = novelty_provenance_reason(feature, value, meta, captured, horizon, row['instrument'])
        if reason:
            return reason
        if reaction:
            for proof in meta['constituent_provenance']:
                source_id = proof['source_id']
                binding = _canonical(proof)
                if source_id in reaction_bindings and reaction_bindings[source_id] != binding:
                    return 'FEATURE_REACTION_PROVENANCE_INVALID'
                reaction_bindings[source_id] = binding
        if reaction or novelty:
            for proof in meta['constituent_provenance']:
                if proof.get('hash_kind') != 'OFFICIAL_NORMALIZED_DOCUMENT_SHA256':
                    continue
                # Reaction availability is HTTP receipt; novelty also retains
                # local import and a sentence projection. Bind common original
                # document identity rather than comparing unequal proof schemas.
                release_id = proof['release_id']
                binding = tuple(proof[key] for key in ('source_url', 'body_sha256', 'published_at', 'received_ts'))
                if release_id in release_bindings and release_bindings[release_id] != binding:
                    return 'FEATURE_EVENT_RELEASE_IDENTITY_CONFLICT'
                release_bindings[release_id] = binding
        from .edge_family_history import feature_applicability_reason, history_provenance_reason
        reason = (feature_applicability_reason(meta, horizon) or
                  (None if reaction or novelty else history_provenance_reason(feature, meta, captured, horizon, row['instrument'])))
        if reason:
            return reason
        if 'history_contract_version' in meta:
            for proof in meta['constituent_provenance']:
                # A source ID cannot acquire a different body/series inside one
                # frozen row merely through correlated transforms.
                binding = tuple(proof.get(key) for key in ('body_sha256', 'series_id',
                    'kind', 'unit', 'category', 'venue', 'provider', 'symbol',
                    'base_currency', 'quote_currency', 'orientation'))
                source_id = proof['source_id']
                if source_id in history_bindings and history_bindings[source_id] != binding:
                    return 'FEATURE_HISTORY_PROVENANCE_INVALID'
                history_bindings[source_id] = binding
        if row["family_id"] == "event" and not (reaction or novelty):
            consensus = meta.get("consensus_provenance")
            if (not isinstance(consensus, dict)
                    or _feature_reason(consensus, row["instrument"], "macro", captured, allow_global=True)
                    or _number(meta.get("published_at")) is None
                    or consensus["observed_ts"] >= meta["published_at"]
                    or consensus["received_ts"] >= meta["published_at"]
                    or consensus["source_id"] != meta.get("consensus_source_id")
                    or consensus["source_id"] not in meta.get("supporting_source_ids", [])
                    or consensus["received_ts"] != meta.get("consensus_received_ts")
                    or any(not isinstance(meta.get(k), str) or not meta[k] or meta[k] != consensus.get(k)
                           for k in ("release_id", "period", "unit"))):
                return "FEATURE_PREPUBLICATION_CONSENSUS_INVALID"
        window = meta.get("window_seconds")
        if window is not None or feature in windows:
            window = _number(window)
            if window is None or window <= 0 or _number(windows.get(feature)) != window:
                return "FEATURE_WINDOW_MISMATCH"
    return None


def _cohort_key(row):
    return (row["instrument"], row["family_id"], float(row["horizon_minutes"]),
            row["geometry_sha256"], row["action"], tuple(sorted(row["features"])),
            tuple(sorted((k, float(v)) for k, v in row["feature_windows_sec"].items())),
            row.get("geometry_contract", ""))


def _fit(rows, features):
    x = np.asarray([[r["features"][name] for name in features] for r in rows], dtype=float)
    y = np.asarray([r["delta_net_r"] for r in rows], dtype=float)
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale = np.where(scale > 0, scale, 1.)
        z = (x - mean) / scale
        centered = y - y.mean()
        beta = np.linalg.solve(z.T @ z + np.eye(len(features)), z.T @ centered) / scale
        intercept = float(y.mean() - mean @ beta)
    if not np.all(np.isfinite(beta)) or not math.isfinite(intercept):
        raise ValueError("nonfinite ridge fit")
    return intercept, beta, float(y.mean())


def _train_cohort(key, rows, dataset_hash, trained):
    instrument, family, horizon, geometry, action, features, windows, geometry_contract = key
    groups = defaultdict(list)
    for row in rows:
        groups[str(row["trade_id"])].append(row)
    ordered = sorted(groups, key=lambda group: (min(r["captured_ts"] for r in groups[group]), group))
    diagnostic = {"instrument": instrument, "family_id": family, "horizon_minutes": horizon,
                  "geometry_sha256": geometry, "action": action, "row_count": len(rows),
                  "group_count": len(groups), "folds": [], "available": False}
    if len(ordered) < 40:
        diagnostic["reason"] = "INSUFFICIENT_INDEPENDENT_GROUPS"
        return None, diagnostic
    validation_groups = ordered[-20:]
    blocks = [validation_groups[:10], validation_groups[10:]]
    second_start = min(r["captured_ts"] for group in blocks[1] for r in groups[group])
    first_end = max(max(r["label_end_ts"], r["captured_ts"] + horizon * 60)
                    for group in blocks[0] for r in groups[group])
    start = min(r["captured_ts"] for group in validation_groups for r in groups[group])
    training_groups = [group for group in ordered[:-20]
                       if all(r["label_end_ts"] < start and r["captured_ts"] + horizon * 60 <= start
                              for r in groups[group])]
    train = [r for group in training_groups for r in groups[group]]
    purged = len(ordered) - 20 - len(training_groups)
    if len(training_groups) < 20:
        diagnostic.update(reason="INSUFFICIENT_TRAIN_GROUPS_AFTER_PURGE",
                          train_group_count=len(training_groups), purged_group_count=purged)
        return None, diagnostic
    if first_end >= second_start:
        diagnostic.update(reason="VALIDATION_BLOCKS_NOT_CHRONOLOGICAL",
                          first_block_end_ts=first_end, second_block_start_ts=second_start)
        return None, diagnostic
    try:
        intercept, beta, baseline = _fit(train, features)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        diagnostic["reason"] = "RIDGE_FIT_NONFINITE_OR_FAILED"
        return None, diagnostic
    model_errors, baseline_errors = [], []
    for fold_number, block in enumerate(blocks, 1):
        validation = [r for group in block for r in groups[group]]
        x = np.asarray([[r["features"][name] for name in features] for r in validation], dtype=float)
        y = np.asarray([r["delta_net_r"] for r in validation], dtype=float)
        with np.errstate(over="ignore", invalid="ignore"):
            model_error = (y - (intercept + x @ beta)) ** 2
            baseline_error = (y - baseline) ** 2
        if not np.all(np.isfinite(model_error)) or not np.all(np.isfinite(baseline_error)):
            diagnostic["reason"] = "VALIDATION_SCORE_NONFINITE"
            return None, diagnostic
        model_errors.extend(model_error.tolist())
        baseline_errors.extend(baseline_error.tolist())
        try:
            with np.errstate(over="raise", invalid="raise"):
                fold_model_mse, fold_baseline_mse = float(model_error.mean()), float(baseline_error.mean())
        except FloatingPointError:
            diagnostic["reason"] = "VALIDATION_SCORE_NONFINITE"
            return None, diagnostic
        diagnostic["folds"].append({"fold": fold_number, "train_row_count": len(train),
            "train_group_count": len(training_groups), "purged_group_count": purged,
            "validation_row_count": len(validation), "validation_group_count": len(block),
            "train_end_ts": max(r["captured_ts"] for r in train),
            "validation_start_ts": min(r["captured_ts"] for r in validation),
            "validation_end_ts": max(r["label_end_ts"] for r in validation),
            "model_mse": fold_model_mse, "baseline_mse": fold_baseline_mse,
            "proper_score_gain": fold_baseline_mse - fold_model_mse})
    try:
        with np.errstate(over="raise", invalid="raise"):
            model_mse, baseline_mse = float(np.mean(model_errors)), float(np.mean(baseline_errors))
    except FloatingPointError:
        diagnostic["reason"] = "VALIDATION_SCORE_NONFINITE"
        return None, diagnostic
    gain = baseline_mse - model_mse
    diagnostic.update(proper_score_gain=gain, model_mse=model_mse, baseline_mse=baseline_mse,
                      train_row_count=len(train), train_group_count=len(training_groups))
    if not gain > 0:
        diagnostic["reason"] = "NO_POSITIVE_OOS_MSE_GAIN"
        return None, diagnostic
    validation = [r for group in validation_groups for r in groups[group]]
    used_rows = train + validation
    artifact = {"contract_version": MODEL_CONTRACT, "feature_contract_version": FEATURE_CONTRACT,
        "trainer_version": VERSION, "config_sha256": _hash(CONFIG), "training_config": dict(CONFIG),
        "code_sha256": hashlib.sha256(__loader__.get_data(__file__)).hexdigest(),
        "family_id": family, "instrument": instrument, "geometry_sha256": geometry,
        "component_id": "mathematical_edge", "regime": "ALL", "dataset_sha256": dataset_hash,
        "trained_at": trained, "train_end_ts": max(r["captured_ts"] for r in train),
        "train_label_end_ts": max(r["label_end_ts"] for r in train),
        "validation_start_ts": start, "validation_end_ts": max(r["label_end_ts"] for r in validation),
        "horizon_minutes": horizon, "score_scale_r": 1., "max_model_age_sec": 30 * 86400.,
        "feature_windows_sec": dict(windows),
        "source_ids": sorted({source for r in used_rows for name in features
            for source in (r["feature_provenance"][name]["source_id"],
                           *r["feature_provenance"][name].get("supporting_source_ids", []))}),
        "evidence_family_ids": sorted({str(r["feature_provenance"][name].get("dependency_group"))
                              for r in used_rows for name in features
                              if r["feature_provenance"][name].get("dependency_group")}),
        "cost_document_sha256": sorted({r["cost_provenance"]["document_sha256"] for r in used_rows}),
        "validation": {"status": "OOS_VALIDATED", "point_in_time": True,
            "outcomes": "OBSERVED_NET_ACTION_DELTA_VS_HOLD", "purged_split": True,
            "sample_count": len(validation), "group_count": len(validation_groups), "fold_count": 2,
            "proper_score_gain": gain, "model_mse": model_mse, "baseline_mse": baseline_mse,
            "baseline_mean_r": baseline, "costs_included": True,
            "train_row_count": len(train), "train_group_count": len(training_groups),
            "folds": diagnostic["folds"], "fit_policy": CONFIG["fit_policy"],
            "evidence_kind": "OBSERVED_PATH_COUNTERFACTUAL_NOT_BROKER_FILL"},
        "action_models": {action: {"validated": True, "kind": "linear_net_action",
            "intercept_r": intercept, "coefficients": dict(zip(features, map(float, beta)))}}}
    if geometry_contract:
        from copy import deepcopy
        artifact.update(geometry_contract=geometry_contract,
                        geometry_descriptor=deepcopy(rows[0]['geometry_descriptor']))
    if 'action_binding' in rows[0]:
        from .edge_family_action_binding import validate_binding
        artifact['action_models'][action]['action_binding'] = validate_binding(rows[0]['action_binding'])
    digest = _hash(artifact)
    artifact.update(model_sha256=digest, model_version=VERSION + ":" + digest)
    diagnostic.update(available=True, reason="VALIDATED_PIT_NET_ACTION_FORECAST", model_sha256=digest)
    return artifact, diagnostic


def train_family_models(dataset: dict, *, trained_at: float) -> dict:
    """Return admitted artifacts or explicit diagnostics; never weaken floors.

    The last twenty distinct trades form two ten-group validation blocks.
    All earlier groups are purged as whole trades against the first holdout.
    Feature/scaling choices and the mean baseline use only those training rows.
    """
    diagnostics = {"input_row_count": 0, "accepted_row_count": 0,
                   "rejected_row_count": 0, "exclusions": [], "cohorts": []}
    result = {"version": VERSION, "models": [], "diagnostics": diagnostics,
              "available": False, "reason": "NO_VALIDATED_MODELS"}
    trained = _number(trained_at)
    if (not isinstance(dataset, dict) or dataset.get("version") != DATASET_VERSION
            or not isinstance(dataset.get("rows"), list) or trained is None or trained <= 0):
        result["reason"] = "DATASET_OR_TRAINED_CLOCK_INVALID"
        return result
    rows = dataset["rows"]
    diagnostics["input_row_count"] = len(rows)
    if dataset.get("synthetic") is True:
        result["reason"] = "SYNTHETIC_DATASET_NOT_PUBLISHABLE"
        diagnostics["rejected_row_count"] = len(rows)
        return result
    if len(rows) > MAX_ROWS:
        result["reason"] = "DATASET_EXCEEDS_ROW_BOUND"
        return result
    try:
        serialized = _canonical(rows)
    except (ValueError, TypeError, OverflowError, RecursionError):
        result["reason"] = "DATASET_NONFINITE_OR_INVALID_JSON"
        diagnostics["rejected_row_count"] = len(rows)
        return result
    if len(serialized) > MAX_BYTES:
        result["reason"] = "DATASET_EXCEEDS_BYTE_BOUND"
        return result
    if (not _digest(dataset.get("dataset_sha256"))
            or hashlib.sha256(serialized).hexdigest() != dataset["dataset_sha256"]):
        result["reason"] = "DATASET_SHA256_MISMATCH"
        return result
    cohorts, identities = defaultdict(list), defaultdict(list)
    for row in rows:
        reason = _row_reason(row, trained)
        if reason:
            diagnostics["exclusions"].append({"trade_id": row.get("trade_id") if isinstance(row, dict) else None,
                "review_id": row.get("review_id") if isinstance(row, dict) else None, "reason": reason})
        else:
            identity = (str(row["trade_id"]), str(row["review_id"]), row["instrument"],
                        row["family_id"], float(row["horizon_minutes"]), row["action"])
            identities[identity].append(row)
    duplicate_count = 0
    for identity in sorted(identities):
        variants = identities[identity]
        if len({_hash(row) for row in variants}) > 1:
            diagnostics["exclusions"].extend({"trade_id": row["trade_id"],
                "review_id": row["review_id"], "reason": "CONFLICTING_REVIEW_IDENTITY"}
                for row in variants)
        else:
            row = variants[0]
            cohorts[_cohort_key(row)].append(row)
            duplicate_count += len(variants) - 1
    diagnostics["duplicate_row_count"] = duplicate_count
    diagnostics["rejected_row_count"] = len(diagnostics["exclusions"])
    diagnostics["accepted_row_count"] = len(rows) - diagnostics["rejected_row_count"] - duplicate_count
    for key in sorted(cohorts):
        ordered_rows = sorted(cohorts[key], key=lambda r: (r["captured_ts"], str(r["trade_id"]), str(r["review_id"])))
        artifact, diagnostic = _train_cohort(key, ordered_rows, dataset["dataset_sha256"], trained)
        diagnostics["cohorts"].append(diagnostic)
        if artifact:
            result["models"].append(artifact)
    if result["models"]:
        result.update(available=True, reason="VALIDATED_PIT_NET_ACTION_MODELS")
    return result
