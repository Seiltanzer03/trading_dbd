"""Real-bar historical walk-forward bootstrap for G.1S.

This is a research-only fast path for choosing *provisional* learned artifacts
before the slow live prospective stream reaches serious sample size.

Important semantics:
- evidence label is HISTORICAL_WALK_FORWARD, never LIVE_PROSPECTIVE_OOS;
- source bars are real Yahoo/yfinance 5m bars, stored immutably with hashes;
- T0 is the close of a completed 5m bar (Yahoo index timestamp + 5 minutes);
- only features reproducible both historically and in live G.1S V2 are used;
- historical option/Greek/cross-asset features are not synthesized;
- expanding chronological folds use purge + embargo and dependency weights;
- a winner is only PROVISIONAL_LEARNED and has no production authority;
- the exact refit artifact may then start a separate frozen
  LIVE_PROSPECTIVE_OOS cohort. Historical fold outcomes never count as live OOS.
"""
from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from typing import Any

import numpy as np

from . import g1_short_horizon_integration as _integration
from . import storage_runtime as _storage
from .config import INSTRUMENTS
from .g1_short_horizon_champion_runtime import (
    CHAMPION_PROGRESS_VERSION,
    DIRECTION_TARGET,
    RETURN_TARGET,
    _ensure_tables as _ensure_champion_tables,
)
from .g1_short_horizon_feature_contract_v2 import _v2_values
from .g1_short_horizon_runtime import HORIZONS, ShortHorizonRuntime


HISTORICAL_WF_CONTRACT_VERSION = "g1s-historical-wf-real-bars-v1"
HISTORICAL_FEATURE_CONTRACT = "g1s-historical-bar-only-v1"
HISTORICAL_FEATURE_SET = "HISTORICAL_BAR_ONLY_V1"
HISTORICAL_PROB_MODEL_FAMILY = "HISTORICAL_WF_LOGISTIC_V1"
HISTORICAL_RETURN_MODEL_FAMILY = "HISTORICAL_WF_RIDGE_V1"
HISTORICAL_EVIDENCE_LABEL = "HISTORICAL_WALK_FORWARD"
LIVE_EVIDENCE_LABEL = "LIVE_PROSPECTIVE_OOS"
SOURCE_PROVIDER = "Yahoo Finance via yfinance"
SOURCE_INTERVAL = "5m"
SOURCE_PERIOD = "60d"
BAR_SECONDS = 5 * 60.0
EMBARGO_SECONDS = BAR_SECONDS
FOLD_COUNT = 4
INITIAL_TRAIN_FRACTION = 0.40
MIN_SOURCE_BARS = 1000
MIN_HISTORICAL_RAW = 1000
MIN_HISTORICAL_EFFECTIVE = 400
MIN_PROVISIONAL_RELATIVE_IMPROVEMENT = 0.005
PROB_L2 = 0.25
RIDGE_L2 = 1.0
FEATURE_NAMES = (
    "ret_5m", "ret_15m", "ret_60m",
    "realized_vol_15m", "realized_vol_60m",
)
INSTRUMENT_DUMMIES = tuple(INSTRUMENTS)[1:]
MODEL_FEATURE_NAMES = FEATURE_NAMES + tuple(f"instrument:{code}" for code in INSTRUMENT_DUMMIES)
HISTORICAL_CRITICAL_TABLES = (
    "g1s_historical_sources",
    "g1s_historical_wf_runs",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(raw: str | bytes) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clip_probability(value: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, float(value)))


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    _ensure_champion_tables(runtime)
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_historical_sources(
                source_id TEXT PRIMARY KEY,
                contract_version TEXT NOT NULL,
                instrument TEXT NOT NULL,
                ticker TEXT NOT NULL,
                provider TEXT NOT NULL,
                interval TEXT NOT NULL,
                requested_period TEXT NOT NULL,
                fetched_ts REAL NOT NULL,
                first_bar_end_ts REAL NOT NULL,
                last_bar_end_ts REAL NOT NULL,
                bar_count INTEGER NOT NULL,
                calendar_span_days REAL NOT NULL,
                source_sha256 TEXT NOT NULL,
                bars_gzip BLOB NOT NULL,
                source_semantics_json TEXT NOT NULL,
                created_ts REAL NOT NULL,
                UNIQUE(contract_version,instrument,source_sha256)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_hist_source_instrument_created "
            "ON g1s_historical_sources(instrument,created_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_historical_wf_runs(
                run_id TEXT PRIMARY KEY,
                contract_version TEXT NOT NULL,
                evidence_label TEXT NOT NULL,
                source_set_sha256 TEXT NOT NULL,
                target TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                feature_set TEXT NOT NULL,
                model_family TEXT NOT NULL,
                fold_count INTEGER NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                positive_n INTEGER NOT NULL,
                negative_n INTEGER NOT NULL,
                historical_winner INTEGER NOT NULL,
                provisional_model_id TEXT,
                verdict TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                production_authority INTEGER NOT NULL DEFAULT 0,
                auto_promotion INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                UNIQUE(contract_version,source_set_sha256,target,horizon_minutes,feature_set,model_family)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_hist_wf_target_horizon "
            "ON g1s_historical_wf_runs(target,horizon_minutes,created_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_historical_wf_state(
                id INTEGER PRIMARY KEY CHECK(id=1),
                contract_version TEXT NOT NULL,
                state TEXT NOT NULL,
                last_started_ts REAL,
                last_success_ts REAL,
                last_error TEXT,
                source_set_sha256 TEXT,
                source_count INTEGER NOT NULL DEFAULT 0,
                run_count INTEGER NOT NULL DEFAULT 0,
                provisional_count INTEGER NOT NULL DEFAULT 0,
                updated_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_historical_wf_state("
            "id,contract_version,state,updated_ts) VALUES(1,?,'PENDING',?)",
            (HISTORICAL_WF_CONTRACT_VERSION, time.time()),
        )
        for table in HISTORICAL_CRITICAL_TABLES:
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable historical WF evidence row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable historical WF evidence row'); END""")


def _state(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT * FROM g1s_historical_wf_state WHERE id=1").fetchone()
    return dict(row) if row is not None else {}


def _set_state(runtime: ShortHorizonRuntime, **updates: Any) -> None:
    allowed = {
        "contract_version", "state", "last_started_ts", "last_success_ts",
        "last_error", "source_set_sha256", "source_count", "run_count",
        "provisional_count", "updated_ts",
    }
    updates = {key: value for key, value in updates.items() if key in allowed}
    updates["updated_ts"] = time.time()
    if not updates:
        return
    assignments = ",".join(f"{key}=?" for key in updates)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"UPDATE g1s_historical_wf_state SET {assignments} WHERE id=1",
            tuple(updates.values()),
        )


def _canonical_bar(start_ts: float, open_: Any, high: Any, low: Any,
                   close: Any, volume: Any) -> dict[str, Any] | None:
    values = [_finite(open_), _finite(high), _finite(low), _finite(close)]
    if any(value is None or value <= 0 for value in values):
        return None
    vol = _finite(volume)
    return {
        "bar_start_ts": float(start_ts),
        "bar_end_ts": float(start_ts + BAR_SECONDS),
        "open": float(values[0]), "high": float(values[1]),
        "low": float(values[2]), "close": float(values[3]),
        "volume": None if vol is None else float(vol),
    }


def _frame_to_bars(frame: Any) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    if frame is None or len(frame) == 0:
        return bars
    for ts, row in frame.iterrows():
        try:
            start_ts = float(ts.timestamp())
            bar = _canonical_bar(
                start_ts, row["Open"], row["High"], row["Low"],
                row["Close"], row.get("Volume"),
            )
        except Exception:
            bar = None
        if bar is not None:
            bars.append(bar)
    dedup = {float(bar["bar_end_ts"]): bar for bar in bars}
    return [dedup[key] for key in sorted(dedup)]


def _store_source(runtime: ShortHorizonRuntime, instrument: str, ticker: str,
                  bars: list[dict[str, Any]], fetched_ts: float) -> dict[str, Any]:
    if len(bars) < MIN_SOURCE_BARS:
        raise RuntimeError(f"{instrument}: only {len(bars)} usable historical bars")
    raw = _json(bars)
    digest = _sha(raw)
    first_ts = float(bars[0]["bar_end_ts"]); last_ts = float(bars[-1]["bar_end_ts"])
    semantics = {
        "provider": SOURCE_PROVIDER,
        "ticker": ticker,
        "interval": SOURCE_INTERVAL,
        "requested_period": SOURCE_PERIOD,
        "bar_timestamp_semantics": "provider timestamp is interval start; T0 is bar_start+300s",
        "completed_bars_only_for_features": True,
        "historical_price_is_yahoo_series": True,
        "exact_live_broker_series": not bool(
            INSTRUMENTS[instrument].swissquote_pair or INSTRUMENTS[instrument].tradingview_symbol),
        "returns_not_absolute_price_used_by_model": True,
        "option_history_used": False,
        "synthetic_option_history": False,
    }
    source_id = "g1s-hist-src-" + _sha(
        f"{HISTORICAL_WF_CONTRACT_VERSION}|{instrument}|{ticker}|{digest}"
    )[:28]
    compressed = gzip.compress(raw.encode("utf-8"), compresslevel=6)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_historical_sources("
            "source_id,contract_version,instrument,ticker,provider,interval,requested_period,"
            "fetched_ts,first_bar_end_ts,last_bar_end_ts,bar_count,calendar_span_days,"
            "source_sha256,bars_gzip,source_semantics_json,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, HISTORICAL_WF_CONTRACT_VERSION, instrument, ticker,
             SOURCE_PROVIDER, SOURCE_INTERVAL, SOURCE_PERIOD, float(fetched_ts),
             first_ts, last_ts, len(bars), (last_ts-first_ts)/86400.0,
             digest, sqlite3.Binary(compressed), _json(semantics), time.time()),
        )
    return {
        "source_id": source_id, "instrument": instrument, "ticker": ticker,
        "bars": bars, "source_sha256": digest, "bar_count": len(bars),
        "first_bar_end_ts": first_ts, "last_bar_end_ts": last_ts,
        "calendar_span_days": (last_ts-first_ts)/86400.0,
        "source_semantics": semantics,
    }


def _load_source_bars(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = gzip.decompress(bytes(row["bars_gzip"])).decode("utf-8")
    if _sha(raw) != str(row["source_sha256"]):
        raise RuntimeError(f"historical source hash mismatch: {row['source_id']}")
    value = json.loads(raw)
    if not isinstance(value, list):
        raise RuntimeError("historical source payload is not a list")
    return value


def _latest_stored_source(runtime: ShortHorizonRuntime, instrument: str) -> dict[str, Any] | None:
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT * FROM g1s_historical_sources WHERE contract_version=? AND instrument=? "
            "ORDER BY created_ts DESC LIMIT 1",
            (HISTORICAL_WF_CONTRACT_VERSION, instrument),
        ).fetchone()
    if row is None:
        return None
    item = dict(row); item["bars"] = _load_source_bars(item)
    item["source_semantics"] = json.loads(item.get("source_semantics_json") or "{}")
    return item


def _fetch_sources(runtime: ShortHorizonRuntime) -> tuple[list[dict[str, Any]], dict[str, str]]:
    import yfinance as yf

    fetched_ts = time.time()
    sources: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for code, inst in INSTRUMENTS.items():
        try:
            frame = yf.Ticker(inst.yahoo).history(
                period=SOURCE_PERIOD, interval=SOURCE_INTERVAL,
                auto_adjust=False, actions=False,
            )
            bars = _frame_to_bars(frame)
            sources.append(_store_source(runtime, code, inst.yahoo, bars, fetched_ts))
        except Exception as exc:
            errors[code] = f"{type(exc).__name__}: {str(exc)[:300]}"
            stored = _latest_stored_source(runtime, code)
            if stored is None:
                raise RuntimeError(f"no real historical source for {code}: {errors[code]}") from exc
            sources.append(stored)
    return sources, errors


def _anchor_index(times: list[float], index: int, seconds: float) -> int | None:
    target = times[index] - seconds
    pos = bisect.bisect_right(times, target + 1e-6, hi=index) - 1
    if pos < 0:
        return None
    # Do not silently turn an overnight/session gap into a 5/15/60m feature.
    if target - times[pos] > BAR_SECONDS * 1.5:
        return None
    return pos


def _target_index(times: list[float], index: int, horizon_minutes: int) -> int | None:
    target = times[index] + horizon_minutes * 60.0
    pos = bisect.bisect_left(times, target - 1e-6, lo=index + 1)
    if pos >= len(times):
        return None
    if times[pos] - target > BAR_SECONDS * 1.5:
        return None
    return pos


def _build_horizon_rows(source: dict[str, Any], horizon_minutes: int) -> list[dict[str, Any]]:
    bars = source["bars"]
    times = [float(bar["bar_end_ts"]) for bar in bars]
    closes = np.asarray([float(bar["close"]) for bar in bars], dtype=float)
    highs = np.asarray([float(bar["high"]) for bar in bars], dtype=float)
    lows = np.asarray([float(bar["low"]) for bar in bars], dtype=float)
    log_step = np.zeros(len(bars), dtype=float)
    log_step[1:] = np.log(closes[1:] / closes[:-1])
    rows: list[dict[str, Any]] = []
    for index in range(12, len(bars) - 1):
        i5 = _anchor_index(times, index, 5*60.0)
        i15 = _anchor_index(times, index, 15*60.0)
        i60 = _anchor_index(times, index, 60*60.0)
        target_index = _target_index(times, index, horizon_minutes)
        if None in (i5, i15, i60, target_index):
            continue
        assert i5 is not None and i15 is not None and i60 is not None and target_index is not None
        # For RV, require a genuinely continuous 60m window of completed 5m bars.
        window_start = i60 + 1
        if index-window_start+1 < 11:
            continue
        rv15_values = log_step[i15+1:index+1]
        rv60_values = log_step[i60+1:index+1]
        if len(rv15_values) < 2 or len(rv60_values) < 10:
            continue
        current = closes[index]
        future = closes[target_index]
        if current <= 0 or future <= 0:
            continue
        target_return = float(math.log(future/current))
        path_slice = closes[index+1:target_index+1]
        if len(path_slice) == 0:
            continue
        path_returns = np.log(path_slice/current)
        features = {
            "ret_5m": float(math.log(current/closes[i5])),
            "ret_15m": float(math.log(current/closes[i15])),
            "ret_60m": float(math.log(current/closes[i60])),
            "realized_vol_15m": float(math.sqrt(float(np.sum(rv15_values*rv15_values)))),
            "realized_vol_60m": float(math.sqrt(float(np.sum(rv60_values*rv60_values)))),
        }
        row = {
            "instrument": str(source["instrument"]),
            "ticker": str(source["ticker"]),
            "captured_ts": float(times[index]),
            "target_ts": float(times[target_index]),
            "horizon_minutes": int(horizon_minutes),
            "features": features,
            "terminal_log_return": target_return,
            "direction_label": "UP" if target_return > 0 else "DOWN" if target_return < 0 else "FLAT",
            "mfe_log_return": float(np.max(path_returns)),
            "mae_log_return": float(np.min(path_returns)),
            "source_id": str(source["source_id"]),
            "evidence_label": HISTORICAL_EVIDENCE_LABEL,
        }
        rows.append(row)
    return rows


def _vector(row: dict[str, Any]) -> list[float]:
    values = [float(row["features"][name]) for name in FEATURE_NAMES]
    values.extend(1.0 if row["instrument"] == code else 0.0 for code in INSTRUMENT_DUMMIES)
    return values


def _dependency_key(row: dict[str, Any]) -> str:
    horizon = int(row["horizon_minutes"])
    bucket = int(float(row["captured_ts"]) // (horizon * 60.0))
    return f"{row['instrument']}|{horizon}|{bucket}"


def _weights(rows: list[dict[str, Any]]) -> tuple[np.ndarray, int]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[_dependency_key(row)].append(index)
    out = np.zeros(len(rows), dtype=float)
    for members in groups.values():
        per = 1.0 / len(members)
        for index in members:
            out[index] = per
    return out, len(groups)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    den = float(np.sum(weights))
    return float(np.sum(values*weights)/den) if den > 0 else float("nan")


def _fit_standardization(x: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    den = max(float(weights.sum()), 1e-12)
    mean = (weights[:, None]*x).sum(axis=0)/den
    var = (weights[:, None]*(x-mean)**2).sum(axis=0)/den
    std = np.sqrt(np.maximum(var, 0.0)); std[std < 1e-12] = 1.0
    return mean, std


def _fit_logistic(x: np.ndarray, y: np.ndarray, weights: np.ndarray,
                  l2: float = PROB_L2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean, std = _fit_standardization(x, weights)
    z = (x-mean)/std
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    reg = np.eye(design.shape[1], dtype=float)*float(l2); reg[0,0] = 0.0
    for _ in range(80):
        p = 1.0/(1.0+np.exp(-np.clip(design@beta, -35.0, 35.0)))
        variance = np.maximum(p*(1.0-p), 1e-6)
        grad = design.T@(weights*(p-y))+reg@beta
        hess = design.T@((weights*variance)[:,None]*design)+reg
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hess)@grad
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return mean, std, beta


def _fit_ridge(x: np.ndarray, y: np.ndarray, weights: np.ndarray,
               l2: float = RIDGE_L2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean, std = _fit_standardization(x, weights)
    z = (x-mean)/std
    design = np.column_stack([np.ones(len(z)), z])
    weighted = design*weights[:,None]
    reg = np.eye(design.shape[1], dtype=float)*float(l2); reg[0,0] = 0.0
    beta = np.linalg.pinv(design.T@weighted+reg)@(design.T@(weights*y))
    return mean, std, beta


def _predict_linear(x: np.ndarray, mean: np.ndarray, std: np.ndarray,
                    beta: np.ndarray, *, probability: bool) -> np.ndarray:
    z = (x-mean)/np.where(std < 1e-12, 1.0, std)
    raw = beta[0]+z@beta[1:]
    if probability:
        return 1.0/(1.0+np.exp(-np.clip(raw, -35.0, 35.0)))
    return raw


def _prob_metrics(y: np.ndarray, p: np.ndarray, weights: np.ndarray) -> dict[str, float | None]:
    if len(y) == 0:
        return {"brier": None, "logloss": None, "ece": None,
                "balanced_accuracy": None, "roc_auc": None}
    p = np.clip(p.astype(float), 1e-6, 1.0-1e-6)
    brier = _weighted_mean((p-y)**2, weights)
    logloss = _weighted_mean(-(y*np.log(p)+(1.0-y)*np.log(1.0-p)), weights)
    ece = 0.0; total = float(weights.sum())
    for lo in np.linspace(0.0, 0.9, 10):
        hi = lo+0.1
        mask = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        if not np.any(mask):
            continue
        w = weights[mask]; den = float(w.sum())
        if den <= 0:
            continue
        ece += (den/total)*abs(_weighted_mean(p[mask], w)-_weighted_mean(y[mask], w))
    predicted = p >= 0.5
    pos = y >= 0.5; neg = ~pos
    tpr = (_weighted_mean(predicted[pos].astype(float), weights[pos]) if np.any(pos) else None)
    tnr = (_weighted_mean((~predicted[neg]).astype(float), weights[neg]) if np.any(neg) else None)
    balanced = ((tpr+tnr)/2.0 if tpr is not None and tnr is not None else None)
    auc = _weighted_auc(y, p, weights)
    return {"brier": brier, "logloss": logloss, "ece": float(ece),
            "balanced_accuracy": balanced, "roc_auc": auc}


def _weighted_auc(y: np.ndarray, score: np.ndarray, weights: np.ndarray) -> float | None:
    pos_total = float(weights[y >= 0.5].sum()); neg_total = float(weights[y < 0.5].sum())
    if pos_total <= 0 or neg_total <= 0:
        return None
    order = np.argsort(score, kind="mergesort")
    y_s = y[order]; s_s = score[order]; w_s = weights[order]
    cumulative_neg = 0.0; concordant = 0.0; index = 0
    while index < len(order):
        end = index+1
        while end < len(order) and s_s[end] == s_s[index]:
            end += 1
        group_y = y_s[index:end]; group_w = w_s[index:end]
        group_pos = float(group_w[group_y >= 0.5].sum())
        group_neg = float(group_w[group_y < 0.5].sum())
        concordant += group_pos*cumulative_neg + 0.5*group_pos*group_neg
        cumulative_neg += group_neg
        index = end
    return float(concordant/(pos_total*neg_total))


def _return_metrics(y: np.ndarray, pred: np.ndarray, weights: np.ndarray) -> dict[str, float | None]:
    if len(y) == 0:
        return {"mae": None, "rmse": None}
    err = pred-y
    return {
        "mae": _weighted_mean(np.abs(err), weights),
        "rmse": math.sqrt(max(0.0, _weighted_mean(err*err, weights))),
    }


def _conditional_probability(train_rows: list[dict[str, Any]], train_y: np.ndarray,
                             train_weights: np.ndarray, feature: str) -> tuple[float, float]:
    values = np.asarray([float(row["features"][feature]) for row in train_rows])
    base = _clip_probability(_weighted_mean(train_y, train_weights))
    rates = []
    for positive in (False, True):
        mask = values > 0 if positive else values <= 0
        if not np.any(mask) or float(train_weights[mask].sum()) <= 0:
            rates.append(base)
        else:
            # Two pseudo-observations at base rate prevent 0/1 log-loss explosions.
            den = float(train_weights[mask].sum())
            observed = float(np.sum(train_weights[mask]*train_y[mask]))
            rates.append(_clip_probability((observed+2.0*base)/(den+2.0)))
    return rates[0], rates[1]


def _historical_folds(rows: list[dict[str, Any]], horizon_minutes: int) -> list[dict[str, Any]]:
    times = sorted({float(row["captured_ts"]) for row in rows})
    if len(times) < 100:
        return []
    initial = max(1, int(len(times)*INITIAL_TRAIN_FRACTION))
    remaining = len(times)-initial
    block = max(1, remaining//FOLD_COUNT)
    folds: list[dict[str, Any]] = []
    for fold_index in range(FOLD_COUNT):
        start_index = initial+fold_index*block
        if start_index >= len(times):
            break
        end_index = len(times) if fold_index == FOLD_COUNT-1 else min(len(times), start_index+block)
        test_start = times[start_index]; test_end = times[end_index-1]
        purge_boundary = test_start-EMBARGO_SECONDS
        train = [row for row in rows if float(row["target_ts"]) < purge_boundary-1e-9]
        test = [row for row in rows if test_start <= float(row["captured_ts"]) <= test_end]
        if len(train) < 100 or len(test) < 20:
            continue
        folds.append({
            "fold_index": fold_index+1,
            "train": train, "test": test,
            "test_start_ts": test_start, "test_end_ts": test_end,
            "purge_boundary_ts": purge_boundary,
            "purge_seconds": horizon_minutes*60.0,
            "embargo_seconds": EMBARGO_SECONDS,
            "train_target_max_ts": max(float(row["target_ts"]) for row in train),
        })
    return folds


def _evaluate_probability(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_p: list[float] = []; all_w: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    fold_reports = []
    for fold in folds:
        train = fold["train"]; test = fold["test"]
        x_train = np.asarray([_vector(row) for row in train], dtype=float)
        y_train = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
        w_train, train_eff = _weights(train)
        x_test = np.asarray([_vector(row) for row in test], dtype=float)
        y_test = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in test])
        w_test, test_eff = _weights(test)
        mean, std, beta = _fit_logistic(x_train, y_train, w_train)
        pred = _predict_linear(x_test, mean, std, beta, probability=True)
        base_rate = _clip_probability(_weighted_mean(y_train, w_train))
        persist_neg, persist_pos = _conditional_probability(train, y_train, w_train, "ret_5m")
        momentum_neg, momentum_pos = _conditional_probability(train, y_train, w_train, "ret_15m")
        test_ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in test])
        test_ret15 = np.asarray([float(row["features"]["ret_15m"]) for row in test])
        baselines = {
            "constant_0_5": np.full(len(test), 0.5),
            "causal_base_rate": np.full(len(test), base_rate),
            "ret5_persistence": np.where(test_ret5 > 0, persist_pos, persist_neg),
            "ret15_momentum": np.where(test_ret15 > 0, momentum_pos, momentum_neg),
        }
        model_metrics = _prob_metrics(y_test, pred, w_test)
        baseline_metrics = {name: _prob_metrics(y_test, values, w_test)
                            for name, values in baselines.items()}
        fold_reports.append({
            "fold_index": fold["fold_index"],
            "train_raw_n": len(train), "train_effective_n": train_eff,
            "test_raw_n": len(test), "test_effective_n": test_eff,
            "test_start_ts": fold["test_start_ts"], "test_end_ts": fold["test_end_ts"],
            "train_target_max_ts": fold["train_target_max_ts"],
            "purge_boundary_ts": fold["purge_boundary_ts"],
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "model": model_metrics, "baselines": baseline_metrics,
        })
        all_y.extend(y_test.tolist()); all_p.extend(pred.tolist()); all_w.extend(w_test.tolist())
        for name, values in baselines.items():
            baseline_values[name].extend(values.tolist())
    if not all_y:
        return {"fold_count": 0, "model": {}, "baselines": {}, "folds": fold_reports}
    y = np.asarray(all_y); p = np.asarray(all_p); weights = np.asarray(all_w)
    model_metrics = _prob_metrics(y, p, weights)
    baseline_metrics = {name: _prob_metrics(y, np.asarray(values), weights)
                        for name, values in baseline_values.items()}
    return {"fold_count": len(fold_reports), "model": model_metrics,
            "baselines": baseline_metrics, "folds": fold_reports,
            "test_raw_n": len(all_y), "test_effective_weight": float(weights.sum())}


def _evaluate_return(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_p: list[float] = []; all_w: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    fold_reports = []
    for fold in folds:
        train = fold["train"]; test = fold["test"]
        x_train = np.asarray([_vector(row) for row in train], dtype=float)
        y_train = np.asarray([float(row["terminal_log_return"]) for row in train])
        w_train, train_eff = _weights(train)
        x_test = np.asarray([_vector(row) for row in test], dtype=float)
        y_test = np.asarray([float(row["terminal_log_return"]) for row in test])
        w_test, test_eff = _weights(test)
        mean, std, beta = _fit_ridge(x_train, y_train, w_train)
        pred = _predict_linear(x_test, mean, std, beta, probability=False)
        hist_mean = _weighted_mean(y_train, w_train)
        ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in test])
        ret15 = np.asarray([float(row["features"]["ret_15m"]) for row in test])
        baselines = {
            "zero_return": np.zeros(len(test)),
            "causal_historical_mean": np.full(len(test), hist_mean),
            "ret5_persistence": ret5,
            "ret15_momentum": ret15,
        }
        model_metrics = _return_metrics(y_test, pred, w_test)
        baseline_metrics = {name: _return_metrics(y_test, values, w_test)
                            for name, values in baselines.items()}
        fold_reports.append({
            "fold_index": fold["fold_index"],
            "train_raw_n": len(train), "train_effective_n": train_eff,
            "test_raw_n": len(test), "test_effective_n": test_eff,
            "test_start_ts": fold["test_start_ts"], "test_end_ts": fold["test_end_ts"],
            "train_target_max_ts": fold["train_target_max_ts"],
            "purge_boundary_ts": fold["purge_boundary_ts"],
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "model": model_metrics, "baselines": baseline_metrics,
        })
        all_y.extend(y_test.tolist()); all_p.extend(pred.tolist()); all_w.extend(w_test.tolist())
        for name, values in baselines.items():
            baseline_values[name].extend(values.tolist())
    if not all_y:
        return {"fold_count": 0, "model": {}, "baselines": {}, "folds": fold_reports}
    y = np.asarray(all_y); p = np.asarray(all_p); weights = np.asarray(all_w)
    model_metrics = _return_metrics(y, p, weights)
    baseline_metrics = {name: _return_metrics(y, np.asarray(values), weights)
                        for name, values in baseline_values.items()}
    return {"fold_count": len(fold_reports), "model": model_metrics,
            "baselines": baseline_metrics, "folds": fold_reports,
            "test_raw_n": len(all_y), "test_effective_weight": float(weights.sum())}


def _beats_probability_baselines(evaluation: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    model = evaluation.get("model") or {}; baselines = evaluation.get("baselines") or {}
    if not model or not baselines:
        return False, {"reason": "NO_EVALUATION"}
    best_brier_name, best_brier = min(
        ((name, float(metrics["brier"])) for name, metrics in baselines.items()
         if metrics.get("brier") is not None), key=lambda item: item[1])
    best_log_name, best_log = min(
        ((name, float(metrics["logloss"])) for name, metrics in baselines.items()
         if metrics.get("logloss") is not None), key=lambda item: item[1])
    margin = MIN_PROVISIONAL_RELATIVE_IMPROVEMENT
    passed = (
        float(model["brier"]) < best_brier*(1.0-margin)
        and float(model["logloss"]) < best_log*(1.0-margin)
    )
    return passed, {
        "required_relative_improvement": margin,
        "best_brier_baseline": best_brier_name, "best_brier": best_brier,
        "best_logloss_baseline": best_log_name, "best_logloss": best_log,
        "model_brier": model.get("brier"), "model_logloss": model.get("logloss"),
    }


def _beats_return_baselines(evaluation: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    model = evaluation.get("model") or {}; baselines = evaluation.get("baselines") or {}
    if not model or not baselines:
        return False, {"reason": "NO_EVALUATION"}
    best_mae_name, best_mae = min(
        ((name, float(metrics["mae"])) for name, metrics in baselines.items()
         if metrics.get("mae") is not None), key=lambda item: item[1])
    best_rmse_name, best_rmse = min(
        ((name, float(metrics["rmse"])) for name, metrics in baselines.items()
         if metrics.get("rmse") is not None), key=lambda item: item[1])
    margin = MIN_PROVISIONAL_RELATIVE_IMPROVEMENT
    passed = (
        float(model["mae"]) < best_mae*(1.0-margin)
        and float(model["rmse"]) < best_rmse*(1.0-margin)
    )
    return passed, {
        "required_relative_improvement": margin,
        "best_mae_baseline": best_mae_name, "best_mae": best_mae,
        "best_rmse_baseline": best_rmse_name, "best_rmse": best_rmse,
        "model_mae": model.get("mae"), "model_rmse": model.get("rmse"),
    }


def _fit_full_artifact(rows: list[dict[str, Any]], *, probability: bool) -> dict[str, Any]:
    x = np.asarray([_vector(row) for row in rows], dtype=float)
    weights, effective = _weights(rows)
    if probability:
        y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in rows])
        mean, std, beta = _fit_logistic(x, y, weights)
    else:
        y = np.asarray([float(row["terminal_log_return"]) for row in rows])
        mean, std, beta = _fit_ridge(x, y, weights)
    return {
        "feature_mean": [float(v) for v in mean],
        "feature_std": [float(v) for v in std],
        "intercept_and_coefficients": [float(v) for v in beta],
        "feature_names": list(MODEL_FEATURE_NAMES),
        "dependency_group_total_weight_one": True,
        "raw_n": len(rows), "effective_n": int(effective),
    }


def _register_live_cohort(runtime: ShortHorizonRuntime, *, target: str, horizon: int,
                          model_family: str, model_id: str,
                          training_cutoff_ts: float, frozen_at: float) -> str:
    _ensure_champion_tables(runtime)
    oos_start = max(float(frozen_at), float(training_cutoff_ts)+1e-6)
    raw = (f"{HISTORICAL_WF_CONTRACT_VERSION}|{target}|{horizon}|{HISTORICAL_FEATURE_SET}|"
           f"{model_family}|{model_id}|{oos_start:.6f}")
    cohort_id = "g1s-hist-live-" + _sha(raw)[:26]
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_validation_cohorts("
            "validation_cohort_id,target,horizon_minutes,feature_set,model_family,"
            "champion_model_id,frozen_at,training_cutoff_ts,oos_start_ts,source,status,"
            "auto_promotion,production_authority,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?, 'LIVE_VALIDATING',0,0,?)",
            (cohort_id, target, int(horizon), HISTORICAL_FEATURE_SET, model_family,
             model_id, float(frozen_at), float(training_cutoff_ts), oos_start,
             HISTORICAL_EVIDENCE_LABEL, time.time()),
        )
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_champion_progress("
            "validation_cohort_id,contract_version,evidence_maturity,status,updated_ts) "
            "VALUES(?,?,'INSUFFICIENT','LIVE_VALIDATING',?)",
            (cohort_id, CHAMPION_PROGRESS_VERSION, time.time()),
        )
    return cohort_id


def _insert_model(runtime: ShortHorizonRuntime, *, target: str, horizon: int,
                  rows: list[dict[str, Any]], source_set_sha: str,
                  run_id: str, evaluation: dict[str, Any], gate: dict[str, Any],
                  created_ts: float) -> str:
    probability = target == DIRECTION_TARGET
    params = _fit_full_artifact(rows, probability=probability)
    training_cutoff = max(float(row["target_ts"]) for row in rows)
    diagnostics = {
        "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
        "feature_contract_version": HISTORICAL_FEATURE_CONTRACT,
        "evidence_label": HISTORICAL_EVIDENCE_LABEL,
        "model_mode": "PROVISIONAL_LEARNED",
        "historical_wf_run_id": run_id,
        "source_set_sha256": source_set_sha,
        "historical_oos_metrics": evaluation.get("model"),
        "historical_baselines": evaluation.get("baselines"),
        "historical_selection_gate": gate,
        "live_prospective_oos_validated": False,
        "historical_fold_outcomes_count_as_live_oos": False,
        "historical_option_features_used": False,
        "synthetic_option_history": False,
        "production_authority": False,
        "auto_promotion": False,
    }
    family = HISTORICAL_PROB_MODEL_FAMILY if probability else HISTORICAL_RETURN_MODEL_FAMILY
    artifact = {
        "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
        "target": target, "horizon_minutes": int(horizon),
        "feature_set": HISTORICAL_FEATURE_SET, "model_family": family,
        "training_cutoff_ts": training_cutoff,
        "parameters": params, "diagnostics": diagnostics,
    }
    artifact_sha = _sha(_json(artifact))
    model_id = ("g1s-hist-prob-" if probability else "g1s-hist-ret-") + artifact_sha[:25]
    weights, effective = _weights(rows)
    trading_days = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"]))) for row in rows})
    if probability:
        positive = sum(row["direction_label"] == "UP" for row in rows)
        negative = sum(row["direction_label"] == "DOWN" for row in rows)
        with runtime._lock, runtime._conn:
            runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_models(model_id,model_family,horizon_minutes,feature_set,"
                "training_cutoff_ts,raw_n,effective_n,positive_n,negative_n,training_days,"
                "parameters_json,artifact_sha256,diagnostics_json,authority,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                (model_id, family, int(horizon), HISTORICAL_FEATURE_SET, training_cutoff,
                 len(rows), float(effective), int(positive), int(negative), trading_days,
                 _json(params), artifact_sha, _json(diagnostics), float(created_ts)),
            )
    else:
        with runtime._lock, runtime._conn:
            runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_return_models(model_id,model_family,horizon_minutes,"
                "feature_set,training_cutoff_ts,raw_n,effective_n,training_days,parameters_json,"
                "diagnostics_json,artifact_sha256,authority,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                (model_id, family, int(horizon), HISTORICAL_FEATURE_SET, training_cutoff,
                 len(rows), float(effective), trading_days, _json(params), _json(diagnostics),
                 artifact_sha, float(created_ts)),
            )
    _register_live_cohort(
        runtime, target=target, horizon=horizon, model_family=family, model_id=model_id,
        training_cutoff_ts=training_cutoff, frozen_at=float(created_ts),
    )
    return model_id


def _materialize_run(runtime: ShortHorizonRuntime, *, target: str, horizon: int,
                     rows: list[dict[str, Any]], source_set_sha: str,
                     source_summary: list[dict[str, Any]], fetch_errors: dict[str, str]) -> dict[str, Any]:
    clean_rows = [row for row in rows if row["direction_label"] != "FLAT"] if target == DIRECTION_TARGET else rows
    weights, effective = _weights(clean_rows)
    positive = sum(float(row["terminal_log_return"]) > 0 for row in clean_rows)
    negative = sum(float(row["terminal_log_return"]) < 0 for row in clean_rows)
    evaluation = (_evaluate_probability(clean_rows, horizon)
                  if target == DIRECTION_TARGET else _evaluate_return(clean_rows, horizon))
    metric_gate, comparison = (_beats_probability_baselines(evaluation)
                               if target == DIRECTION_TARGET else _beats_return_baselines(evaluation))
    sample_gate = len(clean_rows) >= MIN_HISTORICAL_RAW and effective >= MIN_HISTORICAL_EFFECTIVE
    fold_gate = int(evaluation.get("fold_count") or 0) >= FOLD_COUNT
    purge_gate = bool(evaluation.get("folds")) and all(
        bool(fold.get("purge_embargo_valid")) for fold in evaluation.get("folds") or [])
    winner = bool(metric_gate and sample_gate and fold_gate and purge_gate)
    family = HISTORICAL_PROB_MODEL_FAMILY if target == DIRECTION_TARGET else HISTORICAL_RETURN_MODEL_FAMILY
    run_key = (f"{HISTORICAL_WF_CONTRACT_VERSION}|{source_set_sha}|{target}|{horizon}|"
               f"{HISTORICAL_FEATURE_SET}|{family}")
    run_id = "g1s-hist-wf-" + _sha(run_key)[:28]
    created_ts = time.time()
    provisional_model_id = None
    if winner:
        provisional_model_id = _insert_model(
            runtime, target=target, horizon=horizon, rows=clean_rows,
            source_set_sha=source_set_sha, run_id=run_id,
            evaluation=evaluation, gate=comparison, created_ts=created_ts,
        )
    verdict = "PROVISIONAL_LEARNED" if winner else "HISTORICAL_BASELINE_NOT_BEATEN"
    artifact = {
        "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
        "evidence_label": HISTORICAL_EVIDENCE_LABEL,
        "live_validation_label": LIVE_EVIDENCE_LABEL,
        "target": target, "horizon_minutes": int(horizon),
        "feature_contract_version": HISTORICAL_FEATURE_CONTRACT,
        "feature_set": HISTORICAL_FEATURE_SET,
        "feature_names": list(MODEL_FEATURE_NAMES),
        "model_family": family,
        "source_set_sha256": source_set_sha,
        "source_summary": source_summary,
        "source_fetch_errors_using_stored_real_fallback": fetch_errors,
        "historical_option_context": {
            "status": "UNAVAILABLE_FOR_LONG_HISTORICAL_WINDOW",
            "used": False, "synthetic_fill": False,
            "reason": "no timestamped option-chain archive spanning the 60d bar window",
        },
        "walk_forward": {
            "expanding_chronological": True, "shuffle": False,
            "fold_count": int(evaluation.get("fold_count") or 0),
            "purge_by_target_overlap": True, "embargo_seconds": EMBARGO_SECONDS,
            "dependency_group_total_weight_one": True,
            "train_only_standardization": True,
        },
        "sample": {
            "raw_n": len(clean_rows), "effective_n": int(effective),
            "positive_n": int(positive), "negative_n": int(negative),
            "minimum_raw_n": MIN_HISTORICAL_RAW,
            "minimum_effective_n": MIN_HISTORICAL_EFFECTIVE,
        },
        "evaluation": evaluation,
        "selection_gate": {
            **comparison, "metric_gate": bool(metric_gate), "sample_gate": bool(sample_gate),
            "fold_gate": bool(fold_gate), "purge_embargo_gate": bool(purge_gate),
            "historical_winner": winner,
        },
        "verdict": verdict,
        "provisional_model_id": provisional_model_id,
        "historical_edge_claim_scope": "HISTORICAL_ONLY" if winner else "NONE",
        "historical_fold_outcomes_count_as_live_oos": False,
        "live_oos_validated": False,
        "auto_promotion": False, "production_authority": False,
    }
    artifact_raw = _json(artifact); artifact_sha = _sha(artifact_raw)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_historical_wf_runs("
            "run_id,contract_version,evidence_label,source_set_sha256,target,horizon_minutes,"
            "feature_set,model_family,fold_count,raw_n,effective_n,positive_n,negative_n,"
            "historical_winner,provisional_model_id,verdict,artifact_json,artifact_sha256,"
            "production_authority,auto_promotion,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)",
            (run_id, HISTORICAL_WF_CONTRACT_VERSION, HISTORICAL_EVIDENCE_LABEL,
             source_set_sha, target, int(horizon), HISTORICAL_FEATURE_SET, family,
             int(evaluation.get("fold_count") or 0), len(clean_rows), int(effective),
             int(positive), int(negative), int(winner), provisional_model_id, verdict,
             artifact_raw, artifact_sha, created_ts),
        )
    return {"run_id": run_id, "target": target, "horizon_minutes": int(horizon),
            "verdict": verdict, "historical_winner": winner,
            "provisional_model_id": provisional_model_id,
            "raw_n": len(clean_rows), "effective_n": int(effective)}


def _run_once(runtime: ShortHorizonRuntime, *, force: bool = False) -> dict[str, Any]:
    _ensure_tables(runtime)
    state = _state(runtime)
    if (not force and state.get("contract_version") == HISTORICAL_WF_CONTRACT_VERSION
            and state.get("state") == "COMPLETE" and int(state.get("run_count") or 0) >= 2*len(HORIZONS)):
        return {"refreshed": False, "reason": "ALREADY_MATERIALIZED",
                "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
                "run_count": int(state.get("run_count") or 0),
                "provisional_count": int(state.get("provisional_count") or 0)}
    started = time.time()
    _set_state(runtime, contract_version=HISTORICAL_WF_CONTRACT_VERSION,
               state="RUNNING", last_started_ts=started, last_error=None)
    try:
        sources, fetch_errors = _fetch_sources(runtime)
        source_set_sha = _sha(_json(sorted(
            (source["instrument"], source["source_id"], source["source_sha256"])
            for source in sources)))
        source_summary = [{
            "instrument": source["instrument"], "ticker": source["ticker"],
            "source_id": source["source_id"], "bar_count": source["bar_count"],
            "calendar_span_days": round(float(source["calendar_span_days"]), 3),
            "first_bar_end_ts": source["first_bar_end_ts"],
            "last_bar_end_ts": source["last_bar_end_ts"],
        } for source in sources]
        rows_by_horizon: dict[int, list[dict[str, Any]]] = {int(h): [] for h in HORIZONS}
        for source in sources:
            for horizon in HORIZONS:
                rows_by_horizon[int(horizon)].extend(_build_horizon_rows(source, int(horizon)))
        results = []
        for horizon in HORIZONS:
            rows = sorted(rows_by_horizon[int(horizon)],
                          key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
            results.append(_materialize_run(
                runtime, target=DIRECTION_TARGET, horizon=int(horizon), rows=rows,
                source_set_sha=source_set_sha, source_summary=source_summary,
                fetch_errors=fetch_errors,
            ))
            results.append(_materialize_run(
                runtime, target=RETURN_TARGET, horizon=int(horizon), rows=rows,
                source_set_sha=source_set_sha, source_summary=source_summary,
                fetch_errors=fetch_errors,
            ))
        provisional = sum(bool(result["historical_winner"]) for result in results)
        _set_state(runtime, state="COMPLETE", last_success_ts=time.time(), last_error=None,
                   source_set_sha256=source_set_sha, source_count=len(sources),
                   run_count=len(results), provisional_count=int(provisional))
        return {"refreshed": True, "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
                "source_set_sha256": source_set_sha, "source_count": len(sources),
                "run_count": len(results), "provisional_count": int(provisional),
                "fetch_errors": fetch_errors, "results": results,
                "duration_ms": (time.time()-started)*1000.0}
    except Exception as exc:
        _set_state(runtime, state="ERROR",
                   last_error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise


def _historical_status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    _ensure_tables(runtime)
    state = _state(runtime)
    with runtime._lock:
        source_rows = runtime._conn.execute(
            "SELECT source_id,instrument,ticker,provider,interval,requested_period,fetched_ts,"
            "first_bar_end_ts,last_bar_end_ts,bar_count,calendar_span_days,source_sha256 "
            "FROM g1s_historical_sources WHERE contract_version=? ORDER BY created_ts DESC",
            (HISTORICAL_WF_CONTRACT_VERSION,),
        ).fetchall()
        run_rows = runtime._conn.execute(
            "SELECT run_id,target,horizon_minutes,model_family,fold_count,raw_n,effective_n,"
            "positive_n,negative_n,historical_winner,provisional_model_id,verdict,created_ts "
            "FROM g1s_historical_wf_runs WHERE contract_version=? ORDER BY target,horizon_minutes",
            (HISTORICAL_WF_CONTRACT_VERSION,),
        ).fetchall()
    # Keep latest immutable source per instrument in the request-time view.
    latest_sources: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        item = dict(row); latest_sources.setdefault(str(item["instrument"]), item)
    runs = [dict(row) for row in run_rows]
    return {
        "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
        "feature_contract_version": HISTORICAL_FEATURE_CONTRACT,
        "evidence_label": HISTORICAL_EVIDENCE_LABEL,
        "live_validation_label": LIVE_EVIDENCE_LABEL,
        "state": state.get("state"),
        "last_started_ts": state.get("last_started_ts"),
        "last_success_ts": state.get("last_success_ts"),
        "last_error": state.get("last_error"),
        "source_set_sha256": state.get("source_set_sha256"),
        "source_count": int(state.get("source_count") or 0),
        "run_count": int(state.get("run_count") or 0),
        "provisional_count": int(state.get("provisional_count") or 0),
        "provider": SOURCE_PROVIDER, "interval": SOURCE_INTERVAL,
        "requested_period": SOURCE_PERIOD,
        "sources": list(latest_sources.values()), "runs": runs,
        "historical_option_features": "UNAVAILABLE_NOT_SYNTHESIZED",
        "synthetic_option_history": False,
        "expanding_chronological_walk_forward": True,
        "purge_embargo": True, "shuffle": False,
        "dependency_group_total_weight_one": True,
        "historical_fold_outcomes_count_as_live_oos": False,
        "provisional_artifact_starts_separate_live_oos": True,
        "request_time_network_fetch": False,
        "request_time_full_history_scan": False,
        "auto_promotion": False, "production_authority": False,
    }


def _live_feature_vector(row: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    values = _v2_values(row)
    missing = [name for name in FEATURE_NAMES if _finite(values.get(name)) is None]
    if missing:
        raise ValueError("historical bar-only live features unavailable: " + ",".join(missing))
    vector = [float(values[name]) for name in FEATURE_NAMES]
    vector.extend(1.0 if row["instrument"] == code else 0.0 for code in INSTRUMENT_DUMMIES)
    return vector, {name: values[name] for name in FEATURE_NAMES}


def install_g1_short_horizon_historical_wf() -> None:
    if getattr(ShortHorizonRuntime, "_historical_wf_contract", None) == HISTORICAL_WF_CONTRACT_VERSION:
        return
    previous_init = ShortHorizonRuntime.__init__
    previous_vector = ShortHorizonRuntime._feature_vector
    previous_status = ShortHorizonRuntime.status

    def runtime_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _ensure_tables(self)

    def feature_vector(row: dict[str, Any], feature_set: str):
        if feature_set == HISTORICAL_FEATURE_SET:
            return _live_feature_vector(row)
        return previous_vector(row, feature_set)

    def status(self):
        report = previous_status(self)
        report["historical_walk_forward"] = _historical_status(self)
        return report

    ShortHorizonRuntime.__init__ = runtime_init
    ShortHorizonRuntime._feature_vector = staticmethod(feature_vector)
    ShortHorizonRuntime.materialize_historical_walkforward = _run_once
    ShortHorizonRuntime.historical_walkforward_status = _historical_status
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._historical_wf_contract = HISTORICAL_WF_CONTRACT_VERSION

    _storage.CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_storage.CRITICAL_TABLES, *HISTORICAL_CRITICAL_TABLES)))
    _integration.G1S_CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_integration.G1S_CRITICAL_TABLES, *HISTORICAL_CRITICAL_TABLES)))
