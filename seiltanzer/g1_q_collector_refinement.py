"""Exact-calendar refinement for the independent G.1B.1 Q collector."""
from __future__ import annotations

from copy import deepcopy

from . import g1_q_collector as _collector

Q_COLLECTOR_REFINEMENT_VERSION = "q-independent-collector-calendar-v1"
_ORIGINAL_INSERT = _collector._insert_native_q_only


def _insert_with_exact_calendar(
    self,
    *,
    instrument: str,
    freeze_ts: float,
    source_ts: float,
    price: float,
    provenance: dict,
    option_metrics: dict,
    session_state: dict,
    regime: str,
):
    metrics = deepcopy(option_metrics)
    expiry = _collector._finite(metrics.get("expiry_ts_utc"))
    if expiry is not None and expiry > freeze_ts:
        metrics["t_years"] = (expiry - freeze_ts) / (365.0 * 86400.0)
        metrics["calendar_ttm_source"] = "expiry_ts_utc_minus_capture"
    return _ORIGINAL_INSERT(
        self,
        instrument=instrument,
        freeze_ts=freeze_ts,
        source_ts=source_ts,
        price=price,
        provenance=provenance,
        option_metrics=metrics,
        session_state=session_state,
        regime=regime,
    )


def install_g1_q_collector_refinement() -> None:
    engine = _collector._ENGINE
    if getattr(engine, "_g1_q_collector_refinement", None) == Q_COLLECTOR_REFINEMENT_VERSION:
        return
    _collector._insert_native_q_only = _insert_with_exact_calendar
    previous_status = engine.g1_q_status

    def status(self):
        result = previous_status(self)
        result["q_collector_refinement_version"] = Q_COLLECTOR_REFINEMENT_VERSION
        return result

    engine.g1_q_status = status
    engine._g1_q_collector_refinement = Q_COLLECTOR_REFINEMENT_VERSION
