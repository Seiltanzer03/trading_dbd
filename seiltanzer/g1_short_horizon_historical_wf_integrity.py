"""Status integrity for versioned historical walk-forward retries.

Historical source/run artifacts are intentionally immutable. If a worker attempt
fails after writing part of a source set and a later retry fetches a different
real Yahoo snapshot, old artifacts must remain auditable but must not be mixed
into the current COMPLETE materialization view.

This presentation wrapper filters runs to the persisted current source_set hash.
It performs only an indexed bounded query over the small run registry and never
fetches market data or changes model/trading authority.
"""
from __future__ import annotations

from typing import Any

from .g1_short_horizon_historical_wf import (
    HISTORICAL_WF_CONTRACT_VERSION,
    _historical_status,
)
from .g1_short_horizon_historical_wf_memory import (
    HISTORICAL_WF_MEMORY_VERSION,
    install_g1_short_horizon_historical_wf_memory,
)
from .g1_short_horizon_runtime import ShortHorizonRuntime


HISTORICAL_WF_INTEGRITY_VERSION = "g1s-historical-wf-source-set-integrity-v1"


def _current_source_set_runs(runtime: ShortHorizonRuntime,
                             base: dict[str, Any]) -> dict[str, Any]:
    source_set = base.get("source_set_sha256")
    base["memory_contract_version"] = HISTORICAL_WF_MEMORY_VERSION
    base["horizons_materialized_sequentially"] = True
    if not source_set:
        base["source_set_integrity_version"] = HISTORICAL_WF_INTEGRITY_VERSION
        base["current_source_set_isolated"] = False
        base["current_source_set_filter_reason"] = "SOURCE_SET_NOT_FINALIZED"
        return base

    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT run_id,target,horizon_minutes,model_family,fold_count,raw_n,effective_n,"
            "positive_n,negative_n,historical_winner,provisional_model_id,verdict,created_ts "
            "FROM g1s_historical_wf_runs WHERE contract_version=? AND source_set_sha256=? "
            "ORDER BY target,horizon_minutes",
            (HISTORICAL_WF_CONTRACT_VERSION, str(source_set)),
        ).fetchall()
    runs = [dict(row) for row in rows]
    base["runs"] = runs
    base["run_count"] = len(runs)
    base["provisional_count"] = sum(bool(row.get("historical_winner")) for row in runs)
    base["source_set_integrity_version"] = HISTORICAL_WF_INTEGRITY_VERSION
    base["current_source_set_isolated"] = True
    base["current_source_set_filter_reason"] = None
    return base


def _integrity_status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    return _current_source_set_runs(runtime, _historical_status(runtime))


def install_g1_short_horizon_historical_wf_integrity() -> None:
    if getattr(ShortHorizonRuntime, "_historical_wf_integrity_version", None) == HISTORICAL_WF_INTEGRITY_VERSION:
        return

    install_g1_short_horizon_historical_wf_memory()
    previous_status = ShortHorizonRuntime.status

    def status(self):
        report = previous_status(self)
        # Replace only the historical presentation block produced by the prior
        # wrapper. Existing G.1S/champion status contracts remain byte-compatible.
        report["historical_walk_forward"] = _integrity_status(self)
        return report

    ShortHorizonRuntime.historical_walkforward_status = _integrity_status
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._historical_wf_integrity_version = HISTORICAL_WF_INTEGRITY_VERSION
