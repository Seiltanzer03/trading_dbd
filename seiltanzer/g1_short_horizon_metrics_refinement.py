"""G.1S evidence refinement: path statistics, frozen dependency weights and OOS context.

This layer is research-only. It does not change forecasts, production actions or
source observations. It appends derived outcomes only after the independent
short-horizon resolver has frozen the corresponding result.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from typing import Any

from .g1_short_horizon_runtime import (
    ShortHorizonRuntime,
    HORIZONS,
    OOS_CANDIDATE_REQUIRED,
    _brier,
    _logloss,
    _finite,
    _loads,
    _json,
)
from .g1_short_horizon_baseline_refinement import _momentum_probability


REFINEMENT_VERSION = "g1s-evidence-metrics-refinement-v1"
PATH_METRICS_VERSION = "g1s-path-metrics-v1"
DEPENDENCY_GROUP_VERSION = "g1s-finalized-dependency-group-v1"
EFFECTIVENESS_VERSION = "g1s-model-effectiveness-v1"
OOS_CONTEXT_MIN_EFFECTIVE_N = 60


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_path_metrics(
                observation_id TEXT PRIMARY KEY,
                horizon_minutes INTEGER NOT NULL,
                terminal_log_return REAL NOT NULL,
                return_atr_normalized REAL,
                return_rvol_normalized REAL,
                maximum_drawdown_log_return REAL,
                maximum_runup_log_return REAL,
                realized_volatility_1m REAL,
                time_to_mfe_sec REAL,
                time_to_mae_sec REAL,
                path_points_n INTEGER NOT NULL,
                path_source TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                metrics_sha256 TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_path_metrics_horizon "
            "ON g1s_path_metrics(horizon_minutes,observation_id)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_dependency_groups(
                dependency_group_id TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                bucket_start_ts REAL NOT NULL,
                bucket_end_ts REAL NOT NULL,
                member_count INTEGER NOT NULL,
                dependency_weight_per_member REAL NOT NULL,
                members_sha256 TEXT NOT NULL,
                finalized_ts REAL NOT NULL,
                contract_version TEXT NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_dependency_group_horizon "
            "ON g1s_dependency_groups(horizon_minutes,bucket_start_ts)")
        for table in ("g1s_path_metrics", "g1s_dependency_groups"):
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S evidence row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S evidence row'); END""")


def _path_samples(runtime: ShortHorizonRuntime, row: dict) -> tuple[list[dict], str]:
    instrument = str(row["instrument"])
    captured = float(row["captured_ts"])
    target = float(row["target_ts"])
    start = float(row["market_price"])
    with runtime._lock:
        bars = runtime._conn.execute(
            "SELECT bar_start_ts,bar_end_ts,high,low,close FROM passive_market_bars "
            "WHERE instrument=? AND bar_start_ts>=? AND bar_end_ts<=? "
            "ORDER BY bar_start_ts",
            (instrument, captured-1e-6, target+1e-6),
        ).fetchall()
        points = runtime._conn.execute(
            "SELECT ts,price FROM passive_market_path WHERE instrument=? AND ts>=? AND ts<=? "
            "ORDER BY ts",
            (instrument, captured-1e-6, target+1e-6),
        ).fetchall()
    if bars:
        samples = [{"ts": captured, "high": start, "low": start, "close": start}]
        samples.extend({"ts": float(b["bar_end_ts"]), "high": float(b["high"]),
                        "low": float(b["low"]), "close": float(b["close"])} for b in bars)
        return samples, "authoritative_1m_bars"
    samples = [{"ts": captured, "high": start, "low": start, "close": start}]
    samples.extend({"ts": float(p["ts"]), "high": float(p["price"]),
                    "low": float(p["price"]), "close": float(p["price"])} for p in points)
    return samples, "recorded_market_path"


def _metric_payload(runtime: ShortHorizonRuntime, row: dict) -> dict:
    samples, source = _path_samples(runtime, row)
    start = float(row["market_price"])
    terminal_log_return = float(row["terminal_log_return"])
    highs = [(s["ts"], math.log(s["high"] / start)) for s in samples if s["high"] > 0]
    lows = [(s["ts"], math.log(s["low"] / start)) for s in samples if s["low"] > 0]
    mfe_ts, mfe = max(highs, key=lambda x: x[1]) if highs else (None, None)
    mae_ts, mae = min(lows, key=lambda x: x[1]) if lows else (None, None)

    closes = [float(s["close"]) for s in samples if float(s["close"]) > 0]
    close_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    realized = statistics.pstdev(close_returns) if len(close_returns) >= 2 else None
    peak = trough = closes[0] if closes else start
    max_drawdown = 0.0
    max_runup = 0.0
    for price in closes:
        peak = max(peak, price)
        trough = min(trough, price)
        if peak > 0:
            max_drawdown = min(max_drawdown, math.log(price / peak))
        if trough > 0:
            max_runup = max(max_runup, math.log(price / trough))

    features = _loads(row.get("frozen_features_json"), {})
    forecast = _loads(row.get("frozen_forecast_json"), {})
    atr_state = ((features.get("price_state") or {}).get("atr") or {})
    intraday = ((features.get("price_state") or {}).get("g1s_intraday") or {})
    atr = _finite(atr_state.get("atr_price"))
    atr_norm = None
    if atr is not None and atr > 0:
        atr_norm = math.expm1(terminal_log_return) * start / atr

    one_minute_rvol = _finite(intraday.get("realized_vol_60m"))
    rvol_scale = None
    if one_minute_rvol is not None and one_minute_rvol > 0:
        rvol_scale = one_minute_rvol * math.sqrt(max(1, int(row["horizon_minutes"])))
    if rvol_scale is None or rvol_scale <= 0:
        rvol_scale = _finite(forecast.get("sigma_h_return"))
    rvol_norm = terminal_log_return / rvol_scale if rvol_scale and rvol_scale > 0 else None

    return {
        "contract_version": PATH_METRICS_VERSION,
        "observation_id": str(row["observation_id"]),
        "horizon_minutes": int(row["horizon_minutes"]),
        "terminal_log_return": terminal_log_return,
        "return_atr_normalized": atr_norm,
        "return_rvol_normalized": rvol_norm,
        "maximum_drawdown_log_return": max_drawdown,
        "maximum_runup_log_return": max_runup,
        "realized_volatility_1m": realized,
        "time_to_mfe_sec": None if mfe_ts is None else max(0.0, mfe_ts-float(row["captured_ts"])),
        "time_to_mae_sec": None if mae_ts is None else max(0.0, mae_ts-float(row["captured_ts"])),
        "mfe_log_return": mfe,
        "mae_log_return": mae,
        "path_points_n": len(samples),
        "path_source": source,
        "normalization": {
            "atr": "frozen ATR available at T0 only; no hindsight backfill",
            "rvol": "frozen T0 1m realized volatility scaled by sqrt(horizon), fallback frozen sigma_h",
        },
        "future_data_used_only_after_t0": True,
    }


def _dependency_bounds(row: dict) -> tuple[str, float, float]:
    horizon = int(row["horizon_minutes"])
    width = horizon * 60.0
    start = math.floor(float(row["captured_ts"]) / width) * width
    group_id = f"{row['instrument']}|H{horizon}|{int(start)}"
    return group_id, start, start + width


def _finalize_dependency_group(runtime: ShortHorizonRuntime, row: dict) -> None:
    group_id, bucket_start, bucket_end = _dependency_bounds(row)
    with runtime._lock:
        exists = runtime._conn.execute(
            "SELECT 1 FROM g1s_dependency_groups WHERE dependency_group_id=?", (group_id,)
        ).fetchone()
        if exists:
            return
        members = runtime._conn.execute(
            "SELECT observation_id FROM g1s_observations WHERE instrument=? AND horizon_minutes=? "
            "AND captured_ts>=? AND captured_ts<? AND training_eligible=1 "
            "ORDER BY captured_ts,observation_id",
            (row["instrument"], int(row["horizon_minutes"]), bucket_start, bucket_end),
        ).fetchall()
    ids = [str(m["observation_id"]) for m in members]
    if not ids:
        return
    # A resolved H observation cannot occur before its capture bucket has closed,
    # so membership is frozen when the first member is eligible for path metrics.
    weight = 1.0 / len(ids)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_dependency_groups(dependency_group_id,instrument,"
            "horizon_minutes,bucket_start_ts,bucket_end_ts,member_count,"
            "dependency_weight_per_member,members_sha256,finalized_ts,contract_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (group_id, row["instrument"], int(row["horizon_minutes"]), bucket_start,
             bucket_end, len(ids), weight, _sha(_json(ids)), time.time(),
             DEPENDENCY_GROUP_VERSION),
        )


def _materialize_path_metrics(runtime: ShortHorizonRuntime, limit: int = 5000) -> int:
    _ensure_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT g.*,r.terminal_log_return,r.direction_label,r.resolved_ts
            FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
            LEFT JOIN g1s_path_metrics m USING(observation_id)
            WHERE m.observation_id IS NULL
            ORDER BY r.resolved_ts,g.source_rowid LIMIT ?
        """, (max(1, min(int(limit), 10000)),)).fetchall()
    written = 0
    for raw in rows:
        row = dict(raw)
        payload = _metric_payload(runtime, row)
        encoded = _json(payload)
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_path_metrics(observation_id,horizon_minutes,"
                "terminal_log_return,return_atr_normalized,return_rvol_normalized,"
                "maximum_drawdown_log_return,maximum_runup_log_return,realized_volatility_1m,"
                "time_to_mfe_sec,time_to_mae_sec,path_points_n,path_source,metrics_json,"
                "metrics_sha256,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["observation_id"], int(row["horizon_minutes"]),
                 payload["terminal_log_return"], payload["return_atr_normalized"],
                 payload["return_rvol_normalized"], payload["maximum_drawdown_log_return"],
                 payload["maximum_runup_log_return"], payload["realized_volatility_1m"],
                 payload["time_to_mfe_sec"], payload["time_to_mae_sec"],
                 payload["path_points_n"], payload["path_source"], encoded, _sha(encoded),
                 time.time()),
            )
            written += int(cur.rowcount > 0)
        _finalize_dependency_group(runtime, row)
    return written


def _calibration(ps: list[float], ys: list[int], bins: int = 10) -> dict:
    if not ps:
        return {"ece": None, "reliability": []}
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in zip(ps, ys):
        idx = min(bins-1, max(0, int(float(p) * bins)))
        buckets[idx].append((float(p), int(y)))
    reliability = []
    ece = 0.0
    for idx, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_p = sum(v[0] for v in bucket) / len(bucket)
        event_rate = sum(v[1] for v in bucket) / len(bucket)
        ece += len(bucket) / len(ps) * abs(mean_p-event_rate)
        reliability.append({"bin": idx, "n": len(bucket), "mean_probability": mean_p,
                            "event_rate": event_rate})
    return {"ece": ece, "reliability": reliability}


def _balanced_accuracy(ps: list[float], ys: list[int]) -> float | None:
    positives = [i for i,y in enumerate(ys) if y == 1]
    negatives = [i for i,y in enumerate(ys) if y == 0]
    if not positives or not negatives:
        return None
    tpr = sum(ps[i] >= 0.5 for i in positives) / len(positives)
    tnr = sum(ps[i] < 0.5 for i in negatives) / len(negatives)
    return (tpr + tnr) / 2.0


def _auc(ps: list[float], ys: list[int]) -> float | None:
    pos = [p for p,y in zip(ps,ys) if y == 1]
    neg = [p for p,y in zip(ps,ys) if y == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            wins += p > n
            ties += p == n
    return (wins + 0.5*ties) / (len(pos)*len(neg))


def _cohort_stability(rows: list[dict], key: str) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "UNKNOWN")].append(row)
    items = []
    for label, group in grouped.items():
        ps = [float(r["p_up"]) for r in group]
        ys = [1 if r["direction_label"] == "UP" else 0 for r in group]
        mb = _brier(ps, ys)
        bb = _brier([0.5]*len(ys), ys)
        items.append({"cohort": label, "n": len(group), "brier": mb,
                      "delta_brier_vs_0_5": None if mb is None or bb is None else bb-mb})
    eligible = [x for x in items if x["n"] >= 20]
    return {"items": items,
            "stable_on_evaluable_cohorts": None if not eligible else all(
                (x["delta_brier_vs_0_5"] or 0) > 0 for x in eligible)}


def _effectiveness(runtime: ShortHorizonRuntime) -> dict:
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT p.model_id,p.p_up,p.created_ts AS prediction_created_ts,
                   g.observation_id,g.instrument,g.horizon_minutes,g.captured_ts,g.market_regime,
                   CASE WHEN json_valid(g.frozen_features_json)
                        THEN json_extract(g.frozen_features_json,
                             '$.price_state.g1s_intraday.ret_15m')
                        ELSE NULL END AS momentum_ret_15m,
                   r.direction_label,m.model_family,m.feature_set,
                   m.raw_n AS train_raw_n,m.effective_n AS train_effective_n,
                   m.artifact_sha256,m.created_ts AS model_created_ts
            FROM g1s_shadow_predictions p
            JOIN g1s_observations g USING(observation_id)
            JOIN g1s_resolutions r USING(observation_id)
            JOIN g1s_models m USING(model_id)
            WHERE r.direction_label!='FLAT' AND p.production_used=0
            ORDER BY g.captured_ts,p.model_id
        """).fetchall()
        trade_obs = {str(r[0]) for r in runtime._conn.execute(
            "SELECT DISTINCT observation_id FROM g1s_trade_links").fetchall()}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for raw in rows:
        grouped[str(raw["model_id"])].append(dict(raw))
    items = []
    for model_id, group in grouped.items():
        ps = [float(r["p_up"]) for r in group]
        ys = [1 if r["direction_label"] == "UP" else 0 for r in group]
        half = [0.5]*len(ys)
        momentum = [_momentum_probability(r) for r in group]
        prior_ps = []
        seen_up = seen_n = 0
        for y in ys:
            prior_ps.append(0.5 if seen_n < 20 else seen_up/seen_n)
            seen_n += 1; seen_up += y
        model_brier = _brier(ps, ys)
        model_logloss = _logloss(ps, ys)
        baselines = {
            "constant_0_5": {"brier": _brier(half, ys), "log_loss": _logloss(half, ys)},
            "chronological_base_rate": {"brier": _brier(prior_ps, ys),
                                         "log_loss": _logloss(prior_ps, ys)},
            "fixed_momentum_15m": {"brier": _brier(momentum, ys),
                                    "log_loss": _logloss(momentum, ys)},
        }
        groups = {runtime._dependency_key(r) for r in group}
        best_baseline_brier = min(v["brier"] for v in baselines.values() if v["brier"] is not None)
        best_baseline_log = min(v["log_loss"] for v in baselines.values() if v["log_loss"] is not None)
        if len(groups) < OOS_CONTEXT_MIN_EFFECTIVE_N:
            verdict = "INSUFFICIENT"
        elif model_brier is not None and model_logloss is not None and \
                model_brier < best_baseline_brier and model_logloss < best_baseline_log:
            verdict = "YES"
        else:
            verdict = "NO"
        trade_group = [r for r in group if str(r["observation_id"]) in trade_obs]
        trade_ps = [float(r["p_up"]) for r in trade_group]
        trade_ys = [1 if r["direction_label"] == "UP" else 0 for r in trade_group]
        trade_brier = _brier(trade_ps, trade_ys)
        trade_base = _brier([0.5]*len(trade_ys), trade_ys)
        cal = _calibration(ps, ys)
        head = group[0]
        items.append({
            "model_id": model_id,
            "model_family": head["model_family"],
            "feature_set": head["feature_set"],
            "horizon_minutes": int(head["horizon_minutes"]),
            "artifact_sha256": head["artifact_sha256"],
            "train": {"raw_n": int(head["train_raw_n"]),
                      "effective_n": float(head["train_effective_n"])},
            "oos": {"raw_n": len(group), "effective_n": len(groups),
                    "brier": model_brier, "log_loss": model_logloss,
                    "calibration_error_ece": cal["ece"],
                    "reliability": cal["reliability"],
                    "balanced_accuracy_secondary": _balanced_accuracy(ps, ys),
                    "roc_auc_secondary": _auc(ps, ys)},
            "baselines": baselines,
            "delta_brier_vs_0_5": None if model_brier is None else baselines["constant_0_5"]["brier"]-model_brier,
            "delta_brier_vs_base_rate": None if model_brier is None else baselines["chronological_base_rate"]["brier"]-model_brier,
            "delta_brier_vs_momentum": None if model_brier is None else baselines["fixed_momentum_15m"]["brier"]-model_brier,
            "does_model_beat_baseline_oos": verdict,
            "regime_stability": _cohort_stability(group, "market_regime"),
            "instrument_stability": _cohort_stability(group, "instrument"),
            "trade_aligned_n": len(trade_group),
            "trade_aligned_delta_brier_vs_0_5": None if trade_brier is None or trade_base is None else trade_base-trade_brier,
            "oos_validated": False,
            "edge_claim_allowed": False,
            "production_authority": False,
        })
    return {"contract_version": EFFECTIVENESS_VERSION,
            "primary_question": "Does the model beat baseline OOS?",
            "items": items,
            "accuracy_is_not_primary": True,
            "research_only": True,
            "production_authority": False,
            "edge_claim_allowed": False}


def install_g1_short_horizon_metrics_refinement() -> None:
    if getattr(ShortHorizonRuntime, "_evidence_metrics_refinement", None) == REFINEMENT_VERSION:
        return

    original_ensure = ShortHorizonRuntime._ensure_tables
    def ensure(self):
        original_ensure(self)
        _ensure_tables(self)
    ShortHorizonRuntime._ensure_tables = ensure

    original_step = ShortHorizonRuntime.step
    def step(self):
        result = original_step(self)
        try:
            result["path_metrics_created"] = _materialize_path_metrics(self)
        except Exception as exc:
            self._last_error = f"path metrics: {type(exc).__name__}: {str(exc)[:250]}"
            result["path_metrics_created"] = 0
            result["path_metrics_error"] = self._last_error
        return result
    ShortHorizonRuntime.step = step

    original_horizon_report = ShortHorizonRuntime.horizon_report
    def horizon_report(self, horizon: int):
        result = original_horizon_report(self, horizon)
        with self._lock:
            dependency = self._conn.execute(
                "SELECT COUNT(*) groups_n,COALESCE(SUM(member_count*dependency_weight_per_member),0) weight_sum "
                "FROM g1s_dependency_groups WHERE horizon_minutes=?", (int(horizon),)
            ).fetchone()
            anchors = self._conn.execute("""
                SELECT COUNT(DISTINCT p.anchor_group_id)
                FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
                JOIN passive_market_observations p ON p.observation_id=g.source_observation_id
                WHERE g.horizon_minutes=? AND g.training_eligible=1
            """, (int(horizon),)).fetchone()[0]
            regimes = self._conn.execute("""
                SELECT COUNT(DISTINCT COALESCE(g.market_regime,'UNKNOWN'))
                FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
                WHERE g.horizon_minutes=? AND g.training_eligible=1
            """, (int(horizon),)).fetchone()[0]
        temporal_blocks = int(result.get("trading_days") or 0)
        candidate_blockers = []
        observed = {
            "raw_resolved": int(result.get("raw_resolved") or 0),
            "effective_n": int(result.get("effective_n") or 0),
            "positive_n": int(result.get("positive_n") or 0),
            "negative_n": int(result.get("negative_n") or 0),
            "temporal_blocks": temporal_blocks,
        }
        from .g1_short_horizon_evidence_completion import (
            get_oos_candidate_required,
            get_min_volatility_regimes,
        )
        reqs = get_oos_candidate_required(horizon)
        for key, required in reqs.items():
            if observed.get(key, 0) < required:
                candidate_blockers.append(f"INSUFFICIENT_{key.upper()}")
        if regimes < get_min_volatility_regimes():
            candidate_blockers.append("INSUFFICIENT_VOLATILITY_REGIME_DIVERSITY")
        result["dependency_contract_version"] = DEPENDENCY_GROUP_VERSION
        result["dependency_groups_finalized"] = int(dependency["groups_n"] or 0)
        result["dependency_weight_sum"] = float(dependency["weight_sum"] or 0)
        result["unique_temporal_anchors"] = int(anchors or 0)
        result["volatility_regime_count"] = int(regimes or 0)
        result["oos_candidate"] = not candidate_blockers
        result["oos_candidate_blockers"] = candidate_blockers
        if not candidate_blockers:
            result["state"] = "OOS_CANDIDATE"
        result["supported"] = False
        result["supported_requires_future_walk_forward_superiority"] = True
        return result
    ShortHorizonRuntime.horizon_report = horizon_report

    original_status = ShortHorizonRuntime.status
    def status(self):
        result = original_status(self)
        now = time.time()
        with self._lock:
            old_eligible = int(self._conn.execute("""
                SELECT COUNT(*) FROM g1s_observations g
                LEFT JOIN g1s_resolutions r USING(observation_id)
                WHERE g.horizon_minutes=15 AND g.measurement_eligible=1
                  AND g.target_ts<=? AND r.observation_id IS NULL
            """, (now-15*60.0,)).fetchone()[0])
            h15_resolved = int(self._conn.execute("""
                SELECT COUNT(*) FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
                WHERE g.horizon_minutes=15 AND g.measurement_eligible=1
            """).fetchone()[0])
        alerts = []
        if old_eligible >= 100 and h15_resolved == 0:
            alerts.append({"code": "H15_RESOLUTION_CONTRACT_FAILURE", "severity": "ALERT",
                           "eligible_overdue_n": old_eligible,
                           "message": "100+ eligible H15 observations are overdue with zero resolved"})
        result["contract_alerts"] = alerts
        result["resolution_sla_monitoring"] = True
        result["path_metrics_contract_version"] = PATH_METRICS_VERSION
        result["dependency_contract_version"] = DEPENDENCY_GROUP_VERSION
        return result
    ShortHorizonRuntime.status = status

    def path_metrics(self, limit: int = 500):
        with self._lock:
            rows = self._conn.execute("""
                SELECT m.*,g.instrument,g.captured_ts,g.target_ts,g.origin
                FROM g1s_path_metrics m JOIN g1s_observations g USING(observation_id)
                ORDER BY g.captured_ts DESC LIMIT ?
            """, (max(1, min(int(limit), 5000)),)).fetchall()
        return {"contract_version": PATH_METRICS_VERSION,
                "items": [dict(r) for r in rows],
                "research_only": True, "production_authority": False}
    ShortHorizonRuntime.path_metrics = path_metrics
    ShortHorizonRuntime.effectiveness = _effectiveness
    ShortHorizonRuntime.prospective_oos = _effectiveness
    ShortHorizonRuntime._evidence_metrics_refinement = REFINEMENT_VERSION
