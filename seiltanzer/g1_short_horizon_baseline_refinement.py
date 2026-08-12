"""Add a simple frozen momentum baseline to every G.1S horizon report.

The purpose is not to maximize score; it is to prevent a learned model from
claiming value merely for rediscovering the sign of recent return.  Probability
strength (55/45) is fixed by contract, never tuned on the evaluation sample.
"""
from __future__ import annotations

import math

from .g1_short_horizon_runtime import ShortHorizonRuntime, _brier, _logloss, _loads


BASELINE_CONTRACT_VERSION = "g1s-fixed-momentum-baseline-v1"
REFINEMENT_VERSION = "g1s-momentum-baseline-refinement-v1"
MOMENTUM_UP_P = 0.55
MOMENTUM_DOWN_P = 0.45


def _momentum_probability(row: dict) -> float:
    features=_loads(row.get("frozen_features_json"),{})
    state=((features.get("price_state") or {}).get("g1s_intraday") or {})
    try:
        ret=float(state.get("ret_15m"))
    except (TypeError,ValueError):
        return 0.5
    if not math.isfinite(ret):
        return 0.5
    if ret>0: return MOMENTUM_UP_P
    if ret<0: return MOMENTUM_DOWN_P
    return 0.5


def install_g1_short_horizon_baseline_refinement():
    if getattr(ShortHorizonRuntime,"_baseline_refinement",None)==REFINEMENT_VERSION:
        return
    original=ShortHorizonRuntime.horizon_report
    def horizon_report(self,horizon:int):
        result=original(self,horizon)
        rows=self._resolved_eligible(horizon)
        ys=[1 if r["direction_label"]=="UP" else 0 for r in rows]
        ps=[_momentum_probability(r) for r in rows]
        result.setdefault("baselines",{})["fixed_momentum_15m"]={
            "contract_version":BASELINE_CONTRACT_VERSION,
            "p_if_positive_ret15":MOMENTUM_UP_P,
            "p_if_negative_ret15":MOMENTUM_DOWN_P,
            "brier":_brier(ps,ys),"log_loss":_logloss(ps,ys),
            "tuned_on_evaluation_sample":False,
        }
        return result
    ShortHorizonRuntime.horizon_report=horizon_report
    ShortHorizonRuntime._baseline_refinement=REFINEMENT_VERSION
