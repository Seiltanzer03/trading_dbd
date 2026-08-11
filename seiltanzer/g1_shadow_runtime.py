"""Phase G.1C shadow Q->P calibration engine.

This module is deliberately research-only. It consumes only immutable G.1A
q_to_p eligible cuts, fits simple deterministic challenger calibrators, freezes
model artifacts, and records prospective shadow predictions for later G.1D OOS
validation. It never changes production trade-management authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from . import g1_baseline_runtime as _g1b
from . import g1_dataset_runtime as _g1
from . import passive_learning as _pl
from .option_q_adapter import OPTION_Q_CONTRACT_VERSION

G1C_STAGE = "G.1C"
G1C_CONTRACT_VERSION = "g1-shadow-calibration-v1"
G1C_FIT_THRESHOLD_VERSION = "g1c-shadow-fit-threshold-v1"
G1C_WEIGHT_CONTRACT_VERSION = "g1c-dependency-weight-v1"
G1C_REFIT_POLICY_VERSION = "g1c-shadow-refit-policy-v1"
G1C_PREDICTION_CONTRACT_VERSION = "g1c-shadow-prediction-v1"
G1C_TARGET_CONTRACT_VERSION = "terminal-direction-event-v1"
G1C_PLATT_VERSION = "g1c-platt-logit-v1"
G1C_BETA_VERSION = "g1c-beta-calibration-v1"
G1C_ISOTONIC_VERSION = "g1c-isotonic-pava-v1"
G1C_PIT_CDF_VERSION = "g1c-pit-isotonic-cdf-v1"
G1C_OPTIMIZER_VERSION = "projected-newton-linesearch-v1"
G1C_REFIT_INTERVAL_SEC = 6 * 60 * 60
PROB_EPS = 1e-6

FIT_THRESHOLDS = {
    "PLATT": {"raw_n": 60, "effective_n": 30, "positive_n": 15, "negative_n": 15, "unique_q_n": 2},
    "BETA": {"raw_n": 60, "effective_n": 30, "positive_n": 15, "negative_n": 15, "unique_q_n": 3},
    "ISOTONIC": {"raw_n": 120, "effective_n": 60, "positive_n": 30, "negative_n": 30, "unique_q_n": 10},
    "PIT_ISOTONIC_CDF": {"raw_n": 120, "effective_n": 60, "positive_n": 30, "negative_n": 30, "unique_q_n": 10},
}
G1D_THRESHOLDS = {
    "raw_n": 200,
    "effective_n": 100,
    "positive_n": 30,
    "negative_n": 30,
    "temporal_period_n": 3,
    "expiry_cluster_n": 2,
}

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_INIT = _ENGINE.__init__
_ORIGINAL_COLLECT = _ENGINE._collect_instrument


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clip_probability(value: Any) -> float | None:
    out = _finite(value)
    if out is None:
        return None
    return min(1.0 - PROB_EPS, max(PROB_EPS, out))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 700.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -700.0))
    return z / (1.0 + z)


def _logit(probability: float) -> float:
    p = _clip_probability(probability)
    if p is None:
        raise ValueError("RAW_Q_INVALID")
    return math.log(p / (1.0 - p))


def _weighted_binary_metrics(probabilities: list[float], outcomes: list[int], weights: list[float]) -> dict:
    if not probabilities or len(probabilities) != len(outcomes) or len(probabilities) != len(weights):
        return {"n": 0, "weight_sum": 0.0, "brier": None, "log_loss": None}
    weight_sum = sum(float(w) for w in weights)
    if weight_sum <= 0:
        return {"n": len(probabilities), "weight_sum": 0.0, "brier": None, "log_loss": None}
    brier = 0.0
    log_loss = 0.0
    for p_raw, y_raw, weight in zip(probabilities, outcomes, weights):
        p = min(1.0 - 1e-12, max(1e-12, float(p_raw)))
        y = 1 if int(y_raw) else 0
        w = float(weight)
        brier += w * (p - y) ** 2
        log_loss -= w * (y * math.log(p) + (1 - y) * math.log(1.0 - p))
    return {
        "n": len(probabilities),
        "weight_sum": round(weight_sum, 10),
        "brier": round(brier / weight_sum, 10),
        "log_loss": round(log_loss / weight_sum, 10),
    }


def _ensure_tables(self: _ENGINE) -> None:
    with self._lock, self._conn:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1c_fit_runs (
                fit_run_id TEXT PRIMARY KEY,
                created_ts REAL NOT NULL,
                training_cut_id TEXT NOT NULL,
                training_cut_sha256 TEXT NOT NULL,
                training_cutoff REAL NOT NULL,
                model_family TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                target_contract_version TEXT NOT NULL,
                q_contract_version TEXT NOT NULL,
                dataset_contract_version TEXT NOT NULL,
                fit_threshold_version TEXT NOT NULL,
                fit_weight_contract_version TEXT NOT NULL,
                optimizer_version TEXT NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                positive_n INTEGER NOT NULL,
                negative_n INTEGER NOT NULL,
                unique_q_n INTEGER NOT NULL,
                input_manifest_sha256 TEXT NOT NULL,
                hyperparameters_json TEXT NOT NULL,
                status TEXT NOT NULL,
                rejection_reason TEXT,
                diagnostics_json TEXT NOT NULL,
                artifact_sha256 TEXT
            )""")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1c_shadow_models (
                model_id TEXT PRIMARY KEY,
                fit_run_id TEXT NOT NULL UNIQUE,
                model_family TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                created_ts REAL NOT NULL,
                training_cut_id TEXT NOT NULL,
                training_cut_sha256 TEXT NOT NULL,
                training_cutoff REAL NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                positive_n INTEGER NOT NULL,
                negative_n INTEGER NOT NULL,
                unique_q_n INTEGER NOT NULL,
                parameters_json TEXT NOT NULL,
                training_diagnostics_json TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                oos_validated INTEGER NOT NULL,
                production_selected INTEGER NOT NULL,
                production_authority INTEGER NOT NULL,
                FOREIGN KEY(fit_run_id) REFERENCES g1c_fit_runs(fit_run_id)
            )""")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1c_shadow_predictions (
                prediction_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                model_id TEXT NOT NULL,
                model_artifact_sha256 TEXT NOT NULL,
                training_cut_id TEXT NOT NULL,
                training_cutoff REAL NOT NULL,
                model_family TEXT NOT NULL,
                model_scope_key TEXT NOT NULL,
                raw_q REAL NOT NULL,
                shadow_calibrated_probability REAL NOT NULL,
                target_contract_version TEXT NOT NULL,
                prediction_contract_version TEXT NOT NULL,
                prediction_status TEXT NOT NULL,
                authority TEXT NOT NULL,
                production_used INTEGER NOT NULL,
                created_ts REAL NOT NULL,
                UNIQUE(observation_id, model_id),
                FOREIGN KEY(model_id) REFERENCES g1c_shadow_models(model_id)
            )""")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1c_contract_errors (
                error_id TEXT PRIMARY KEY,
                error_type TEXT NOT NULL,
                fit_run_id TEXT,
                model_id TEXT,
                observation_id TEXT,
                detail TEXT,
                created_ts REAL NOT NULL
            )""")
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_g1c_models_scope ON g1c_shadow_models(scope_key,model_family,created_ts)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_g1c_predictions_obs ON g1c_shadow_predictions(observation_id,captured_ts)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_g1c_fit_cut ON g1c_fit_runs(training_cut_id,scope_key,model_family)")
        for table, label in (
            ("g1c_fit_runs", "G1C fit run"),
            ("g1c_shadow_models", "G1C shadow model"),
            ("g1c_shadow_predictions", "G1C shadow prediction"),
        ):
            self._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable {label}'); END""")
            self._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable {label}'); END""")


def _record_error(
    self: _ENGINE,
    error_type: str,
    *,
    fit_run_id: str | None = None,
    model_id: str | None = None,
    observation_id: str | None = None,
    detail: str | None = None,
) -> None:
    _ensure_tables(self)
    payload = {
        "error_type": error_type,
        "fit_run_id": fit_run_id,
        "model_id": model_id,
        "observation_id": observation_id,
        "detail": detail,
    }
    with self._lock, self._conn:
        self._conn.execute(
            "INSERT OR IGNORE INTO g1c_contract_errors(error_id,error_type,fit_run_id,model_id,observation_id,detail,created_ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (_sha(payload), error_type, fit_run_id, model_id, observation_id, detail, time.time()),
        )


def _q_rows(self: _ENGINE, *, cut_id: str | None = None) -> list[dict]:
    rows, _ = _g1b._load_rows(self, cut_id=cut_id)
    clean = []
    for row in rows:
        if int(row.get("q_to_p_eligible") or 0) != 1:
            continue
        q = _g1b._q_up_probability(row)
        y = _g1b._future_direction(row)
        if q is None or y is None or not math.isfinite(float(q)):
            continue
        item = dict(row)
        item["raw_q"] = float(q)
        item["outcome_y"] = int(y)
        clean.append(item)
    return clean


def _stats(self: _ENGINE, rows: list[dict]) -> dict:
    raw_n = len(rows)
    positive_n = sum(int(row.get("outcome_y") or 0) for row in rows)
    negative_n = raw_n - positive_n
    effective_n = int(self._g1_effective_n(rows, aggregate=True)) if rows else 0
    unique_q_n = len({round(float(row["raw_q"]), 8) for row in rows})
    anchors = len({str(row.get("dependency_group_id")) for row in rows})
    temporal_periods = {
        datetime.fromtimestamp(float(row["captured_ts"]), timezone.utc).date().isoformat()
        for row in rows if _finite(row.get("captured_ts")) is not None
    }
    expiries = set()
    for row in rows:
        expiry = _finite((row.get("forecast") or {}).get("source_expiry_ts_utc"))
        if expiry is not None:
            expiries.add(datetime.fromtimestamp(expiry, timezone.utc).date().isoformat())
    return {
        "raw_n": raw_n,
        "effective_n": effective_n,
        "positive_n": positive_n,
        "negative_n": negative_n,
        "unique_q_n": unique_q_n,
        "unique_anchor_n": anchors,
        "temporal_period_n": len(temporal_periods),
        "expiry_cluster_n": len(expiries),
    }


def _threshold_status(stats: dict, family: str) -> dict:
    threshold = FIT_THRESHOLDS[family]
    blockers = []
    mapping = (
        ("raw_n", "INSUFFICIENT_RAW_N"),
        ("effective_n", "INSUFFICIENT_EFFECTIVE_N"),
        ("positive_n", "INSUFFICIENT_POSITIVE_EVENTS"),
        ("negative_n", "INSUFFICIENT_NEGATIVE_EVENTS"),
        ("unique_q_n", "INSUFFICIENT_Q_VARIATION"),
    )
    for key, code in mapping:
        if int(stats.get(key, 0)) < int(threshold[key]):
            blockers.append(code)
    return {
        "family": family,
        "threshold_contract_version": G1C_FIT_THRESHOLD_VERSION,
        "required": dict(threshold),
        "observed": {key: int(stats.get(key, 0)) for key in threshold},
        "ready": not blockers,
        "status": "READY_TO_FIT" if not blockers else "INSUFFICIENT_EVIDENCE",
        "blockers": blockers,
    }


def _g1d_status(stats: dict, critical_contract_errors: int = 0) -> dict:
    blockers = []
    for key, required in G1D_THRESHOLDS.items():
        if int(stats.get(key, 0)) < int(required):
            blockers.append(f"INSUFFICIENT_{key.upper()}")
    if critical_contract_errors:
        blockers.append("CRITICAL_CONTRACT_ERRORS")
    return {
        "ready": not blockers,
        "required": dict(G1D_THRESHOLDS),
        "observed": {key: int(stats.get(key, 0)) for key in G1D_THRESHOLDS},
        "blockers": blockers,
        "production_promotion": False,
    }


def _dependency_weights(rows: list[dict]) -> list[float]:
    counts = Counter(str(row.get("dependency_group_id") or row.get("observation_id")) for row in rows)
    return [1.0 / counts[str(row.get("dependency_group_id") or row.get("observation_id"))] for row in rows]


def _objective(x: np.ndarray, y: np.ndarray, w: np.ndarray, beta: np.ndarray, ridge: float = 1e-8) -> float:
    z = x @ beta
    # Stable logistic loss: max(z,0)-y*z+log1p(exp(-abs(z))).
    loss = np.maximum(z, 0.0) - y * z + np.log1p(np.exp(-np.abs(z)))
    penalty = ridge * float(np.dot(beta[:-1], beta[:-1]))
    return float(np.dot(w, loss) / max(float(np.sum(w)), 1e-12) + penalty)


def _fit_projected_logistic(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    initial: list[float],
    constrained_nonnegative: tuple[int, ...],
) -> list[float]:
    beta = np.asarray(initial, dtype=float)
    ridge = 1e-8
    previous = _objective(x, y, w, beta, ridge)
    for _ in range(120):
        z = x @ beta
        p = np.asarray([_sigmoid(float(value)) for value in z], dtype=float)
        grad = x.T @ (w * (p - y)) / max(float(np.sum(w)), 1e-12)
        curvature = w * np.maximum(p * (1.0 - p), 1e-8)
        hessian = (x.T @ (x * curvature[:, None])) / max(float(np.sum(w)), 1e-12)
        for idx in range(len(beta) - 1):
            grad[idx] += 2.0 * ridge * beta[idx]
            hessian[idx, idx] += 2.0 * ridge
        hessian += np.eye(len(beta)) * 1e-10
        try:
            step = np.linalg.solve(hessian, grad)
        except np.linalg.LinAlgError as exc:
            raise ValueError("OPTIMIZER_FAILED") from exc
        accepted = False
        alpha = 1.0
        candidate = beta.copy()
        for _ in range(24):
            candidate = beta - alpha * step
            for index in constrained_nonnegative:
                candidate[index] = max(0.0, candidate[index])
            objective = _objective(x, y, w, candidate, ridge)
            if math.isfinite(objective) and objective <= previous + 1e-12:
                accepted = True
                break
            alpha *= 0.5
        if not accepted:
            break
        delta = float(np.max(np.abs(candidate - beta)))
        beta = candidate
        previous = objective
        if delta < 1e-10:
            break
    if not np.all(np.isfinite(beta)):
        raise ValueError("NONFINITE_PARAMETERS")
    for index in constrained_nonnegative:
        if beta[index] < -1e-12:
            raise ValueError("NON_MONOTONE_MAPPING")
    return [float(value) for value in beta]


def _fit_platt(rows: list[dict], weights: list[float]) -> dict:
    qs = np.asarray([_clip_probability(row["raw_q"]) for row in rows], dtype=float)
    ys = np.asarray([int(row["outcome_y"]) for row in rows], dtype=float)
    ws = np.asarray(weights, dtype=float)
    x = np.column_stack((np.log(qs / (1.0 - qs)), np.ones(len(qs))))
    a, b = _fit_projected_logistic(x, ys, ws, initial=[1.0, 0.0], constrained_nonnegative=(0,))
    return {"a": a, "b": b}


def _fit_beta(rows: list[dict], weights: list[float]) -> dict:
    qs = np.asarray([_clip_probability(row["raw_q"]) for row in rows], dtype=float)
    ys = np.asarray([int(row["outcome_y"]) for row in rows], dtype=float)
    ws = np.asarray(weights, dtype=float)
    x = np.column_stack((np.log(qs), -np.log(1.0 - qs), np.ones(len(qs))))
    a, b, c = _fit_projected_logistic(x, ys, ws, initial=[1.0, 1.0, 0.0], constrained_nonnegative=(0, 1))
    return {"a": a, "b": b, "c": c}


def _fit_isotonic_points(xs: list[float], ys: list[float], weights: list[float]) -> dict:
    grouped: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for x, y, weight in sorted(zip(xs, ys, weights), key=lambda item: (item[0], item[1])):
        key = round(float(x), 12)
        grouped[key][0] += float(weight) * float(y)
        grouped[key][1] += float(weight)
    blocks = []
    for x in sorted(grouped):
        numerator, weight = grouped[x]
        blocks.append({"lo": x, "hi": x, "weight": weight, "mean": numerator / max(weight, 1e-12)})
        while len(blocks) >= 2 and blocks[-2]["mean"] > blocks[-1]["mean"] + 1e-15:
            right = blocks.pop()
            left = blocks.pop()
            total_weight = left["weight"] + right["weight"]
            blocks.append({
                "lo": left["lo"],
                "hi": right["hi"],
                "weight": total_weight,
                "mean": (left["mean"] * left["weight"] + right["mean"] * right["weight"]) / total_weight,
            })
    knots_x: list[float] = []
    knots_y: list[float] = []
    for block in blocks:
        for x in sorted(value for value in grouped if block["lo"] <= value <= block["hi"]):
            knots_x.append(float(x))
            knots_y.append(min(1.0, max(0.0, float(block["mean"]))))
    if not knots_x or any(knots_x[i] <= knots_x[i - 1] for i in range(1, len(knots_x))):
        raise ValueError("MODEL_ARTIFACT_INVALID")
    if any(knots_y[i] < knots_y[i - 1] - 1e-12 for i in range(1, len(knots_y))):
        raise ValueError("NON_MONOTONE_MAPPING")
    return {"knots_x": knots_x, "knots_y": knots_y}


def _fit_isotonic(rows: list[dict], weights: list[float]) -> dict:
    return _fit_isotonic_points(
        [float(row["raw_q"]) for row in rows],
        [float(row["outcome_y"]) for row in rows],
        weights,
    )


def _fit_pit_isotonic(rows: list[dict], weights: list[float]) -> dict:
    pairs = []
    for row, weight in zip(rows, weights):
        terminal = ((row.get("outcome") or {}).get("terminal") or {})
        pit = _finite(terminal.get("terminal_pit_q"))
        if pit is None or pit < 0.0 or pit > 1.0:
            continue
        pairs.append((float(pit), float(weight)))
    if len(pairs) < 2:
        raise ValueError("INSUFFICIENT_Q_VARIATION")
    pairs.sort(key=lambda item: item[0])
    total = sum(weight for _, weight in pairs)
    cumulative = 0.0
    xs, ys, ws = [], [], []
    for pit, weight in pairs:
        cumulative += weight
        xs.append(pit)
        ys.append(cumulative / max(total, 1e-12))
        ws.append(weight)
    return _fit_isotonic_points(xs, ys, ws)


def _predict_parameters(family: str, params: dict, raw_q: float) -> float:
    q = _clip_probability(raw_q)
    if q is None:
        raise ValueError("RAW_Q_INVALID")
    if family == "PLATT":
        value = _sigmoid(float(params["a"]) * _logit(q) + float(params["b"]))
    elif family == "BETA":
        value = _sigmoid(
            float(params["a"]) * math.log(q)
            - float(params["b"]) * math.log(1.0 - q)
            + float(params["c"])
        )
    elif family == "ISOTONIC":
        xs = [float(x) for x in params.get("knots_x", [])]
        ys = [float(y) for y in params.get("knots_y", [])]
        if not xs or len(xs) != len(ys):
            raise ValueError("MODEL_ARTIFACT_INVALID")
        if q <= xs[0]:
            value = ys[0]
        elif q >= xs[-1]:
            value = ys[-1]
        else:
            value = ys[-1]
            for index in range(1, len(xs)):
                if q <= xs[index]:
                    left_x, right_x = xs[index - 1], xs[index]
                    left_y, right_y = ys[index - 1], ys[index]
                    weight = (q - left_x) / max(right_x - left_x, 1e-12)
                    value = left_y + weight * (right_y - left_y)
                    break
    else:
        raise ValueError("MODEL_ARTIFACT_INVALID")
    if not math.isfinite(value) or value < -1e-12 or value > 1.0 + 1e-12:
        raise ValueError("SHADOW_P_INVALID")
    return min(1.0, max(0.0, float(value)))


def _algorithm_version(family: str) -> str:
    return {
        "PLATT": G1C_PLATT_VERSION,
        "BETA": G1C_BETA_VERSION,
        "ISOTONIC": G1C_ISOTONIC_VERSION,
        "PIT_ISOTONIC_CDF": G1C_PIT_CDF_VERSION,
    }[family]


def _scope_definitions(rows: list[dict]) -> list[tuple[str, dict, list[dict]]]:
    scopes: list[tuple[str, dict, list[dict]]] = [
        ("GLOBAL_TERMINAL_Q", {"kind": "global_terminal_q"}, list(rows))
    ]
    by_instrument: dict[str, list[dict]] = defaultdict(list)
    by_cohort: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_instrument[str(row.get("instrument"))].append(row)
        by_cohort[str(row.get("base_cohort_id"))].append(row)
    for instrument in sorted(by_instrument):
        scopes.append((f"INSTRUMENT:{instrument}", {"kind": "instrument", "instrument": instrument}, by_instrument[instrument]))
    for cohort_id in sorted(by_cohort):
        base = by_cohort[cohort_id][0].get("base_cohort") or {}
        scopes.append((
            f"COHORT:{cohort_id}",
            {
                "kind": "g1a_cohort",
                "cohort_id": cohort_id,
                "instrument": base.get("instrument"),
                "horizon_bucket": base.get("horizon_bucket"),
                "q_relation": base.get("q_relation"),
                "proxy_transform": base.get("proxy_transform"),
            },
            by_cohort[cohort_id],
        ))
    return scopes


def _revalidate_cut(self: _ENGINE, cut_id: str) -> tuple[dict, list[dict]]:
    _ensure_tables(self)
    with self._lock:
        cut_row = self._conn.execute(
            "SELECT cut_id,cutoff_ts,manifest_sha256,manifest_json,status FROM g1_dataset_cuts "
            "WHERE cut_id=? AND dataset_contract_version=?",
            (cut_id, _g1.G1_DATASET_CONTRACT_VERSION),
        ).fetchone()
        member_rows = [dict(row) for row in self._conn.execute(
            "SELECT c.observation_id,c.source_record_sha256,p.* FROM g1_dataset_cut_members c "
            "JOIN passive_market_observations p ON p.observation_id=c.observation_id "
            "WHERE c.cut_id=? AND c.q_to_p_eligible=1 ORDER BY c.observation_id",
            (cut_id,),
        ).fetchall()]
    if cut_row is None:
        raise ValueError("TRAINING_CUT_INVALID")
    cut = dict(cut_row)
    if cut.get("status") != "FROZEN":
        raise ValueError("TRAINING_CUT_INVALID")
    manifest = _loads(cut.get("manifest_json"), {})
    if _sha(manifest) != str(cut.get("manifest_sha256")):
        raise ValueError("TRAINING_CUT_INVALID")
    for row in member_rows:
        actual = _g1._source_record_sha256(row)
        if actual != str(row.get("source_record_sha256")):
            raise ValueError("TRAINING_CUT_MUTATED")
    rows = _q_rows(self, cut_id=cut_id)
    if len(rows) != len(member_rows):
        # A q member silently disappearing from the G.1A view means the frozen
        # source/eligibility boundary no longer matches the cut.
        raise ValueError("TRAINING_CUT_MUTATED")
    return cut, rows


def _input_manifest(rows: list[dict]) -> str:
    return _sha([
        {
            "observation_id": str(row.get("observation_id")),
            "source_record_sha256": str(row.get("source_record_sha256") or ""),
            "raw_q": round(float(row["raw_q"]), 12),
            "outcome_y": int(row["outcome_y"]),
            "dependency_group_id": str(row.get("dependency_group_id")),
        }
        for row in sorted(rows, key=lambda item: str(item.get("observation_id")))
    ])


def _latest_model(self: _ENGINE, scope_key: str, family: str) -> dict | None:
    with self._lock:
        row = self._conn.execute(
            "SELECT * FROM g1c_shadow_models WHERE scope_key=? AND model_family=? "
            "ORDER BY created_ts DESC,model_id DESC LIMIT 1",
            (scope_key, family),
        ).fetchone()
    return dict(row) if row is not None else None


def _refit_delta_ready(self: _ENGINE, scope_key: str, family: str, effective_n: int) -> bool:
    previous = _latest_model(self, scope_key, family)
    if previous is None:
        return True
    if previous.get("algorithm_version") != _algorithm_version(family):
        return True
    prior_n = int(previous.get("effective_n") or 0)
    required_delta = max(10, int(math.ceil(prior_n * 0.10)))
    return int(effective_n) >= prior_n + required_delta


def _record_fit_run(
    self: _ENGINE,
    *,
    cut: dict,
    family: str,
    scope_key: str,
    scope_json: dict,
    stats: dict,
    input_manifest: str,
    status: str,
    rejection_reason: str | None,
    diagnostics: dict,
    artifact_sha: str | None,
) -> str:
    fit_run_id = "g1c-fit-" + uuid.uuid4().hex
    with self._lock, self._conn:
        self._conn.execute(
            "INSERT INTO g1c_fit_runs("
            "fit_run_id,created_ts,training_cut_id,training_cut_sha256,training_cutoff,model_family,"
            "algorithm_version,scope_key,scope_json,target_contract_version,q_contract_version,"
            "dataset_contract_version,fit_threshold_version,fit_weight_contract_version,optimizer_version,"
            "raw_n,effective_n,positive_n,negative_n,unique_q_n,input_manifest_sha256,hyperparameters_json,"
            "status,rejection_reason,diagnostics_json,artifact_sha256) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fit_run_id, time.time(), cut["cut_id"], cut["manifest_sha256"], float(cut["cutoff_ts"]),
                family, _algorithm_version(family), scope_key, _json(scope_json), G1C_TARGET_CONTRACT_VERSION,
                OPTION_Q_CONTRACT_VERSION, _g1.G1_DATASET_CONTRACT_VERSION, G1C_FIT_THRESHOLD_VERSION,
                G1C_WEIGHT_CONTRACT_VERSION, G1C_OPTIMIZER_VERSION,
                int(stats["raw_n"]), int(stats["effective_n"]), int(stats["positive_n"]),
                int(stats["negative_n"]), int(stats["unique_q_n"]), input_manifest,
                _json({"probability_clip": PROB_EPS, "ridge": 1e-8}), status, rejection_reason,
                _json(diagnostics), artifact_sha,
            ),
        )
    return fit_run_id


def _fit_one(
    self: _ENGINE,
    *,
    cut: dict,
    family: str,
    scope_key: str,
    scope_json: dict,
    rows: list[dict],
) -> dict | None:
    stats = _stats(self, rows)
    threshold = _threshold_status(stats, family)
    if not threshold["ready"]:
        return None
    if not _refit_delta_ready(self, scope_key, family, stats["effective_n"]):
        return None
    weights = _dependency_weights(rows)
    input_manifest = _input_manifest(rows)
    try:
        if family == "PLATT":
            params = _fit_platt(rows, weights)
        elif family == "BETA":
            params = _fit_beta(rows, weights)
        elif family == "ISOTONIC":
            params = _fit_isotonic(rows, weights)
        elif family == "PIT_ISOTONIC_CDF":
            params = _fit_pit_isotonic(rows, weights)
        else:
            raise ValueError("MODEL_ARTIFACT_INVALID")

        training_diag: dict[str, Any]
        if family == "PIT_ISOTONIC_CDF":
            training_diag = {
                "evaluation_scope": "TRAINING_DIAGNOSTIC",
                "oos_validated": False,
                "edge_claim": False,
                "pit_mapping": True,
            }
        else:
            raw = [float(row["raw_q"]) for row in rows]
            y = [int(row["outcome_y"]) for row in rows]
            calibrated = [_predict_parameters(family, params, q) for q in raw]
            training_diag = {
                "evaluation_scope": "TRAINING_DIAGNOSTIC",
                "oos_validated": False,
                "edge_claim": False,
                "raw_q": _weighted_binary_metrics(raw, y, weights),
                "calibrated": _weighted_binary_metrics(calibrated, y, weights),
                "raw_q_unweighted": _g1b._binary_metrics(raw, y),
                "calibrated_unweighted": _g1b._binary_metrics(calibrated, y),
                "reliability": _g1b._reliability(calibrated, y),
            }
        artifact = {
            "g1c_contract_version": G1C_CONTRACT_VERSION,
            "algorithm_version": _algorithm_version(family),
            "model_family": family,
            "scope_key": scope_key,
            "scope": scope_json,
            "training_cut_id": cut["cut_id"],
            "training_cut_sha256": cut["manifest_sha256"],
            "target_contract_version": G1C_TARGET_CONTRACT_VERSION,
            "fit_weight_contract_version": G1C_WEIGHT_CONTRACT_VERSION,
            "parameters": params,
        }
        artifact_sha = _sha(artifact)
        fit_run_id = _record_fit_run(
            self, cut=cut, family=family, scope_key=scope_key, scope_json=scope_json,
            stats=stats, input_manifest=input_manifest, status="FITTED_UNVALIDATED",
            rejection_reason=None, diagnostics=training_diag, artifact_sha=artifact_sha,
        )
        model_id = f"g1c-{family.lower()}-{artifact_sha[:24]}"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO g1c_shadow_models("
                "model_id,fit_run_id,model_family,algorithm_version,scope_key,scope_json,created_ts,"
                "training_cut_id,training_cut_sha256,training_cutoff,raw_n,effective_n,positive_n,negative_n,"
                "unique_q_n,parameters_json,training_diagnostics_json,artifact_sha256,status,oos_validated,"
                "production_selected,production_authority) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    model_id, fit_run_id, family, _algorithm_version(family), scope_key, _json(scope_json),
                    time.time(), cut["cut_id"], cut["manifest_sha256"], float(cut["cutoff_ts"]),
                    int(stats["raw_n"]), int(stats["effective_n"]), int(stats["positive_n"]),
                    int(stats["negative_n"]), int(stats["unique_q_n"]), _json(params), _json(training_diag),
                    artifact_sha, "FITTED_UNVALIDATED", 0, 0, 0,
                ),
            )
        return {"model_id": model_id, "family": family, "scope_key": scope_key, "artifact_sha256": artifact_sha}
    except ValueError as exc:
        reason = str(exc) if str(exc) else "OPTIMIZER_FAILED"
        _record_fit_run(
            self, cut=cut, family=family, scope_key=scope_key, scope_json=scope_json,
            stats=stats, input_manifest=input_manifest, status="CONTRACT_REJECTED",
            rejection_reason=reason, diagnostics={"edge_claim": False, "oos_validated": False},
            artifact_sha=None,
        )
        _record_error(self, reason, detail=f"fit {family} {scope_key}")
        return None


def g1c_refit(self: _ENGINE, *, force: bool = False, cutoff_ts: float | None = None) -> dict:
    """Create a frozen G.1A cut and fit research-only challengers when justified."""
    _ensure_tables(self)
    live_rows = _q_rows(self)
    live_stats = _stats(self, live_rows)
    global_ready = _threshold_status(live_stats, "PLATT")["ready"] or _threshold_status(live_stats, "BETA")["ready"]
    if not global_ready:
        return {"status": "INSUFFICIENT_EVIDENCE", "models_created": 0, "stats": live_stats}

    # Avoid creating cuts when no family has enough new independent evidence.
    if not force:
        needs_refit = any(
            _threshold_status(live_stats, family)["ready"]
            and _refit_delta_ready(self, "GLOBAL_TERMINAL_Q", family, live_stats["effective_n"])
            for family in ("PLATT", "BETA", "ISOTONIC", "PIT_ISOTONIC_CDF")
        )
        if not needs_refit:
            return {"status": "REFIT_DELTA_NOT_REACHED", "models_created": 0, "stats": live_stats}

    cut = self.create_g1_dataset_cut(cutoff_ts if cutoff_ts is not None else time.time())
    try:
        cut_meta, cut_rows = _revalidate_cut(self, cut["cut_id"])
    except ValueError as exc:
        _record_error(self, str(exc), detail=f"cut={cut.get('cut_id')}")
        return {"status": "CONTRACT_REJECTED", "models_created": 0, "reason": str(exc)}

    created = []
    for scope_key, scope_json, scope_rows in _scope_definitions(cut_rows):
        for family in ("PLATT", "BETA", "ISOTONIC", "PIT_ISOTONIC_CDF"):
            model = _fit_one(
                self, cut=cut_meta, family=family, scope_key=scope_key,
                scope_json=scope_json, rows=scope_rows,
            )
            if model:
                created.append(model)
    return {
        "status": "FITTED_UNVALIDATED" if created else "NO_NEW_MODEL",
        "models_created": len(created),
        "models": created,
        "cut_id": cut_meta["cut_id"],
        "cut_sha256": cut_meta["manifest_sha256"],
        "stats": _stats(self, cut_rows),
        "production_authority": False,
        "promotion_allowed": False,
    }


def _pending_q_observation(self: _ENGINE, observation_id: str) -> dict | None:
    with self._lock:
        row = self._conn.execute(
            "SELECT * FROM passive_market_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
    if row is None:
        return None
    row = dict(row)
    forecast = _loads(row.get("forecast_json"), {})
    if forecast.get("probability_measure") != "risk_neutral_Q_terminal":
        return None
    q = _g1b._cdf_value(forecast.get("terminal_q_cdf"), 0.0)
    if q is None:
        return None
    raw_q = 1.0 - float(q)
    cohort = _g1._cohort_contract(row, forecast)
    return {
        **row,
        "forecast": forecast,
        "raw_q": raw_q,
        "base_cohort_id": _sha(cohort),
        "base_cohort": cohort,
    }


def _model_matches(model: dict, observation: dict) -> bool:
    scope = _loads(model.get("scope_json"), {})
    kind = scope.get("kind")
    if kind == "global_terminal_q":
        return True
    if kind == "instrument":
        return str(scope.get("instrument")) == str(observation.get("instrument"))
    if kind == "g1a_cohort":
        return str(scope.get("cohort_id")) == str(observation.get("base_cohort_id"))
    return False


def g1c_predict_observation(self: _ENGINE, observation_id: str) -> dict:
    """Freeze prospective predictions from models that already existed at T0."""
    _ensure_tables(self)
    observation = _pending_q_observation(self, observation_id)
    if observation is None:
        return {"observation_id": observation_id, "predictions_created": 0, "status": "NOT_Q_OBSERVATION"}
    captured = float(observation["captured_ts"])
    target = _finite(observation.get("target_ts"))
    if target is None or target <= captured:
        return {"observation_id": observation_id, "predictions_created": 0, "status": "TIME_CONTRACT_INVALID"}
    # A prediction written after outcome availability is not prospective.
    if time.time() >= target:
        return {"observation_id": observation_id, "predictions_created": 0, "status": "PREDICTION_TOO_LATE"}
    with self._lock:
        models = [dict(row) for row in self._conn.execute(
            "SELECT * FROM g1c_shadow_models WHERE status='FITTED_UNVALIDATED' "
            "AND oos_validated=0 AND production_authority=0 AND created_ts<=? AND training_cutoff<? "
            "ORDER BY created_ts,model_id",
            (captured, captured),
        ).fetchall()]
    created = []
    for model in models:
        if model["model_family"] not in {"PLATT", "BETA", "ISOTONIC"}:
            continue
        if not _model_matches(model, observation):
            continue
        with self._lock:
            overlap = self._conn.execute(
                "SELECT 1 FROM g1_dataset_cut_members WHERE cut_id=? AND observation_id=? LIMIT 1",
                (model["training_cut_id"], observation_id),
            ).fetchone()
        if overlap is not None:
            _record_error(self, "PREDICTION_TRAINING_OVERLAP", model_id=model["model_id"], observation_id=observation_id)
            continue
        params = _loads(model.get("parameters_json"), {})
        try:
            shadow_p = _predict_parameters(model["model_family"], params, float(observation["raw_q"]))
        except ValueError as exc:
            _record_error(self, str(exc), model_id=model["model_id"], observation_id=observation_id)
            continue
        identity = {
            "prediction_contract_version": G1C_PREDICTION_CONTRACT_VERSION,
            "observation_id": observation_id,
            "model_id": model["model_id"],
            "model_artifact_sha256": model["artifact_sha256"],
            "raw_q": round(float(observation["raw_q"]), 12),
            "shadow_p": round(float(shadow_p), 12),
        }
        prediction_id = "g1c-pred-" + _sha(identity)[:24]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO g1c_shadow_predictions("
                "prediction_id,observation_id,captured_ts,model_id,model_artifact_sha256,training_cut_id,"
                "training_cutoff,model_family,model_scope_key,raw_q,shadow_calibrated_probability,"
                "target_contract_version,prediction_contract_version,prediction_status,authority,production_used,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    prediction_id, observation_id, captured, model["model_id"], model["artifact_sha256"],
                    model["training_cut_id"], float(model["training_cutoff"]), model["model_family"],
                    model["scope_key"], float(observation["raw_q"]), float(shadow_p),
                    G1C_TARGET_CONTRACT_VERSION, G1C_PREDICTION_CONTRACT_VERSION,
                    "PENDING_OUTCOME", "research_only", 0, time.time(),
                ),
            )
        if cursor.rowcount:
            created.append(prediction_id)
    return {
        "observation_id": observation_id,
        "predictions_created": len(created),
        "prediction_ids": created,
        "status": "PREDICTED" if created else "NO_ELIGIBLE_FROZEN_MODEL",
        "production_used": False,
    }


def _maybe_refit(self: _ENGINE, now: float) -> None:
    last = _finite(getattr(self, "_g1c_last_refit_check_ts", None))
    if last is not None and now - last < G1C_REFIT_INTERVAL_SEC:
        return
    self._g1c_last_refit_check_ts = now
    try:
        result = g1c_refit(self)
        self._g1c_last_refit_result = result
        self._g1c_last_refit_error = None
    except Exception as exc:  # noqa: BLE001
        self._g1c_last_refit_error = f"{type(exc).__name__}: {str(exc)[:180]}"


def collect_with_g1c(self: _ENGINE, instrument: str, now: float) -> list[str]:
    # Fit, if justified, strictly before the next prospective Q observation is
    # captured. This ensures any shadow prediction uses a model frozen before T0.
    _maybe_refit(self, float(now))
    created = _ORIGINAL_COLLECT(self, instrument, now)
    for observation_id in created:
        if str(observation_id).endswith("-native-expiry"):
            try:
                g1c_predict_observation(self, str(observation_id))
            except Exception as exc:  # noqa: BLE001
                _record_error(self, "SHADOW_P_INVALID", observation_id=str(observation_id), detail=f"{type(exc).__name__}: {str(exc)[:180]}")
    return created


def _model_rows(self: _ENGINE, limit: int = 200) -> list[dict]:
    _ensure_tables(self)
    with self._lock:
        rows = [dict(row) for row in self._conn.execute(
            "SELECT * FROM g1c_shadow_models ORDER BY created_ts DESC,model_id DESC LIMIT ?",
            (max(1, min(2000, int(limit))),),
        ).fetchall()]
    for row in rows:
        row["scope"] = _loads(row.pop("scope_json"), {})
        row["parameters"] = _loads(row.pop("parameters_json"), {})
        row["training_diagnostics"] = _loads(row.pop("training_diagnostics_json"), {})
        row["oos_validated"] = bool(row["oos_validated"])
        row["production_selected"] = bool(row["production_selected"])
        row["production_authority"] = bool(row["production_authority"])
    return rows


def g1c_models(self: _ENGINE, limit: int = 200) -> dict:
    return {
        "g1_stage": G1C_STAGE,
        "g1c_contract_version": G1C_CONTRACT_VERSION,
        "items": _model_rows(self, limit=limit),
        "authority": "research_only",
        "oos_validated": False,
        "production_authority": False,
        "promotion_allowed": False,
    }


def g1c_predictions(self: _ENGINE, limit: int = 200, instrument: str | None = None) -> dict:
    _ensure_tables(self)
    args: list[Any] = []
    clause = ""
    if instrument:
        clause = " WHERE p.instrument=?"
        args.append(str(instrument))
    args.append(max(1, min(2000, int(limit))))
    with self._lock:
        rows = [dict(row) for row in self._conn.execute(
            "SELECT s.*,p.instrument,p.target_ts,p.resolution_status FROM g1c_shadow_predictions s "
            "JOIN passive_market_observations p ON p.observation_id=s.observation_id" + clause +
            " ORDER BY s.captured_ts DESC,s.prediction_id DESC LIMIT ?",
            tuple(args),
        ).fetchall()]
    for row in rows:
        row["production_used"] = bool(row["production_used"])
    return {
        "g1_stage": G1C_STAGE,
        "prediction_contract_version": G1C_PREDICTION_CONTRACT_VERSION,
        "items": rows,
        "authority": "research_only",
        "production_used": False,
    }


def g1c_cohorts(self: _ENGINE) -> dict:
    rows = _q_rows(self)
    items = []
    for scope_key, scope_json, members in _scope_definitions(rows):
        stats = _stats(self, members)
        items.append({
            "scope_key": scope_key,
            "scope": scope_json,
            **stats,
            "platt": _threshold_status(stats, "PLATT"),
            "beta": _threshold_status(stats, "BETA"),
            "isotonic": _threshold_status(stats, "ISOTONIC"),
            "full_cdf": _threshold_status(stats, "PIT_ISOTONIC_CDF"),
        })
    return {
        "g1_stage": G1C_STAGE,
        "g1c_contract_version": G1C_CONTRACT_VERSION,
        "items": items,
        "authority": "research_only",
        "production_authority": False,
    }


def g1c_status(self: _ENGINE) -> dict:
    _ensure_tables(self)
    rows = _q_rows(self)
    stats = _stats(self, rows)
    with self._lock:
        model_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_shadow_models").fetchone()[0])
        prediction_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_shadow_predictions").fetchone()[0])
        fit_run_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_fit_runs").fetchone()[0])
        error_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_contract_errors").fetchone()[0])
        latest_model = self._conn.execute(
            "SELECT model_id,model_family,scope_key,created_ts,artifact_sha256 FROM g1c_shadow_models "
            "ORDER BY created_ts DESC LIMIT 1"
        ).fetchone()
        latest_prediction = self._conn.execute(
            "SELECT prediction_id,observation_id,model_id,captured_ts,shadow_calibrated_probability "
            "FROM g1c_shadow_predictions ORDER BY created_ts DESC LIMIT 1"
        ).fetchone()
        error_counts = {str(row["error_type"]): int(row["n"]) for row in self._conn.execute(
            "SELECT error_type,COUNT(*) n FROM g1c_contract_errors GROUP BY error_type"
        ).fetchall()}
    q_status = self.g1_q_status() if hasattr(self, "g1_q_status") else {}
    g1d = _g1d_status(stats, critical_contract_errors=error_n)
    thresholds = {
        "platt": _threshold_status(stats, "PLATT"),
        "beta": _threshold_status(stats, "BETA"),
        "isotonic": _threshold_status(stats, "ISOTONIC"),
        "full_cdf": _threshold_status(stats, "PIT_ISOTONIC_CDF"),
    }
    blocker_counts = Counter()
    for item in thresholds.values():
        blocker_counts.update(item["blockers"])
    return {
        "g1_stage": G1C_STAGE,
        "g1c_contract_version": G1C_CONTRACT_VERSION,
        "dataset_contract_version": _g1.G1_DATASET_CONTRACT_VERSION,
        "fit_threshold_contract_version": G1C_FIT_THRESHOLD_VERSION,
        "fit_weight_contract_version": G1C_WEIGHT_CONTRACT_VERSION,
        "refit_policy_version": G1C_REFIT_POLICY_VERSION,
        "prediction_contract_version": G1C_PREDICTION_CONTRACT_VERSION,
        "target_contract_version": G1C_TARGET_CONTRACT_VERSION,
        "generated_ts": time.time(),
        "q_captured": int(q_status.get("successful_q_capture_n", 0)),
        "q_resolved": int(q_status.get("resolved_q_observation_n", 0)),
        "q_eligible": stats["raw_n"],
        "effective_q_n": stats["effective_n"],
        "positive_n": stats["positive_n"],
        "negative_n": stats["negative_n"],
        "unique_q_n": stats["unique_q_n"],
        "fit_readiness": thresholds,
        "fit_run_n": fit_run_n,
        "frozen_model_n": model_n,
        "prospective_shadow_prediction_n": prediction_n,
        "latest_model": dict(latest_model) if latest_model is not None else None,
        "latest_prediction": dict(latest_prediction) if latest_prediction is not None else None,
        "ready_for_g1d": g1d["ready"],
        "g1d_readiness": g1d,
        "top_fit_blockers": dict(blocker_counts.most_common()),
        "contract_error_n": error_n,
        "contract_error_counts": dict(sorted(error_counts.items())),
        "last_refit_result": getattr(self, "_g1c_last_refit_result", None),
        "last_refit_error": getattr(self, "_g1c_last_refit_error", None),
        "calibrator_fitted": bool(model_n > 0),
        "shadow_model_fitting_allowed": bool(thresholds["platt"]["ready"] or thresholds["beta"]["ready"]),
        "production_model_training_allowed": False,
        "oos_validated": False,
        "edge_claim": False,
        "physical_probability_published": False,
        "production_authority": False,
        "production_replacement_allowed": False,
        "promotion_allowed": False,
        "sample_count_auto_promotion": False,
        "authority": "research_only",
    }


def init_g1c(self: _ENGINE, *args, **kwargs) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    try:
        _ensure_tables(self)
        self._g1c_last_refit_check_ts = None
        self._g1c_last_refit_result = None
        self._g1c_last_refit_error = None
    except Exception as exc:  # noqa: BLE001
        self._g1c_init_error = f"{type(exc).__name__}: {str(exc)[:180]}"


def install_g1_shadow_runtime() -> None:
    if getattr(_ENGINE, "_g1_shadow_runtime", None) == G1C_CONTRACT_VERSION:
        return
    _ENGINE.__init__ = init_g1c
    _ENGINE._collect_instrument = collect_with_g1c
    _ENGINE._g1c_ensure_tables = _ensure_tables
    _ENGINE._g1c_q_rows = _q_rows
    _ENGINE._g1c_stats = _stats
    _ENGINE._g1c_dependency_weights = staticmethod(_dependency_weights)
    _ENGINE._g1c_predict_parameters = staticmethod(_predict_parameters)
    _ENGINE.g1c_refit = g1c_refit
    _ENGINE.g1c_predict_observation = g1c_predict_observation
    _ENGINE.g1c_status = g1c_status
    _ENGINE.g1c_models = g1c_models
    _ENGINE.g1c_cohorts = g1c_cohorts
    _ENGINE.g1c_predictions = g1c_predictions
    _ENGINE._g1_shadow_runtime = G1C_CONTRACT_VERSION
