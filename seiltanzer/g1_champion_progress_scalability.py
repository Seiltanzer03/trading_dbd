"""Constant-memory G1S frozen-champion progress aggregation.

The champion contract only needs aggregate evidence counters.  The original
implementation materialized every resolved OOS row in Python and then built
sets for dependency groups, dates and regimes.  On the production ledger that
can transiently allocate hundreds of MiB during startup/research refresh.

This overlay preserves the same counters and maturity gates while delegating
the aggregation to SQLite.  It changes no research labels, model fitting,
predictions, promotion rules or production authority.
"""
from __future__ import annotations

import time
from typing import Any

from .g1_short_horizon_evidence_completion import SERIOUS_OOS_REQUIRED


_PATCH_VERSION = "g1s-champion-progress-sql-aggregate-v1"
_INSTALLED = False


def _row_value(row: Any, key: str, default: Any = 0) -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _aggregate_sql(target: str, direction_target: str) -> str:
    if target == direction_target:
        eligibility = "r.direction_label!='FLAT'"
        positive = "SUM(CASE WHEN r.direction_label='UP' THEN 1 ELSE 0 END)"
        negative = "SUM(CASE WHEN r.direction_label='DOWN' THEN 1 ELSE 0 END)"
    else:
        eligibility = "r.terminal_log_return IS NOT NULL"
        positive = "SUM(CASE WHEN r.terminal_log_return>0 THEN 1 ELSE 0 END)"
        negative = "SUM(CASE WHEN r.terminal_log_return<0 THEN 1 ELSE 0 END)"
    # Dependency-key parity with ShortHorizonRuntime._dependency_key:
    # instrument | horizon | floor(captured_ts / horizon_seconds).
    dependency = (
        "g.instrument || '|' || CAST(g.horizon_minutes AS TEXT) || '|' || "
        "CAST(CAST(l.captured_ts / (g.horizon_minutes * 60.0) AS INTEGER) AS TEXT)"
    )
    return f"""
        SELECT
            COUNT(*) AS raw_n,
            COUNT(DISTINCT {dependency}) AS effective_n,
            COALESCE({positive},0) AS positive_n,
            COALESCE({negative},0) AS negative_n,
            COUNT(DISTINCT strftime('%Y-%m-%d',l.captured_ts,'unixepoch')) AS temporal_blocks,
            COUNT(DISTINCT COALESCE(g.market_regime,'UNKNOWN')) AS regimes,
            MAX(r.resolved_ts) AS latest_resolved_ts
        FROM g1s_champion_prediction_links l
        JOIN g1s_observations g USING(observation_id)
        JOIN g1s_resolutions r USING(observation_id)
        WHERE l.validation_cohort_id=? AND l.target=?
          AND {eligibility} AND g.oos_eligible=1
          AND l.prediction_created_ts<g.target_ts
    """


def _refresh_progress_bounded(runtime) -> None:
    """Refresh champion evidence using O(number of cohorts) Python memory."""
    from . import g1_short_horizon_champion_runtime as champion

    champion._ensure_tables(runtime)
    with runtime._lock:
        cohorts = [dict(row) for row in runtime._conn.execute(
            "SELECT validation_cohort_id,target FROM g1s_validation_cohorts "
            "ORDER BY created_ts,validation_cohort_id"
        ).fetchall()]

    for cohort in cohorts:
        target = str(cohort["target"])
        cohort_id = str(cohort["validation_cohort_id"])
        sql = _aggregate_sql(target, champion.DIRECTION_TARGET)
        with runtime._lock:
            aggregate = runtime._conn.execute(sql, (cohort_id, target)).fetchone()
            link_meta = runtime._conn.execute(
                "SELECT COUNT(*) n,MAX(prediction_created_ts) latest FROM "
                "g1s_champion_prediction_links WHERE validation_cohort_id=?",
                (cohort_id,),
            ).fetchone()

        raw_n = int(_row_value(aggregate, "raw_n", 0) or 0)
        effective_n = int(_row_value(aggregate, "effective_n", 0) or 0)
        positive_n = int(_row_value(aggregate, "positive_n", 0) or 0)
        negative_n = int(_row_value(aggregate, "negative_n", 0) or 0)
        temporal_blocks = int(_row_value(aggregate, "temporal_blocks", 0) or 0)
        regimes = int(_row_value(aggregate, "regimes", 0) or 0)
        latest_resolved = _row_value(aggregate, "latest_resolved_ts", None)

        observed = {
            "raw_resolved": raw_n,
            "effective_n": effective_n,
            "positive_n": positive_n,
            "negative_n": negative_n,
            "temporal_blocks": temporal_blocks,
        }
        blockers = [
            key for key, required in SERIOUS_OOS_REQUIRED.items()
            if int(observed.get(key, 0)) < int(required)
        ]
        if regimes < 2:
            blockers.append("volatility_regime_count")
        maturity = "SERIOUS_SAMPLE_GATE_MET" if not blockers else "INSUFFICIENT"

        with runtime._lock, runtime._conn:
            runtime._conn.execute("""
                INSERT INTO g1s_champion_progress(
                    validation_cohort_id,contract_version,oos_raw_n,oos_effective_n,positive_n,
                    negative_n,temporal_blocks,regimes,linked_prediction_n,latest_prediction_ts,
                    latest_resolved_ts,evidence_maturity,status,updated_ts)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(validation_cohort_id) DO UPDATE SET
                    contract_version=excluded.contract_version,
                    oos_raw_n=excluded.oos_raw_n,oos_effective_n=excluded.oos_effective_n,
                    positive_n=excluded.positive_n,negative_n=excluded.negative_n,
                    temporal_blocks=excluded.temporal_blocks,regimes=excluded.regimes,
                    linked_prediction_n=excluded.linked_prediction_n,
                    latest_prediction_ts=excluded.latest_prediction_ts,
                    latest_resolved_ts=excluded.latest_resolved_ts,
                    evidence_maturity=excluded.evidence_maturity,status=excluded.status,
                    updated_ts=excluded.updated_ts
            """, (
                cohort_id, champion.CHAMPION_PROGRESS_VERSION,
                raw_n, effective_n, positive_n, negative_n, temporal_blocks, regimes,
                int(_row_value(link_meta, "n", 0) or 0),
                _row_value(link_meta, "latest", None), latest_resolved,
                maturity, "LIVE_VALIDATING", time.time(),
            ))


def install_g1_champion_progress_scalability() -> None:
    """Install after champion runtime module exists; idempotent by version."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import g1_short_horizon_champion_runtime as champion
    if getattr(champion, "_progress_scalability_version", None) == _PATCH_VERSION:
        _INSTALLED = True
        return
    champion._refresh_progress = _refresh_progress_bounded
    champion._progress_scalability_version = _PATCH_VERSION
    _INSTALLED = True
