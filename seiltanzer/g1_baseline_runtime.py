"""Phase G.1B baseline measurement engine.

Consumes only the G.1A prospective dataset boundary.  This module measures
frozen forecast/reference quality and risk-neutral Q identity calibration; it
fits no calibrator, publishes no physical P, and has no production authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from typing import Any, Callable

from . import g1_dataset_runtime as _g1
from . import passive_learning as _pl

G1B_STAGE = "G.1B"
G1_BASELINE_CONTRACT_VERSION = "g1-baseline-metrics-v1"
G1_DIRECTION_EVENT_CONTRACT_VERSION = "terminal-log-return-positive-v1"
G1_BASE_RATE_CONTRACT_VERSION = "g1-prequential-base-rate-laplace-v1"
G1_RELIABILITY_CONTRACT_VERSION = "g1-reliability-10bin-v1"
G1_PIT_CONTRACT_VERSION = "g1-pit-10bin-v1"
G1_QUANTILE_SCORE_CONTRACT_VERSION = "g1-quantile-score-v1"
BASE_RATE_ALPHA = 1.0
RELIABILITY_BIN_COUNT = 10
PIT_BIN_COUNT = 10
QUANTILE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)
PIT_MATCH_TOLERANCE = 1e-6

_ENGINE = _pl.PassiveLearningEngine


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _cdf_arrays(cdf_obj: Any) -> tuple[list[float], list[float]] | None:
    if not isinstance(cdf_obj, dict):
        return None
    support = cdf_obj.get("support")
    cdf = cdf_obj.get("cdf")
    if not isinstance(support, list) or not isinstance(cdf, list) or len(support) != len(cdf) or len(cdf) < 2:
        return None
    xs: list[float] = []
    fs: list[float] = []
    for x, f in zip(support, cdf):
        xv, fv = _finite(x), _finite(f)
        if xv is None or fv is None:
            return None
        xs.append(xv)
        fs.append(fv)
    if any(xs[i] <= xs[i - 1] for i in range(1, len(xs))):
        return None
    if any(fs[i] < fs[i - 1] - 1e-12 for i in range(1, len(fs))):
        return None
    if any(f < -1e-9 or f > 1.0 + 1e-9 for f in fs):
        return None
    return xs, fs


def _cdf_value(cdf_obj: Any, value: float) -> float | None:
    arrays = _cdf_arrays(cdf_obj)
    x = _finite(value)
    if arrays is None or x is None:
        return None
    support, cdf = arrays
    if x <= support[0]:
        return max(0.0, min(1.0, cdf[0]))
    if x >= support[-1]:
        return max(0.0, min(1.0, cdf[-1]))
    for index in range(1, len(support)):
        if x <= support[index]:
            left_x, right_x = support[index - 1], support[index]
            left_f, right_f = cdf[index - 1], cdf[index]
            weight = (x - left_x) / (right_x - left_x)
            return max(0.0, min(1.0, left_f + weight * (right_f - left_f)))
    return None


def _cdf_quantile(cdf_obj: Any, tau: float) -> float | None:
    arrays = _cdf_arrays(cdf_obj)
    t = _finite(tau)
    if arrays is None or t is None or t < 0.0 or t > 1.0:
        return None
    support, cdf = arrays
    if t <= cdf[0]:
        return support[0]
    if t >= cdf[-1]:
        return support[-1]
    for index in range(1, len(cdf)):
        if t <= cdf[index] + 1e-15:
            left_f, right_f = cdf[index - 1], cdf[index]
            left_x, right_x = support[index - 1], support[index]
            if right_f <= left_f + 1e-15:
                return right_x
            weight = (t - left_f) / (right_f - left_f)
            return left_x + weight * (right_x - left_x)
    return None


def _binary_metrics(probabilities: list[float], outcomes: list[int]) -> dict:
    n = min(len(probabilities), len(outcomes))
    if n == 0:
        return {"n": 0, "brier": None, "log_loss": None}
    eps = 1e-12
    brier = 0.0
    log_loss = 0.0
    for probability, outcome in zip(probabilities[:n], outcomes[:n]):
        p = max(0.0, min(1.0, float(probability)))
        y = 1 if int(outcome) else 0
        brier += (p - y) ** 2
        pc = max(eps, min(1.0 - eps, p))
        log_loss -= y * math.log(pc) + (1 - y) * math.log(1.0 - pc)
    return {
        "n": n,
        "brier": round(brier / n, 10),
        "log_loss": round(log_loss / n, 10),
    }


def _reliability(probabilities: list[float], outcomes: list[int]) -> dict:
    n = min(len(probabilities), len(outcomes))
    bins: list[list[tuple[float, int]]] = [[] for _ in range(RELIABILITY_BIN_COUNT)]
    for probability, outcome in zip(probabilities[:n], outcomes[:n]):
        p = max(0.0, min(1.0, float(probability)))
        index = min(RELIABILITY_BIN_COUNT - 1, int(p * RELIABILITY_BIN_COUNT))
        bins[index].append((p, 1 if int(outcome) else 0))
    items = []
    ece = 0.0
    mce = 0.0
    for index, values in enumerate(bins):
        lower = index / RELIABILITY_BIN_COUNT
        upper = (index + 1) / RELIABILITY_BIN_COUNT
        if values:
            avg_probability = sum(p for p, _ in values) / len(values)
            empirical_rate = sum(y for _, y in values) / len(values)
            gap = abs(avg_probability - empirical_rate)
            if n:
                ece += (len(values) / n) * gap
            mce = max(mce, gap)
            avg_probability_out = round(avg_probability, 10)
            empirical_rate_out = round(empirical_rate, 10)
            gap_out = round(gap, 10)
        else:
            avg_probability_out = None
            empirical_rate_out = None
            gap_out = None
        items.append({
            "bin": index,
            "lower": lower,
            "upper": upper,
            "n": len(values),
            "avg_probability": avg_probability_out,
            "empirical_rate": empirical_rate_out,
            "absolute_gap": gap_out,
        })
    return {
        "contract_version": G1_RELIABILITY_CONTRACT_VERSION,
        "n": n,
        "bin_count": RELIABILITY_BIN_COUNT,
        "ece": round(ece, 10) if n else None,
        "mce": round(mce, 10) if n else None,
        "bins": items,
    }


def _pit_metrics(pits: list[float]) -> dict:
    clean = [max(0.0, min(1.0, float(value))) for value in pits if _finite(value) is not None]
    n = len(clean)
    counts = [0] * PIT_BIN_COUNT
    for value in clean:
        index = min(PIT_BIN_COUNT - 1, int(value * PIT_BIN_COUNT))
        counts[index] += 1
    rates = [count / n if n else None for count in counts]
    if n:
        mean = sum(clean) / n
        variance = sum((value - mean) ** 2 for value in clean) / n
        ordered = sorted(clean)
        d_plus = max((index + 1) / n - value for index, value in enumerate(ordered))
        d_minus = max(value - index / n for index, value in enumerate(ordered))
        ks = max(d_plus, d_minus)
        max_bin_deviation = max(abs(rate - 1.0 / PIT_BIN_COUNT) for rate in rates if rate is not None)
    else:
        mean = variance = ks = max_bin_deviation = None
    histogram = [
        {
            "bin": index,
            "lower": index / PIT_BIN_COUNT,
            "upper": (index + 1) / PIT_BIN_COUNT,
            "n": counts[index],
            "rate": round(rates[index], 10) if rates[index] is not None else None,
        }
        for index in range(PIT_BIN_COUNT)
    ]
    return {
        "contract_version": G1_PIT_CONTRACT_VERSION,
        "n": n,
        "mean": round(mean, 10) if mean is not None else None,
        "variance": round(variance, 10) if variance is not None else None,
        "uniform_reference_mean": 0.5,
        "uniform_reference_variance": round(1.0 / 12.0, 10),
        "ks_distance_to_uniform": round(ks, 10) if ks is not None else None,
        "max_bin_deviation_from_uniform": (
            round(max_bin_deviation, 10) if max_bin_deviation is not None else None
        ),
        "p_value": None,
        "histogram": histogram,
    }


def _pinball(y: float, q: float, tau: float) -> float:
    error = y - q
    return tau * error if error >= 0 else (tau - 1.0) * error


def _quantile_metrics(
    rows: list[dict], provider: Callable[[dict, float], float | None]
) -> dict:
    levels = {}
    for tau in QUANTILE_LEVELS:
        predictions: list[float] = []
        outcomes: list[float] = []
        for row in rows:
            y = _finite((row.get("outcome") or {}).get("future_log_return"))
            q = provider(row, tau)
            if y is None or q is None:
                continue
            predictions.append(q)
            outcomes.append(y)
        n = len(outcomes)
        if n:
            coverage = sum(1 for y, q in zip(outcomes, predictions) if y <= q) / n
            loss = sum(_pinball(y, q, tau) for y, q in zip(outcomes, predictions)) / n
        else:
            coverage = loss = None
        levels[f"q{int(round(tau * 100)):02d}"] = {
            "tau": tau,
            "n": n,
            "coverage": round(coverage, 10) if coverage is not None else None,
            "coverage_error": round(coverage - tau, 10) if coverage is not None else None,
            "pinball_loss": round(loss, 12) if loss is not None else None,
        }

    def interval(low_tau: float, high_tau: float, nominal: float) -> dict:
        values = []
        for row in rows:
            y = _finite((row.get("outcome") or {}).get("future_log_return"))
            low = provider(row, low_tau)
            high = provider(row, high_tau)
            if y is None or low is None or high is None or high < low:
                continue
            values.append(low <= y <= high)
        n = len(values)
        coverage = sum(1 for value in values if value) / n if n else None
        return {
            "nominal": nominal,
            "n": n,
            "coverage": round(coverage, 10) if coverage is not None else None,
            "coverage_error": round(coverage - nominal, 10) if coverage is not None else None,
        }

    return {
        "contract_version": G1_QUANTILE_SCORE_CONTRACT_VERSION,
        "levels": levels,
        "central_intervals": {
            "50pct": interval(0.25, 0.75, 0.50),
            "80pct": interval(0.10, 0.90, 0.80),
        },
        "crps": None,
    }


def _future_direction(row: dict) -> int | None:
    value = _finite((row.get("outcome") or {}).get("future_log_return"))
    if value is None:
        return None
    return 1 if value > 0.0 else 0


def _q_up_probability(row: dict) -> float | None:
    forecast = row.get("forecast") or {}
    cdf_at_zero = _cdf_value(forecast.get("terminal_q_cdf"), 0.0)
    return None if cdf_at_zero is None else max(0.0, min(1.0, 1.0 - cdf_at_zero))


def _fixed_quantile(row: dict, tau: float) -> float | None:
    forecast = row.get("forecast") or {}
    values = forecast.get("gaussian_reference_quantiles_log_return") or {}
    key = f"q{int(round(tau * 100)):02d}"
    return _finite(values.get(key))


def _q_quantile(row: dict, tau: float) -> float | None:
    return _cdf_quantile((row.get("forecast") or {}).get("terminal_q_cdf"), tau)


def _sample_manifest(rows: list[dict]) -> str:
    payload = [
        {
            "observation_id": str(row.get("observation_id")),
            "source_record_sha256": str(row.get("source_record_sha256") or ""),
        }
        for row in sorted(rows, key=lambda item: str(item.get("observation_id")))
    ]
    return _sha256(payload)


def _effective_sample_rows(rows: list[dict]) -> list[dict]:
    """Deterministic cohort-local non-overlap representatives.

    Mirrors G.1A effective-n semantics while returning the exact observations
    used by G.1B primary metrics. One dependency group contributes at most one
    representative to a cohort and overlapping future windows are skipped.
    """
    by_cohort: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_cohort[str(row.get("base_cohort_id"))].append(row)
    selected: list[dict] = []
    for cohort_id in sorted(by_cohort):
        members = by_cohort[cohort_id]
        dependencies: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in members:
            dependencies[(str(row.get("instrument")), str(row.get("dependency_group_id")))].append(row)
        by_instrument: dict[str, list[dict]] = defaultdict(list)
        for (instrument, dependency_group_id), dep_members in dependencies.items():
            representative = min(
                dep_members,
                key=lambda item: (
                    float(item.get("captured_ts") or 0.0),
                    float(item.get("target_ts") or 0.0),
                    str(item.get("observation_id")),
                ),
            )
            by_instrument[instrument].append({
                "dependency_group_id": dependency_group_id,
                "captured_ts": min(float(item["captured_ts"]) for item in dep_members),
                "target_ts": max(float(item["target_ts"]) for item in dep_members),
                "representative": representative,
            })
        for instrument in sorted(by_instrument):
            last_end = -math.inf
            for interval in sorted(
                by_instrument[instrument],
                key=lambda item: (
                    float(item["captured_ts"]),
                    float(item["target_ts"]),
                    str(item["dependency_group_id"]),
                ),
            ):
                if float(interval["captured_ts"]) >= last_end - 1e-9:
                    selected.append(interval["representative"])
                    last_end = float(interval["target_ts"])
    return sorted(
        selected,
        key=lambda item: (
            float(item.get("captured_ts") or 0.0),
            str(item.get("base_cohort_id")),
            str(item.get("observation_id")),
        ),
    )


def _prequential_base_rate(rows: list[dict]) -> tuple[list[float], list[int], dict]:
    """Past-only Laplace-smoothed base rate, cohort-local and deterministic."""
    state: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    probabilities: list[float] = []
    outcomes: list[int] = []
    history_before: list[int] = []
    for row in sorted(
        rows,
        key=lambda item: (
            float(item.get("captured_ts") or 0.0),
            str(item.get("base_cohort_id")),
            str(item.get("observation_id")),
        ),
    ):
        y = _future_direction(row)
        if y is None:
            continue
        cohort_id = str(row.get("base_cohort_id"))
        successes, n = state[cohort_id]
        probability = (successes + BASE_RATE_ALPHA) / (n + 2.0 * BASE_RATE_ALPHA)
        probabilities.append(probability)
        outcomes.append(y)
        history_before.append(n)
        state[cohort_id][0] += y
        state[cohort_id][1] += 1
    return probabilities, outcomes, {
        "contract_version": G1_BASE_RATE_CONTRACT_VERSION,
        "alpha": BASE_RATE_ALPHA,
        "cohort_local": True,
        "past_only": True,
        "random_shuffle": False,
        "cold_start_probability": 0.5,
        "cold_start_n": sum(1 for value in history_before if value == 0),
        "min_history_before_prediction": min(history_before) if history_before else None,
        "max_history_before_prediction": max(history_before) if history_before else None,
    }


def _direction_baselines(rows: list[dict]) -> dict:
    outcomes = [value for row in rows if (value := _future_direction(row)) is not None]
    uninformed_probabilities = [0.5] * len(outcomes)
    base_probabilities, base_outcomes, base_meta = _prequential_base_rate(rows)
    return {
        "event_contract_version": G1_DIRECTION_EVENT_CONTRACT_VERSION,
        "event": "terminal_log_return_gt_0",
        "sample_semantics": "G1A effective non-overlap observations",
        "uninformed_0_5": {
            "metrics": _binary_metrics(uninformed_probabilities, outcomes),
            "reliability": _reliability(uninformed_probabilities, outcomes),
        },
        "prequential_base_rate": {
            **base_meta,
            "metrics": _binary_metrics(base_probabilities, base_outcomes),
            "reliability": _reliability(base_probabilities, base_outcomes),
        },
    }


def _metric_evidence_status(effective_n: int, span_days: float) -> str:
    if effective_n < 30:
        return "INSUFFICIENT"
    if effective_n < 100 or span_days < 7:
        return "EARLY"
    if effective_n < 300 or span_days < 30:
        return "PROVISIONAL"
    return "SUPPORTED"


def _q_metrics(rows: list[dict]) -> dict:
    q_rows = [row for row in rows if int(row.get("q_to_p_eligible") or 0) == 1]
    effective_q_rows = _effective_sample_rows(q_rows)
    valid_rows: list[dict] = []
    pits: list[float] = []
    mismatch_n = 0
    for row in effective_q_rows:
        outcome = row.get("outcome") or {}
        terminal = outcome.get("terminal") or {}
        realized = _finite(outcome.get("future_log_return"))
        stored = _finite(terminal.get("terminal_pit_q"))
        recomputed = _cdf_value((row.get("forecast") or {}).get("terminal_q_cdf"), realized) if realized is not None else None
        if stored is None or recomputed is None or abs(stored - recomputed) > PIT_MATCH_TOLERANCE:
            mismatch_n += 1
            continue
        valid_rows.append(row)
        pits.append(recomputed)

    probabilities: list[float] = []
    outcomes: list[int] = []
    scored_rows: list[dict] = []
    for row in valid_rows:
        probability = _q_up_probability(row)
        outcome = _future_direction(row)
        if probability is None or outcome is None:
            continue
        probabilities.append(probability)
        outcomes.append(outcome)
        scored_rows.append(row)

    uninformed = [0.5] * len(outcomes)
    base_probabilities, base_outcomes, base_meta = _prequential_base_rate(scored_rows)
    q_binary = _binary_metrics(probabilities, outcomes)
    uninformed_binary = _binary_metrics(uninformed, outcomes)
    base_binary = _binary_metrics(base_probabilities, base_outcomes)

    def improvement(baseline: dict, candidate: dict, key: str) -> float | None:
        left = _finite(baseline.get(key))
        right = _finite(candidate.get(key))
        return round(left - right, 10) if left is not None and right is not None else None

    q_first_ts = min((float(row["captured_ts"]) for row in valid_rows), default=None)
    q_last_ts = max((float(row["captured_ts"]) for row in valid_rows), default=None)
    q_span_days = (
        max(0.0, (q_last_ts - q_first_ts) / 86400.0)
        if q_first_ts is not None and q_last_ts is not None else 0.0
    )
    return {
        "semantics": "risk_neutral_Q_terminal_identity_not_physical_P",
        "q_identity_transform": "none",
        "raw_q_eligible_n": len(q_rows),
        "effective_q_n": len(effective_q_rows),
        "metrics_eligible_n": len(valid_rows),
        "direction_metrics_eligible_n": len(scored_rows),
        "pit_contract_mismatch_n": mismatch_n,
        "pit_match_tolerance": PIT_MATCH_TOLERANCE,
        "eligible_time_span_days": round(q_span_days, 10),
        "evidence_status": _metric_evidence_status(len(valid_rows), q_span_days),
        "evidence_status_scope": "Q_identity_measurement_only_not_edge_claim",
        "sample_manifest_sha256": _sample_manifest(valid_rows),
        "direction_event": {
            "event_contract_version": G1_DIRECTION_EVENT_CONTRACT_VERSION,
            "event": "terminal_log_return_gt_0",
            "q_identity": {
                "metrics": q_binary,
                "reliability": _reliability(probabilities, outcomes),
            },
            "uninformed_0_5": {
                "metrics": uninformed_binary,
                "reliability": _reliability(uninformed, outcomes),
            },
            "prequential_base_rate": {
                **base_meta,
                "metrics": base_binary,
                "reliability": _reliability(base_probabilities, base_outcomes),
            },
            "descriptive_improvement_positive_is_better": {
                "brier_vs_uninformed": improvement(uninformed_binary, q_binary, "brier"),
                "log_loss_vs_uninformed": improvement(uninformed_binary, q_binary, "log_loss"),
                "brier_vs_prequential_base_rate": improvement(base_binary, q_binary, "brier"),
                "log_loss_vs_prequential_base_rate": improvement(base_binary, q_binary, "log_loss"),
                "edge_claim": False,
            },
        },
        "pit": _pit_metrics(pits),
        "quantiles": _quantile_metrics(valid_rows, _q_quantile),
        "crps": None,
        "physical_probability_published": False,
    }


def _fixed_metrics(rows: list[dict]) -> dict:
    fixed_rows = [
        row for row in rows
        if str(row.get("forecast_family")) == "FIXED_HORIZON_MARKET_FORECAST"
    ]
    effective_fixed_rows = _effective_sample_rows(fixed_rows)
    return {
        "semantics": "historical_gaussian_reference_geometry_not_Q_not_physical_P",
        "raw_n": len(fixed_rows),
        "effective_n": len(effective_fixed_rows),
        "sample_manifest_sha256": _sample_manifest(effective_fixed_rows),
        "quantiles": _quantile_metrics(effective_fixed_rows, _fixed_quantile),
        "crps": None,
    }


def _load_rows(self: _ENGINE, *, cut_id: str | None = None) -> tuple[list[dict], dict]:
    self._g1_sync_membership(limit=5000)
    cut_join = ""
    cut_clause = ""
    source_scope = "live_g1a_eligible_view"
    cutoff_ts = time.time()
    if cut_id:
        with self._lock:
            cut = self._conn.execute(
                "SELECT cut_id,cutoff_ts,manifest_sha256,status FROM g1_dataset_cuts "
                "WHERE cut_id=? AND dataset_contract_version=?",
                (str(cut_id), _g1.G1_DATASET_CONTRACT_VERSION),
            ).fetchone()
        if cut is None:
            raise KeyError(f"unknown G1 dataset cut: {cut_id}")
        cut = dict(cut)
        cut_join = (
            " JOIN g1_dataset_cut_members c ON c.observation_id=p.observation_id "
            "AND c.cut_id=? "
        )
        cut_clause = " AND c.forecast_eval_eligible=1"
        source_scope = "frozen_g1a_dataset_cut"
        cutoff_ts = float(cut["cutoff_ts"])
    query = (
        "SELECT p.observation_id,p.anchor_group_id,p.captured_ts,p.target_ts,p.resolved_ts,"
        "p.instrument,p.horizon_minutes,p.market_price,p.market_regime,p.session,"
        "p.forecast_json,p.outcome_json,g.source_record_sha256,g.forecast_eval_eligible,"
        "g.q_to_p_eligible,g.terminal_q_eligible,g.first_touch_q_eligible,g.forecast_family,"
        "g.base_cohort_id,g.base_cohort_json,g.regime_stratum,g.session_stratum,"
        "g.dependency_group_id "
        "FROM g1_dataset_membership g JOIN passive_market_observations p "
        "ON p.observation_id=g.observation_id" + cut_join +
        " WHERE g.dataset_contract_version=? AND g.forecast_eval_eligible=1" + cut_clause +
        " AND NOT EXISTS(SELECT 1 FROM g1_contract_errors e WHERE "
        "e.dataset_contract_version=g.dataset_contract_version "
        "AND e.observation_id=g.observation_id AND e.error_type='SOURCE_MUTATED') "
        "ORDER BY p.captured_ts,p.observation_id"
    )
    if cut_id:
        sql_args = (str(cut_id), _g1.G1_DATASET_CONTRACT_VERSION)
    else:
        sql_args = (_g1.G1_DATASET_CONTRACT_VERSION,)
    with self._lock:
        rows = [dict(row) for row in self._conn.execute(query, sql_args).fetchall()]
    for row in rows:
        row["forecast"] = _loads(row.pop("forecast_json"), {})
        row["outcome"] = _loads(row.pop("outcome_json"), {})
        row["base_cohort"] = _loads(row.get("base_cohort_json"), {})
    return rows, {
        "source_scope": source_scope,
        "cut_id": str(cut_id) if cut_id else None,
        "data_cutoff_ts": cutoff_ts,
    }


def _report_for_rows(rows: list[dict], source_meta: dict) -> dict:
    effective_rows = _effective_sample_rows(rows)
    captured_values = [float(row["captured_ts"]) for row in effective_rows]
    first_ts = min(captured_values) if captured_values else None
    last_ts = max(captured_values) if captured_values else None
    span_days = (
        max(0.0, (last_ts - first_ts) / 86400.0)
        if first_ts is not None and last_ts is not None else 0.0
    )
    q = _q_metrics(rows)
    return {
        "g1_stage": G1B_STAGE,
        "baseline_contract_version": G1_BASELINE_CONTRACT_VERSION,
        "dataset_contract_version": _g1.G1_DATASET_CONTRACT_VERSION,
        "effective_n_contract_version": _g1.G1_EFFECTIVE_N_CONTRACT_VERSION,
        "generated_ts": time.time(),
        **source_meta,
        "sample_manifest_sha256": _sample_manifest(effective_rows),
        "raw_forecast_eval_n": len(rows),
        "unique_observation_n": len({str(row.get("observation_id")) for row in rows}),
        "unique_anchor_n": len({str(row.get("dependency_group_id")) for row in rows}),
        "effective_n": len(effective_rows),
        "eligible_time_span_days": round(span_days, 10),
        "cohort_count": len({str(row.get("base_cohort_id")) for row in rows}),
        "directional_baselines": _direction_baselines(effective_rows),
        "fixed_horizon_reference": _fixed_metrics(rows),
        "terminal_q_identity": q,
        "evidence_status": _metric_evidence_status(len(effective_rows), span_days),
        "evidence_status_scope": "baseline_measurement_only_not_edge_claim",
        "q_evidence_status": q["evidence_status"],
        "calibrator_fitted": False,
        "calibrator_registry_writes": False,
        "g1_training_allowed": False,
        "physical_probability_published": False,
        "production_authority": False,
        "promotion_allowed": False,
        "production_replacement_allowed": False,
        "sample_count_auto_promotion": False,
        "authority": "research_only",
    }


def g1_baseline_status(self: _ENGINE, cut_id: str | None = None) -> dict:
    rows, source_meta = _load_rows(self, cut_id=cut_id)
    return _report_for_rows(rows, source_meta)


def g1_baseline_cohorts(self: _ENGINE, cut_id: str | None = None) -> dict:
    rows, source_meta = _load_rows(self, cut_id=cut_id)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("base_cohort_id"))].append(row)
    items = []
    for cohort_id in sorted(grouped):
        members = grouped[cohort_id]
        report = _report_for_rows(members, source_meta)
        regimes = {str(row.get("regime_stratum")) for row in members}
        sessions = {str(row.get("session_stratum")) for row in members}
        items.append({
            "cohort_id": cohort_id,
            "base_cohort": members[0].get("base_cohort") or {},
            "regime_counts": dict(sorted((value, sum(1 for row in members if str(row.get("regime_stratum")) == value)) for value in regimes)),
            "session_counts": dict(sorted((value, sum(1 for row in members if str(row.get("session_stratum")) == value)) for value in sessions)),
            "raw_n": report["raw_forecast_eval_n"],
            "unique_anchor_n": report["unique_anchor_n"],
            "effective_n": report["effective_n"],
            "sample_manifest_sha256": report["sample_manifest_sha256"],
            "directional_baselines": report["directional_baselines"],
            "fixed_horizon_reference": report["fixed_horizon_reference"],
            "terminal_q_identity": report["terminal_q_identity"],
            "evidence_status": report["evidence_status"],
            "evidence_status_scope": report["evidence_status_scope"],
        })
    return {
        "g1_stage": G1B_STAGE,
        "baseline_contract_version": G1_BASELINE_CONTRACT_VERSION,
        "dataset_contract_version": _g1.G1_DATASET_CONTRACT_VERSION,
        "generated_ts": time.time(),
        **source_meta,
        "items": items,
        "calibrator_fitted": False,
        "g1_training_allowed": False,
        "physical_probability_published": False,
        "production_authority": False,
        "promotion_allowed": False,
        "production_replacement_allowed": False,
        "authority": "research_only",
    }


def install_g1_baseline_runtime() -> None:
    if getattr(_ENGINE, "_g1_baseline_runtime", None) == G1_BASELINE_CONTRACT_VERSION:
        return
    _ENGINE.g1_baseline_status = g1_baseline_status
    _ENGINE.g1_baseline_cohorts = g1_baseline_cohorts
    _ENGINE._g1b_effective_sample_rows = staticmethod(_effective_sample_rows)
    _ENGINE._g1b_prequential_base_rate = staticmethod(_prequential_base_rate)
    _ENGINE._g1b_q_up_probability = staticmethod(_q_up_probability)
    _ENGINE._g1b_cdf_value = staticmethod(_cdf_value)
    _ENGINE._g1b_cdf_quantile = staticmethod(_cdf_quantile)
    _ENGINE._g1_baseline_runtime = G1_BASELINE_CONTRACT_VERSION
