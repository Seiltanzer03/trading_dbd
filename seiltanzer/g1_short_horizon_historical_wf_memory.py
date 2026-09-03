"""Memory-bounded execution for the real-bar historical WF bootstrap.

The mathematical contract is unchanged.  The base implementation is intentionally
factored into pure source/row/evaluation helpers; this runner consumes them one
horizon at a time so production never retains all 15/30/60/120/240m row ledgers
in RAM simultaneously.
"""
from __future__ import annotations

import gc
import time

from .g1_short_horizon_historical_wf import (
    DIRECTION_TARGET,
    HISTORICAL_WF_CONTRACT_VERSION,
    RETURN_TARGET,
    _build_horizon_rows,
    _ensure_tables,
    _fetch_sources,
    _json,
    _materialize_run,
    _set_state,
    _sha,
    _state,
)
from .g1_short_horizon_runtime import HORIZONS, ShortHorizonRuntime


HISTORICAL_WF_MEMORY_VERSION = "g1s-historical-wf-memory-bounded-v1"


def _run_once_memory_bounded(runtime: ShortHorizonRuntime, *, force: bool = False):
    _ensure_tables(runtime)
    state = _state(runtime)
    expected_runs = 2 * len(HORIZONS)
    if (
        not force
        and state.get("contract_version") == HISTORICAL_WF_CONTRACT_VERSION
        and state.get("state") == "COMPLETE"
        and int(state.get("run_count") or 0) >= expected_runs
    ):
        return {
            "refreshed": False,
            "reason": "ALREADY_MATERIALIZED",
            "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
            "memory_contract_version": HISTORICAL_WF_MEMORY_VERSION,
            "run_count": int(state.get("run_count") or 0),
            "provisional_count": int(state.get("provisional_count") or 0),
        }

    started = time.time()
    from .production_resource_guard import memory_pressure_state, trim_memory_for_pressure

    pressure = memory_pressure_state()
    if pressure.get("pause_background"):
        trim_memory_for_pressure()
        return {
            "refreshed": False,
            "reason": "MEMORY_PRESSURE_YIELD",
            "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
            "memory_contract_version": HISTORICAL_WF_MEMORY_VERSION,
            "rss_mib": pressure.get("rss_mib"),
        }

    _set_state(
        runtime,
        contract_version=HISTORICAL_WF_CONTRACT_VERSION,
        state="RUNNING",
        last_started_ts=started,
        last_error=None,
    )

    try:
        sources, fetch_errors = _fetch_sources(runtime)
        source_set_sha = _sha(
            _json(
                sorted(
                    (source["instrument"], source["source_id"], source["source_sha256"])
                    for source in sources
                )
            )
        )
        source_summary = [
            {
                "instrument": source["instrument"],
                "ticker": source["ticker"],
                "source_id": source["source_id"],
                "bar_count": source["bar_count"],
                "calendar_span_days": round(float(source["calendar_span_days"]), 3),
                "first_bar_end_ts": source["first_bar_end_ts"],
                "last_bar_end_ts": source["last_bar_end_ts"],
            }
            for source in sources
        ]
        # Persist the current immutable source-set identity as soon as all real
        # sources are known. If a later horizon fails, status can distinguish
        # this attempt from older immutable retry artifacts.
        _set_state(
            runtime,
            source_set_sha256=source_set_sha,
            source_count=len(sources),
            run_count=0,
            provisional_count=0,
        )

        results = []
        for horizon in HORIZONS:
            rows = []
            for source in sources:
                rows.extend(_build_horizon_rows(source, int(horizon)))
            rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))

            results.append(
                _materialize_run(
                    runtime,
                    target=DIRECTION_TARGET,
                    horizon=int(horizon),
                    rows=rows,
                    source_set_sha=source_set_sha,
                    source_summary=source_summary,
                    fetch_errors=fetch_errors,
                )
            )
            results.append(
                _materialize_run(
                    runtime,
                    target=RETURN_TARGET,
                    horizon=int(horizon),
                    rows=rows,
                    source_set_sha=source_set_sha,
                    source_summary=source_summary,
                    fetch_errors=fetch_errors,
                )
            )
            provisional_so_far = sum(bool(item["historical_winner"]) for item in results)
            _set_state(
                runtime,
                run_count=len(results),
                provisional_count=int(provisional_so_far),
            )
            # Explicitly drop the largest per-horizon object graph before the
            # next horizon is constructed. numpy/pandas source frames are gone
            # already; source bars stay because they are shared across horizons.
            del rows
            gc.collect()
            trim_memory_for_pressure()

        provisional = sum(bool(item["historical_winner"]) for item in results)
        _set_state(
            runtime,
            state="COMPLETE",
            last_success_ts=time.time(),
            last_error=None,
            source_set_sha256=source_set_sha,
            source_count=len(sources),
            run_count=len(results),
            provisional_count=int(provisional),
        )
        return {
            "refreshed": True,
            "contract_version": HISTORICAL_WF_CONTRACT_VERSION,
            "memory_contract_version": HISTORICAL_WF_MEMORY_VERSION,
            "source_set_sha256": source_set_sha,
            "source_count": len(sources),
            "run_count": len(results),
            "provisional_count": int(provisional),
            "fetch_errors": fetch_errors,
            "results": results,
            "duration_ms": (time.time() - started) * 1000.0,
            "horizons_materialized_sequentially": True,
        }
    except Exception as exc:
        _set_state(
            runtime,
            state="ERROR",
            last_error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        raise
    finally:
        trim_memory_for_pressure()



def install_g1_short_horizon_historical_wf_memory() -> None:
    if getattr(ShortHorizonRuntime, "_historical_wf_memory_version", None) == HISTORICAL_WF_MEMORY_VERSION:
        return
    ShortHorizonRuntime.materialize_historical_walkforward = _run_once_memory_bounded
    ShortHorizonRuntime._historical_wf_memory_version = HISTORICAL_WF_MEMORY_VERSION
