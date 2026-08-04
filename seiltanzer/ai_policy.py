"""Stable public facade for the quantitative AI policy manager v2."""
from __future__ import annotations

from . import ai_policy_v2 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})


def cancellation_boundaries(inputs: _impl.PolicyInputs, selected: str) -> dict:
    """Recompute the nearest r-level where the raw optimizer switches to HOLD.

    This implementation is intentionally local to the public facade. It avoids
    calling the rebound function in ai_policy_base, which caused recursion.
    """
    if selected == "HOLD":
        return {
            "available": False,
            "reason": "Для HOLD границы отмены до исполнения нет; переоценка при движении ±0.15R, новой цепочке или касании рубежа.",
        }
    grid = _impl.np.linspace(
        max(-0.95, inputs.r0 - 0.50),
        min(inputs.T - 0.02, inputs.r0 + 0.50),
        21,
    )
    rows = []
    for r_value in grid:
        scenario = _impl.replace(
            inputs,
            r0=float(r_value),
            max_r=max(inputs.max_r, float(r_value)),
        )
        metrics, sim = _impl._run_once(
            scenario, n_paths=1200, n_steps=160, seed=0xD000)
        choice, _ = _impl._raw_policy_choice(metrics, scenario.r0)
        p_take = float(_impl.np.mean(~_impl.np.isnan(sim.take_time)))
        p_stop = float(_impl.np.mean(~_impl.np.isnan(sim.stop_time)))
        rows.append({
            "r": round(float(r_value), 4),
            "choice": choice,
            "barrier_ev_r": round(inputs.T * p_take - p_stop, 4),
        })
    hold_rows = [row for row in rows if row["choice"] == "HOLD"]
    nearest = min(hold_rows, key=lambda row: abs(row["r"] - inputs.r0)) if hold_rows else None
    return {
        "available": bool(nearest),
        "hold_switch": nearest,
        "grid_min_r": rows[0]["r"],
        "grid_max_r": rows[-1]["r"],
        "method": "пересчёт всех политик по r-сетке; остальные опционные параметры фиксированы",
        "reason": None if nearest else "На проверенной r-сетке переход к HOLD не найден.",
    }


# analyze_policies is defined in ai_policy_v2 and resolves module globals there.
# Patch both its module and the shared base module before any analysis is called.
_impl.cancellation_boundaries = cancellation_boundaries
_impl._base.cancellation_boundaries = cancellation_boundaries
globals()["cancellation_boundaries"] = cancellation_boundaries
