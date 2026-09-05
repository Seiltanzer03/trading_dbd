"""Deterministic Phase-2 evaluation for LLM-proposed edge hypotheses.

The LLM is finished before this module starts.  Each run is evaluated against a
frozen evidence cutoff equal to the original proposal timestamp.  Only outcomes
that were already resolved by that cutoff are admissible.  Numeric thresholds
are fitted inside each chronological train fold by the existing EDE rule fitter.

Results are immutable research artifacts.  A statistical discovery signal is
not prospective confirmation and never receives Position Manager, CVaR, stop,
size, Active Edge, or production-policy authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from typing import Any

from .edge_discovery.filters import CandidateTemplate, ConditionTemplate
from .edge_discovery.prospective import ProspectiveFeatureAdapter
from .edge_discovery.scoring import benjamini_hochberg
from .edge_discovery.universal_outcome_adapter import ProspectiveUniversalOutcomeAdapter
from .edge_discovery.universal_structured_discovery import (
    MAX_Q_VALUE,
    MIN_OUTER_TEST_RAW,
    MIN_RELATIVE_IMPROVEMENT,
    MIN_STABLE_FOLDS,
    _aggregate_candidate,
    _evaluate_rule,
)
from .edge_discovery.universal_target_scoring import (
    UniversalTargetSpec,
    eligible_target_rows,
    universal_target_specs,
)
from .g1_short_horizon_historical_wf import _historical_folds


CONTRACT_VERSION = "llm-edge-deterministic-evaluator-v1"
MEASUREMENT_CONTRACT = "predeclared-prospective-t0-walk-forward-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite_json(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value in LLM edge evaluation dataset")
        return value
    if isinstance(value, dict):
        return {str(key): _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported evaluation fingerprint value: {type(value).__name__}")


def _ensure_tables(runtime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_edge_evaluations(
                evaluation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                hypothesis_id TEXT NOT NULL,
                evaluation_cutoff_ts REAL NOT NULL,
                dataset_sha256 TEXT NOT NULL,
                measurement_contract TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_ts REAL NOT NULL,
                UNIQUE(hypothesis_id,dataset_sha256,measurement_contract)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_llm_edge_evaluations_run "
            "ON llm_edge_evaluations(run_id,created_ts)")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS llm_edge_evaluations_immutable_update
            BEFORE UPDATE ON llm_edge_evaluations
            BEGIN SELECT RAISE(ABORT,'immutable llm edge evaluation row'); END""")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS llm_edge_evaluations_immutable_delete
            BEFORE DELETE ON llm_edge_evaluations
            BEGIN SELECT RAISE(ABORT,'immutable llm edge evaluation row'); END""")


def _load_run(runtime, run_id: str | None) -> dict[str, Any] | None:
    with runtime._lock:
        if run_id:
            row = runtime._conn.execute(
                "SELECT * FROM llm_edge_research_runs WHERE run_id=? LIMIT 1",
                (str(run_id),),
            ).fetchone()
        else:
            row = runtime._conn.execute(
                "SELECT * FROM llm_edge_research_runs ORDER BY created_ts DESC LIMIT 1"
            ).fetchone()
    return None if row is None else dict(row)


def _load_hypotheses(runtime, hypothesis_ids: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    with runtime._lock:
        for hypothesis_id in hypothesis_ids:
            row = runtime._conn.execute(
                "SELECT * FROM llm_edge_hypotheses WHERE hypothesis_id=? LIMIT 1",
                (str(hypothesis_id),),
            ).fetchone()
            if row is None:
                continue
            value = dict(row)
            output.append({
                "hypothesis_id": str(value["hypothesis_id"]),
                "target_id": str(value["target_id"]),
                "target_family": str(value["target_family"]),
                "horizon_minutes": int(value["horizon_minutes"]),
                "conditions": json.loads(str(value["conditions_json"])),
                "source": str(value["source"]),
            })
    return output


def _resolved_rows_at_cutoff(runtime, cutoff_ts: float) -> list[dict[str, Any]]:
    """Return only prospective T0 rows whose outcomes existed by ``cutoff_ts``.

    ``ProspectiveFeatureAdapter.available_asof`` gates target time, while the
    explicit ``resolved_ts <= cutoff`` check below also gates database arrival
    time.  This prevents a later resolution from leaking into an older proposal.
    """
    adapter = ProspectiveFeatureAdapter(runtime, available_asof=float(cutoff_ts))
    rows = adapter.rows(resolved_only=False, strict=False)
    admissible = []
    for row in rows:
        resolved_ts = row.get("resolved_ts")
        if not bool(row.get("outcome_available")) or resolved_ts is None:
            continue
        if float(row["target_ts"]) > float(cutoff_ts) + 1e-6:
            continue
        if float(resolved_ts) > float(cutoff_ts) + 1e-6:
            continue
        admissible.append(row)
    return ProspectiveUniversalOutcomeAdapter(runtime).attach(admissible)


def _barrier_ids(rows: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for row in rows:
        outcome = row.get("universal_outcome") or {}
        values.update(str(key) for key in (outcome.get("barriers") or {}).keys())
    return values


def _specs(rows: list[dict[str, Any]]) -> dict[str, UniversalTargetSpec]:
    return {item.target_id: item for item in universal_target_specs(_barrier_ids(rows))}


def _template(hypothesis: dict[str, Any]) -> CandidateTemplate:
    return CandidateTemplate(tuple(
        ConditionTemplate(
            str(condition["feature_id"]),
            str(condition["kind"]),
            str(condition["state"]),
        )
        for condition in hypothesis.get("conditions") or []
    ))


def _dataset_sha(
    rows: list[dict[str, Any]], hypothesis: dict[str, Any], *, cutoff_ts: float,
) -> str:
    feature_ids = sorted({
        str(condition.get("feature_id"))
        for condition in hypothesis.get("conditions") or []
    })
    projected = []
    for row in sorted(rows, key=lambda item: (
        float(item["captured_ts"]), str(item["instrument"]),
        str(item["observation_id"]),
    )):
        features = row.get("ede_features") or {}
        projected.append({
            "observation_id": str(row["observation_id"]),
            "instrument": str(row["instrument"]),
            "asset_family": features.get("regime.asset_family", features.get("asset_family")),
            "captured_ts": float(row["captured_ts"]),
            "target_ts": float(row["target_ts"]),
            "resolved_ts": float(row["resolved_ts"]),
            "horizon_minutes": int(row["horizon_minutes"]),
            "universal_target_id": row.get("universal_target_id"),
            "universal_target_value": row.get("universal_target_value"),
            "features": {feature_id: features.get(feature_id) for feature_id in feature_ids},
            "prospective_adapter_version": row.get("prospective_adapter_version"),
            "universal_outcome_adapter_version": (
                (row.get("universal_outcome") or {}).get("adapter_version")
            ),
        })
    return _sha(_finite_json({
        "contract_version": CONTRACT_VERSION,
        "measurement_contract": MEASUREMENT_CONTRACT,
        "evaluation_cutoff_ts": float(cutoff_ts),
        "hypothesis_id": str(hypothesis["hypothesis_id"]),
        "target_id": str(hypothesis["target_id"]),
        "horizon_minutes": int(hypothesis["horizon_minutes"]),
        "feature_ids": feature_ids,
        "rows": projected,
    }))


def _insufficient(
    hypothesis: dict[str, Any], dataset_sha256: str, reason: str, *,
    raw_rows: int, target_rows: int, fold_count: int = 0,
) -> dict[str, Any]:
    return {
        "hypothesis_id": str(hypothesis["hypothesis_id"]),
        "target_id": str(hypothesis["target_id"]),
        "target_family": str(hypothesis["target_family"]),
        "horizon_minutes": int(hypothesis["horizon_minutes"]),
        "dataset_sha256": str(dataset_sha256),
        "evaluation_state": "INSUFFICIENT_DATA",
        "status": "INSUFFICIENT_DATA",
        "reason": str(reason),
        "raw_rows": int(raw_rows),
        "target_rows": int(target_rows),
        "fold_count": int(fold_count),
        "p_value": None,
        "q_value": None,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
        "prospective_confirmation": False,
    }


def _evaluate_one(
    rows: list[dict[str, Any]], hypothesis: dict[str, Any], *,
    cutoff_ts: float, specs: dict[str, UniversalTargetSpec],
) -> dict[str, Any]:
    horizon = int(hypothesis["horizon_minutes"])
    horizon_rows = [row for row in rows if int(row["horizon_minutes"]) == horizon]
    spec = specs.get(str(hypothesis["target_id"]))
    if spec is None:
        dataset_sha256 = _dataset_sha(horizon_rows, hypothesis, cutoff_ts=cutoff_ts)
        return _insufficient(
            hypothesis, dataset_sha256, "TARGET_NOT_AVAILABLE_IN_FROZEN_EVIDENCE",
            raw_rows=len(horizon_rows), target_rows=0,
        )

    target_rows = eligible_target_rows(horizon_rows, spec)
    dataset_sha256 = _dataset_sha(target_rows, hypothesis, cutoff_ts=cutoff_ts)
    if not target_rows:
        return _insufficient(
            hypothesis, dataset_sha256, "NO_ELIGIBLE_TARGET_ROWS",
            raw_rows=len(horizon_rows), target_rows=0,
        )

    template = _template(hypothesis)
    folds = _historical_folds(target_rows, horizon)
    occurrences: list[dict[str, Any]] = []
    for fold in folds:
        evaluation = _evaluate_rule(template, fold["train"], fold["test"], spec)
        if evaluation is None or len(evaluation["rows"]) < MIN_OUTER_TEST_RAW:
            continue
        occurrences.append({
            "fold_index": fold["fold_index"],
            "test_start_ts": fold["test_start_ts"],
            "test_end_ts": fold["test_end_ts"],
            "purge_embargo_valid": (
                fold["train_target_max_ts"] < fold["purge_boundary_ts"]
            ),
            "evaluation": evaluation,
        })
    if not occurrences:
        return _insufficient(
            hypothesis, dataset_sha256, "NO_EVALUABLE_PURGED_WALK_FORWARD_FOLDS",
            raw_rows=len(horizon_rows), target_rows=len(target_rows), fold_count=len(folds),
        )

    aggregate = _aggregate_candidate(
        template.template_id, occurrences, spec, horizon=horizon)
    return {
        "hypothesis_id": str(hypothesis["hypothesis_id"]),
        "target_id": str(hypothesis["target_id"]),
        "target_family": str(hypothesis["target_family"]),
        "horizon_minutes": horizon,
        "dataset_sha256": dataset_sha256,
        "evaluation_state": "DETERMINISTIC_EVALUATED",
        "status": "PENDING_MULTIPLE_TESTING_GATE",
        "reason": None,
        "raw_rows": len(horizon_rows),
        "target_rows": len(target_rows),
        "fold_count": len(folds),
        "evaluated_fold_count": int(aggregate["fold_evaluated"]),
        "fold_positive": int(aggregate["fold_positive"]),
        "p_value": float(aggregate["p_value"]),
        "q_value": None,
        "primary_improvement": float(aggregate["primary_improvement"]),
        "model": aggregate["model"],
        "baseline": aggregate["baseline"],
        "improvement": aggregate["improvement"],
        "folds": aggregate["folds"],
        "fold_rules": aggregate["fold_rules"],
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
        "prospective_confirmation": False,
    }


def _apply_multiple_testing(results: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if result.get("evaluation_state") != "DETERMINISTIC_EVALUATED":
            continue
        grouped[(str(result["target_id"]), int(result["horizon_minutes"]))].append(result)
    for group in grouped.values():
        q_values = benjamini_hochberg([float(item["p_value"]) for item in group])
        for item, q_value in zip(group, q_values):
            item["q_value"] = float(q_value)
            qualified = (
                float(q_value) <= MAX_Q_VALUE
                and float(item["primary_improvement"]) >= MIN_RELATIVE_IMPROVEMENT
                and int(item["fold_positive"]) >= MIN_STABLE_FOLDS
            )
            item["status"] = "DISCOVERY_SIGNAL" if qualified else "RESEARCH_DIAGNOSTIC"
            item["fdr_scope"] = "LLM_RUN_TARGET_HORIZON"
            item["gates"] = {
                "q_value_max": MAX_Q_VALUE,
                "minimum_relative_improvement": MIN_RELATIVE_IMPROVEMENT,
                "minimum_positive_outer_folds": MIN_STABLE_FOLDS,
            }


def _evaluation_id(result: dict[str, Any]) -> str:
    return "llm-edge-eval-" + _sha({
        "hypothesis_id": result["hypothesis_id"],
        "dataset_sha256": result["dataset_sha256"],
        "measurement_contract": MEASUREMENT_CONTRACT,
    })[:24]


def _persist(runtime, run_id: str, cutoff_ts: float,
             results: list[dict[str, Any]]) -> int:
    inserted = 0
    created_ts = time.time()
    with runtime._lock, runtime._conn:
        for result in results:
            evaluation_id = _evaluation_id(result)
            result["evaluation_id"] = evaluation_id
            existing = runtime._conn.execute(
                "SELECT 1 FROM llm_edge_evaluations WHERE evaluation_id=? LIMIT 1",
                (evaluation_id,),
            ).fetchone()
            if existing is not None:
                continue
            runtime._conn.execute("""
                INSERT INTO llm_edge_evaluations(
                    evaluation_id,run_id,hypothesis_id,evaluation_cutoff_ts,
                    dataset_sha256,measurement_contract,result_json,created_ts
                ) VALUES(?,?,?,?,?,?,?,?)""", (
                    evaluation_id, str(run_id), str(result["hypothesis_id"]),
                    float(cutoff_ts), str(result["dataset_sha256"]),
                    MEASUREMENT_CONTRACT, _canonical(result), created_ts,
                ))
            inserted += 1
    return inserted


def evaluate_edge_research_run(runtime, run_id: str | None = None) -> dict[str, Any]:
    _ensure_tables(runtime)
    run = _load_run(runtime, run_id)
    if run is None:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "UNAVAILABLE",
            "reason": "RESEARCH_RUN_NOT_FOUND",
            "research_only": True,
            "production_authority": False,
            "eligible_for_policy": False,
        }
    hypothesis_ids = json.loads(str(run.get("hypothesis_ids_json") or "[]"))
    hypothesis_ids = [str(value) for value in hypothesis_ids]
    hypotheses = _load_hypotheses(runtime, hypothesis_ids)
    if len(hypotheses) != len(hypothesis_ids):
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "UNAVAILABLE",
            "reason": "HYPOTHESIS_REGISTRY_INCOMPLETE",
            "run_id": str(run["run_id"]),
            "research_only": True,
            "production_authority": False,
            "eligible_for_policy": False,
        }
    cutoff_ts = float(run["created_ts"])
    try:
        rows = _resolved_rows_at_cutoff(runtime, cutoff_ts)
        specs = _specs(rows)
        results = [
            _evaluate_one(rows, hypothesis, cutoff_ts=cutoff_ts, specs=specs)
            for hypothesis in hypotheses
        ]
        _apply_multiple_testing(results)
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "UNAVAILABLE",
            "reason": f"DETERMINISTIC_EVALUATION_ERROR:{type(exc).__name__}:{str(exc)[:180]}",
            "run_id": str(run["run_id"]),
            "evaluation_cutoff_ts": cutoff_ts,
            "research_only": True,
            "production_authority": False,
            "eligible_for_policy": False,
        }

    inserted = _persist(runtime, str(run["run_id"]), cutoff_ts, results)
    discovery_n = sum(item.get("status") == "DISCOVERY_SIGNAL" for item in results)
    return {
        "contract_version": CONTRACT_VERSION,
        "measurement_contract": MEASUREMENT_CONTRACT,
        "status": "OK" if results else "NO_HYPOTHESES",
        "run_id": str(run["run_id"]),
        "evaluation_cutoff_ts": cutoff_ts,
        "hypothesis_n": len(hypotheses),
        "evaluated_n": sum(
            item.get("evaluation_state") == "DETERMINISTIC_EVALUATED" for item in results
        ),
        "insufficient_data_n": sum(
            item.get("evaluation_state") == "INSUFFICIENT_DATA" for item in results
        ),
        "discovery_signal_n": discovery_n,
        "new_artifact_n": inserted,
        "results": results,
        "cutoff_frozen_at_proposal_time": True,
        "future_resolutions_excluded": True,
        "numeric_thresholds_fit_train_only": True,
        "walk_forward_chronological": True,
        "llm_scores_or_selects_results": False,
        "prospective_confirmation": False,
        "writes_active_edge_registry": False,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
        "may_change_position_manager": False,
        "may_change_cvar_stop_or_size": False,
        "next_step": (
            "FREEZE_SEPARATELY_FOR_FUTURE_PROSPECTIVE_CONFIRMATION"
            if discovery_n else "COLLECT_MORE_CAUSAL_T0_OR_REJECT"
        ),
    }


def edge_evaluator_status(runtime) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        evaluation_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_evaluations"
        ).fetchone()[0])
        latest = runtime._conn.execute(
            "SELECT evaluation_id,run_id,hypothesis_id,evaluation_cutoff_ts,"
            "dataset_sha256,measurement_contract,created_ts "
            "FROM llm_edge_evaluations ORDER BY created_ts DESC LIMIT 1"
        ).fetchone()
    return {
        "contract_version": CONTRACT_VERSION,
        "measurement_contract": MEASUREMENT_CONTRACT,
        "status": "OK",
        "evaluation_n": evaluation_n,
        "latest_evaluation": None if latest is None else dict(latest),
        "cutoff_frozen_at_proposal_time": True,
        "future_resolutions_excluded": True,
        "numeric_thresholds_fit_train_only": True,
        "llm_scores_or_selects_results": False,
        "prospective_confirmation": False,
        "writes_active_edge_registry": False,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
    }


def pending_edge_research_summary(runtime) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        total_runs = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_research_runs"
        ).fetchone()[0])
        total_hypotheses = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_hypotheses"
        ).fetchone()[0])
        evaluated_hypotheses = int(runtime._conn.execute(
            "SELECT COUNT(DISTINCT hypothesis_id) FROM llm_edge_evaluations"
        ).fetchone()[0])
        evaluated_ids = {
            str(row[0]) for row in runtime._conn.execute(
                "SELECT DISTINCT hypothesis_id FROM llm_edge_evaluations"
            ).fetchall()
        }
        runs = runtime._conn.execute(
            "SELECT run_id, hypothesis_ids_json, created_ts FROM llm_edge_research_runs ORDER BY created_ts ASC"
        ).fetchall()
        pending_runs = []
        for row in runs:
            run_id = str(row["run_id"])
            try:
                hyp_ids = json.loads(str(row["hypothesis_ids_json"] or "[]"))
            except Exception:
                hyp_ids = []
            unevaluated = [hid for hid in hyp_ids if str(hid) not in evaluated_ids]
            if unevaluated:
                pending_runs.append({
                    "run_id": run_id,
                    "created_ts": float(row["created_ts"]),
                    "unevaluated_count": len(unevaluated),
                    "total_hypotheses": len(hyp_ids),
                })
        pending_hypotheses = max(0, total_hypotheses - evaluated_hypotheses)
    return {
        "contract_version": CONTRACT_VERSION,
        "total_runs": total_runs,
        "total_hypotheses": total_hypotheses,
        "evaluated_hypotheses": evaluated_hypotheses,
        "pending_hypotheses": pending_hypotheses,
        "pending_runs_count": len(pending_runs),
        "pending_runs": pending_runs,
    }


def evaluate_pending_edge_research_runs(
    runtime, *, max_runs: int = 10
) -> dict[str, Any]:
    summary = pending_edge_research_summary(runtime)
    pending_runs = summary.get("pending_runs", [])[:max_runs]
    results = []
    total_evaluated = 0
    total_discoveries = 0
    for item in pending_runs:
        run_id = item["run_id"]
        res = evaluate_edge_research_run(runtime, run_id)
        results.append(res)
        total_evaluated += int(res.get("evaluated_n") or 0)
        total_discoveries += int(res.get("discovery_signal_n") or 0)

    remaining = max(0, summary.get("pending_runs_count", 0) - len(results))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "OK" if results else "NO_PENDING_RUNS",
        "pending_runs_processed": len(results),
        "hypotheses_evaluated": total_evaluated,
        "discovery_signals_found": total_discoveries,
        "remaining_pending_runs": remaining,
        "results": results,
    }

