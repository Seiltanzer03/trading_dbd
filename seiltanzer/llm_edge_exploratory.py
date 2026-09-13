"""Rolling low-confidence verdicts for already-proposed LLM hypotheses.

The strict immutable evaluator remains the only discovery/promotion authority.
This module deliberately answers a different, useful question: what does the
currently available historical evidence suggest when the strict per-fold
sample gate is not met?  Results are mutable rolling diagnostics, never trading
authority, and are allowed to use explicitly labelled non-authoritative path
summaries which the strict evaluator must reject.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from typing import Any

import numpy as np

from .edge_discovery.filters import fit_rule, rule_mask
from .edge_discovery.prospective import HORIZONS, ProspectiveFeatureAdapter
from .edge_discovery.scoring import benjamini_hochberg
from .edge_discovery.universal_outcomes import causal_local_sigma_h
from .edge_discovery.universal_structured_discovery import _aggregate_candidate
from .edge_discovery.universal_target_scoring import (
    UniversalTargetSpec,
    fitted_constant_predictions,
    paired_target_pvalue,
    relative_target_improvement,
    target_metrics,
    universal_target_specs,
)
from .g1_short_horizon_historical_wf import EMBARGO_SECONDS, _historical_folds, _weights
from .llm_edge_evaluator import _load_hypotheses, _template


CONTRACT_VERSION = "llm-edge-exploratory-verdict-v1"
MIN_SELECTED_TRAIN = 5
MIN_SELECTED_TEST = 3
REFRESH_INTERVAL_SEC = 6 * 60 * 60
_LOCK = threading.Lock()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def ensure_tables(runtime: Any) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_edge_exploratory_evaluations(
                hypothesis_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                evaluated_asof_ts REAL NOT NULL,
                source_resolved_watermark REAL NOT NULL,
                dataset_sha256 TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                result_json TEXT NOT NULL,
                updated_ts REAL NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_edge_exploratory_state(
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                last_started_ts REAL,
                last_finished_ts REAL,
                last_resolved_watermark REAL NOT NULL DEFAULT 0,
                evaluated_hypotheses INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                contract_version TEXT NOT NULL
            )""")
        runtime._conn.execute(
            "INSERT OR IGNORE INTO llm_edge_exploratory_state("
            "singleton_id,contract_version) VALUES(1,?)", (CONTRACT_VERSION,))


def _path_metrics(runtime: Any) -> dict[str, dict[str, Any]]:
    with runtime._lock:
        tables = {str(row[0]) for row in runtime._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "g1s_path_metrics" not in tables:
            return {}
        rows = runtime._conn.execute(
            "SELECT observation_id,realized_volatility_1m,path_points_n,path_source "
            "FROM g1s_path_metrics").fetchall()
    return {str(row["observation_id"]): dict(row) for row in rows}


def _sigma_h(row: dict[str, Any]) -> float | None:
    features = row.get("ede_features") or {}
    rv60 = _finite(features.get("vol.rv_60m"))
    if rv60 is None:
        rv15 = _finite(features.get("vol.rv_15m"))
        if rv15 is not None and rv15 > 0:
            rv60 = rv15 * 2.0
    return causal_local_sigma_h(rv60, int(row["horizon_minutes"]))


def _first_touch_value(row: dict[str, Any], target_id: str,
                       sigma_h: float) -> str | None:
    token = target_id.split(":", 1)[1]
    try:
        up_text, down_text = token.removeprefix("up_").split("s_down_", 1)
        up = float(up_text.replace("p", "."))
        down = float(down_text.removesuffix("s").replace("p", "."))
    except (ValueError, IndexError):
        return None
    mfe = _finite(row.get("mfe_log_return"))
    mae = _finite(row.get("mae_log_return"))
    if mfe is None or mae is None:
        return None
    upper_hit = mfe / sigma_h >= up
    lower_hit = mae / sigma_h <= -down
    # Excursions prove a one-sided outcome only when the opposite barrier was
    # never reached.  If both were reached their order is unknown and we abstain.
    if upper_hit and not lower_hit:
        return "UP_FIRST"
    if lower_hit and not upper_hit:
        return "DOWN_FIRST"
    quality = str(row.get("path_quality_status") or "").lower()
    if not upper_hit and not lower_hit and quality.startswith("complete"):
        return "NO_TOUCH"
    return None


def _target_value(row: dict[str, Any], spec: UniversalTargetSpec,
                  metrics: dict[str, Any] | None) -> tuple[float | str | None, str]:
    sigma_h = _sigma_h(row)
    if spec.target_id == "DIRECTION":
        value = str(row.get("direction_label") or "")
        return (value if value in {"UP", "DOWN"} else None), "FROZEN_TERMINAL"
    if sigma_h is None or sigma_h <= 0:
        return None, "T0_SCALE_UNAVAILABLE"
    if spec.target_id == "RETURN_SIGMA":
        value = _finite(row.get("terminal_log_return"))
        return (None if value is None else value / sigma_h), "FROZEN_TERMINAL"
    if spec.target_id == "MFE_SIGMA":
        value = _finite(row.get("mfe_log_return"))
        return (None if value is None else value / sigma_h), "FROZEN_PATH_SUMMARY"
    if spec.target_id == "MAE_SIGMA":
        value = _finite(row.get("mae_log_return"))
        return (None if value is None else value / sigma_h), "FROZEN_PATH_SUMMARY"
    if spec.target_id == "FORWARD_VOL_RATIO" and metrics:
        one_minute = _finite(metrics.get("realized_volatility_1m"))
        points = int(metrics.get("path_points_n") or 0)
        if one_minute is not None and points >= 3:
            return one_minute * math.sqrt(max(1, points - 1)) / sigma_h, "FROZEN_PATH_METRIC"
        return None, "PATH_METRIC_UNAVAILABLE"
    if spec.target_id.startswith("FIRST_TOUCH:"):
        return _first_touch_value(row, spec.target_id, sigma_h), "FROZEN_EXCURSION_ORDER_PROXY"
    return None, "TARGET_UNSUPPORTED"


def exploratory_target_rows(rows: list[dict[str, Any]], spec: UniversalTargetSpec,
                            path_metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for source in rows:
        value, quality = _target_value(
            source, spec, path_metrics.get(str(source.get("observation_id") or "")))
        if value is None:
            continue
        row = dict(source)
        row["universal_target_id"] = spec.target_id
        row["universal_target_value"] = value
        row["exploratory_evidence_quality"] = quality
        output.append(row)
    return output


def _relaxed_rule(template, train: list[dict[str, Any]], test: list[dict[str, Any]],
                  spec: UniversalTargetSpec) -> dict[str, Any] | None:
    rule = fit_rule(template, train)
    if rule is None:
        return None
    selected_train = [row for row, keep in zip(train, rule_mask(train, rule)) if keep]
    selected_test = [row for row, keep in zip(test, rule_mask(test, rule)) if keep]
    if len(selected_train) < MIN_SELECTED_TRAIN or len(selected_test) < MIN_SELECTED_TEST:
        return None
    if spec.kind == "CONTINUOUS":
        values = np.asarray([float(row["universal_target_value"]) for row in selected_train])
        if len(values) < 3 or float(np.std(values)) <= 1e-12:
            return None
    try:
        model_prediction, baseline_prediction = fitted_constant_predictions(
            train, selected_train, selected_test, spec)
        model = target_metrics(selected_test, model_prediction, spec)
        baseline = target_metrics(selected_test, baseline_prediction, spec)
        improvement = relative_target_improvement(model, baseline, spec)
        p_value = paired_target_pvalue(
            selected_test, model_prediction, baseline_prediction, spec)
    except (ValueError, TypeError, KeyError, FloatingPointError):
        return None
    return {
        "template_id": template.template_id,
        "complexity": template.complexity,
        "rule": rule.as_dict(),
        "target_id": spec.target_id,
        "target_family": spec.family,
        "target_kind": spec.kind,
        "model": model,
        "baseline": baseline,
        "improvement": improvement,
        "primary_improvement": min(float(improvement[name]) for name in spec.primary_metrics),
        "p_value": float(p_value),
        "selected_train_raw_n": len(selected_train),
        "selected_train_effective_n": float(_weights(selected_train)[1]),
        "rows": selected_test,
        "model_prediction": model_prediction,
        "baseline_prediction": baseline_prediction,
    }


def _fallback_fold(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    times = sorted({float(row["captured_ts"]) for row in rows})
    if len(times) < 20:
        return []
    split = max(10, int(len(times) * 0.60))
    if split >= len(times):
        return []
    test_start = times[split]
    purge = test_start - max(float(EMBARGO_SECONDS), float(horizon * 60))
    train = [row for row in rows if float(row["target_ts"]) < purge - 1e-9]
    test = [row for row in rows if float(row["captured_ts"]) >= test_start]
    if len(train) < 20 or len(test) < 3:
        return []
    return [{
        "fold_index": 1,
        "train": train,
        "test": test,
        "test_start_ts": test_start,
        "test_end_ts": times[-1],
        "purge_boundary_ts": purge,
        "train_target_max_ts": max(float(row["target_ts"]) for row in train),
    }]


def _prediction_shift(occurrences: list[dict[str, Any]], spec: UniversalTargetSpec) -> Any:
    model = np.concatenate([
        np.asarray(item["evaluation"]["model_prediction"]) for item in occurrences], axis=0)
    baseline = np.concatenate([
        np.asarray(item["evaluation"]["baseline_prediction"]) for item in occurrences], axis=0)
    rows = [row for item in occurrences for row in item["evaluation"]["rows"]]
    weights, _ = _weights(rows)
    if spec.kind == "MULTICLASS":
        delta = np.average(model - baseline, axis=0, weights=weights)
        return {label: float(delta[index]) for index, label in enumerate(spec.classes)}
    return float(np.average(model - baseline, weights=weights))


def _evaluate_one(rows: list[dict[str, Any]], hypothesis: dict[str, Any],
                  specs: dict[str, UniversalTargetSpec], path_metrics: dict[str, dict[str, Any]],
                  asof_ts: float) -> dict[str, Any]:
    horizon = int(hypothesis["horizon_minutes"])
    horizon_rows = [row for row in rows if int(row["horizon_minutes"]) == horizon]
    spec = specs.get(str(hypothesis["target_id"]))
    if spec is None:
        return {
            "contract_version": CONTRACT_VERSION,
            "hypothesis_id": str(hypothesis["hypothesis_id"]),
            "target_id": str(hypothesis["target_id"]),
            "target_family": str(hypothesis["target_family"]),
            "horizon_minutes": horizon,
            "evaluated_asof_ts": float(asof_ts),
            "status": "EARLY_UNDECIDED",
            "reason": "TARGET_SPEC_UNAVAILABLE",
            "confidence": "NONE",
            "strict_gate_passed": False,
            "rolling_result": True,
            "research_only": True,
            "production_authority": False,
            "automatic_execution": False,
            "position_manager_weight": 0.0,
        }
    target_rows = exploratory_target_rows(horizon_rows, spec, path_metrics)
    template = _template(hypothesis)
    folds = _historical_folds(target_rows, horizon) or _fallback_fold(target_rows, horizon)
    occurrences = []
    for fold in folds:
        evaluation = _relaxed_rule(template, fold["train"], fold["test"], spec)
        if evaluation is None:
            continue
        occurrences.append({
            "fold_index": fold["fold_index"],
            "test_start_ts": fold["test_start_ts"],
            "test_end_ts": fold["test_end_ts"],
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "evaluation": evaluation,
        })
    base = {
        "contract_version": CONTRACT_VERSION,
        "hypothesis_id": str(hypothesis["hypothesis_id"]),
        "target_id": str(hypothesis["target_id"]),
        "target_family": str(hypothesis["target_family"]),
        "horizon_minutes": horizon,
        "evaluated_asof_ts": float(asof_ts),
        "raw_rows": len(horizon_rows),
        "target_rows": len(target_rows),
        "condition_count": len(hypothesis.get("conditions") or []),
        "strict_gate_passed": False,
        "rolling_result": True,
        "research_only": True,
        "production_authority": False,
        "automatic_execution": False,
        "position_manager_weight": 0.0,
    }
    if not occurrences:
        return {**base, "status": "EARLY_UNDECIDED",
                "reason": "NO_MATCHED_RELAXED_HOLDOUT_ROWS", "confidence": "NONE",
                "evaluated_fold_count": 0, "selected_test_n": 0}
    aggregate = _aggregate_candidate(template.template_id, occurrences, spec, horizon=horizon)
    fold_n = int(aggregate["fold_evaluated"])
    positive = int(aggregate["fold_positive"])
    effect = float(aggregate["primary_improvement"])
    if effect > 0 and positive >= max(1, math.ceil(fold_n / 2)):
        status = "EARLY_ADVANTAGE"
    elif effect <= 0 and positive == 0:
        status = "EARLY_DISADVANTAGE"
    else:
        status = "EARLY_MIXED"
    selected_n = int(aggregate["raw_n"])
    confidence = "LIMITED" if selected_n >= 24 and fold_n >= 2 else "VERY_LOW"
    qualities = sorted({
        str(row.get("exploratory_evidence_quality") or "UNKNOWN")
        for item in occurrences for row in item["evaluation"]["rows"]
    })
    result = {
        **base,
        "status": status,
        "reason": None,
        "confidence": confidence,
        "evaluated_fold_count": fold_n,
        "positive_fold_count": positive,
        "selected_test_n": selected_n,
        "selected_effective_n": float(aggregate["effective_n"]),
        "primary_improvement": effect,
        "p_value": float(aggregate["p_value"]),
        "q_value": None,
        "prediction_shift": _prediction_shift(occurrences, spec),
        "target_kind": spec.kind,
        "target_classes": list(spec.classes),
        "evidence_quality": qualities,
        "deployment_rule": occurrences[-1]["evaluation"]["rule"],
        "folds": aggregate["folds"],
    }
    result["dataset_sha256"] = _sha({
        "hypothesis_id": result["hypothesis_id"], "asof": asof_ts,
        "target_rows": [str(row["observation_id"]) for row in target_rows],
        "contract": CONTRACT_VERSION,
    })
    return result


def _apply_q_values(results: list[dict[str, Any]]) -> None:
    evaluated = [item for item in results if item.get("p_value") is not None]
    if not evaluated:
        return
    for item, q_value in zip(
        evaluated, benjamini_hochberg([float(item["p_value"]) for item in evaluated])
    ):
        item["q_value"] = float(q_value)


def evaluate_all(runtime: Any, *, now: float | None = None,
                 max_hypotheses: int = 200) -> dict[str, Any]:
    current = float(time.time() if now is None else now)
    ensure_tables(runtime)
    with _LOCK:
        with runtime._lock:
            raw = runtime._conn.execute(
                "SELECT hypothesis_id,first_run_id FROM llm_edge_hypotheses "
                "ORDER BY created_ts DESC,hypothesis_id LIMIT ?", (max(1, min(int(max_hypotheses), 500)),)
            ).fetchall()
            watermark = runtime._conn.execute(
                "SELECT COALESCE(MAX(resolved_ts),0) FROM g1s_resolutions").fetchone()[0]
        ids = [str(row["hypothesis_id"]) for row in raw]
        run_by_id = {
            str(row["hypothesis_id"]): str(row["first_run_id"]) for row in raw
        }
        hypotheses = _load_hypotheses(runtime, ids)
        horizons = {int(item["horizon_minutes"]) for item in hypotheses}
        adapter = ProspectiveFeatureAdapter(runtime, available_asof=current)
        rows = []
        for horizon in sorted(horizons & set(HORIZONS)):
            rows.extend(adapter.rows(resolved_only=True, strict=False, horizon_minutes=horizon))
        metrics = _path_metrics(runtime)
        specs = {item.target_id: item for item in universal_target_specs((
            "up_0p5s_down_0p5s", "up_1s_down_0p5s",
            "up_0p5s_down_1s", "up_1s_down_1s",
        ))}
        results = [
            _evaluate_one(rows, hypothesis, specs, metrics, current)
            for hypothesis in hypotheses
        ]
        _apply_q_values(results)
        updated = time.time()
        with runtime._lock, runtime._conn:
            runtime._conn.execute(
                "UPDATE llm_edge_exploratory_state SET last_started_ts=?,last_error=NULL "
                "WHERE singleton_id=1", (current,))
            for result in results:
                result.setdefault("dataset_sha256", _sha({
                    "hypothesis_id": result.get("hypothesis_id"), "asof": current,
                    "contract": CONTRACT_VERSION,
                }))
                hypothesis_id = str(result.get("hypothesis_id") or "")
                runtime._conn.execute("""
                    INSERT INTO llm_edge_exploratory_evaluations(
                        hypothesis_id,run_id,evaluated_asof_ts,source_resolved_watermark,
                        dataset_sha256,contract_version,result_json,updated_ts
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(hypothesis_id) DO UPDATE SET
                        run_id=excluded.run_id,evaluated_asof_ts=excluded.evaluated_asof_ts,
                        source_resolved_watermark=excluded.source_resolved_watermark,
                        dataset_sha256=excluded.dataset_sha256,contract_version=excluded.contract_version,
                        result_json=excluded.result_json,updated_ts=excluded.updated_ts
                """, (
                    hypothesis_id, run_by_id.get(hypothesis_id, ""), current, float(watermark or 0),
                    result["dataset_sha256"], CONTRACT_VERSION, _canonical(result), updated,
                ))
            runtime._conn.execute("""
                UPDATE llm_edge_exploratory_state SET last_finished_ts=?,
                    last_resolved_watermark=?,evaluated_hypotheses=?,last_error=NULL,
                    contract_version=? WHERE singleton_id=1
            """, (updated, float(watermark or 0), len(results), CONTRACT_VERSION))
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "OK",
            "evaluated_n": len(results),
            "advantage_n": sum(item.get("status") == "EARLY_ADVANTAGE" for item in results),
            "disadvantage_n": sum(item.get("status") == "EARLY_DISADVANTAGE" for item in results),
            "mixed_n": sum(item.get("status") == "EARLY_MIXED" for item in results),
            "undecided_n": sum(item.get("status") == "EARLY_UNDECIDED" for item in results),
            "source_resolved_watermark": float(watermark or 0),
            "production_authority": False,
            "position_manager_weight": 0.0,
        }


def refresh_if_due(runtime: Any, *, now: float | None = None) -> dict[str, Any]:
    current = float(time.time() if now is None else now)
    ensure_tables(runtime)
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT * FROM llm_edge_exploratory_state WHERE singleton_id=1").fetchone()
        hypothesis_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_hypotheses").fetchone()[0])
        evaluation_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_exploratory_evaluations").fetchone()[0])
        watermark = float(runtime._conn.execute(
            "SELECT COALESCE(MAX(resolved_ts),0) FROM g1s_resolutions").fetchone()[0] or 0)
    last_finished = float(state["last_finished_ts"] or 0) if state else 0.0
    last_watermark = float(state["last_resolved_watermark"] or 0) if state else 0.0
    due = evaluation_n < hypothesis_n or (
        watermark > last_watermark + 1e-6 and current - last_finished >= REFRESH_INTERVAL_SEC
    )
    if not due:
        return {"status": "NOT_DUE", "evaluated_n": evaluation_n,
                "hypothesis_n": hypothesis_n, "contract_version": CONTRACT_VERSION}
    return evaluate_all(runtime, now=current)


def status(runtime: Any) -> dict[str, Any]:
    ensure_tables(runtime)
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT * FROM llm_edge_exploratory_state WHERE singleton_id=1").fetchone()
        rows = runtime._conn.execute(
            "SELECT result_json FROM llm_edge_exploratory_evaluations").fetchall()
    results = []
    for row in rows:
        try:
            results.append(json.loads(str(row[0])))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "OK",
        "evaluated_n": len(results),
        "advantage_n": sum(item.get("status") == "EARLY_ADVANTAGE" for item in results),
        "disadvantage_n": sum(item.get("status") == "EARLY_DISADVANTAGE" for item in results),
        "mixed_n": sum(item.get("status") == "EARLY_MIXED" for item in results),
        "undecided_n": sum(item.get("status") == "EARLY_UNDECIDED" for item in results),
        "last_started_ts": None if state is None else state["last_started_ts"],
        "last_finished_ts": None if state is None else state["last_finished_ts"],
        "last_error": None if state is None else state["last_error"],
        "rolling_result": True,
        "strict_gate_unchanged": True,
        "production_authority": False,
        "position_manager_weight": 0.0,
    }
