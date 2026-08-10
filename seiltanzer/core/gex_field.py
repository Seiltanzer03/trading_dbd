"""Analytic local geometry of the observed OI×gamma strike field.

This is option-derived context, not an observed dealer-position signal.  The
Gaussian field and both derivatives use the same kernel bandwidth so FORCE and
STIFFNESS are mathematically consistent and do not introduce extra votes.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np


def _finite_arrays(strikes: Sequence[Any], gex: Sequence[Any]) -> tuple[np.ndarray, np.ndarray]:
    k = np.asarray(strikes, dtype=float)
    g = np.asarray(gex, dtype=float)
    if k.shape != g.shape:
        raise ValueError("strikes and gex must have equal shape")
    mask = np.isfinite(k) & np.isfinite(g)
    k, g = k[mask], g[mask]
    order = np.argsort(k)
    return k[order], g[order]


def analytic_gex_field(
    strikes: Sequence[Any],
    gex: Sequence[Any],
    spot: float,
    *,
    bandwidth: float | None = None,
) -> dict:
    """Return FIELD, -dFIELD/dS and d²FIELD/dS² at ``spot``.

    GEX is scaled by its robust 90th absolute percentile.  Raw derivatives keep
    their natural price dimensions; ``*_score`` values remove those dimensions
    using the common bandwidth and apply a bounded ``tanh`` normalization.
    """
    k, g = _finite_arrays(strikes, gex)
    spot = float(spot)
    if len(k) < 3 or not math.isfinite(spot):
        return {"available": False, "reason": "insufficient finite GEX strikes"}

    spacings = np.diff(np.unique(k))
    spacings = spacings[np.isfinite(spacings) & (spacings > 0)]
    if bandwidth is None:
        spacing = float(np.median(spacings)) if spacings.size else abs(spot) * 0.0045
        bandwidth = max(spacing * 1.45, abs(spot) * 0.001, 1e-9)
    h = float(bandwidth)
    if not math.isfinite(h) or h <= 0:
        raise ValueError("bandwidth must be finite and > 0")

    scale = float(np.percentile(np.abs(g), 90)) or float(np.max(np.abs(g))) or 1.0
    weights = g / scale
    distance = spot - k
    kernel = np.exp(-0.5 * (distance / h) ** 2)
    field = float(np.sum(weights * kernel))
    gradient = float(np.sum(weights * kernel * (-distance / (h * h))))
    stiffness = float(np.sum(
        weights * kernel * ((distance * distance) / (h ** 4) - 1.0 / (h * h))))
    force = -gradient

    positive = np.flatnonzero(g > 0)
    negative = np.flatnonzero(g < 0)
    call_wall = float(k[positive[np.argmax(g[positive])]]) if positive.size else None
    put_wall = float(k[negative[np.argmax(np.abs(g[negative]))]]) if negative.size else None

    return {
        "available": True,
        "family": "option_distribution",
        "independent_vote": False,
        "authority": "context_only",
        "quality": "oi_x_black_scholes_gamma_not_observed_dealer_position",
        "spot": spot,
        "bandwidth": h,
        "gex_scale": scale,
        "field": field,
        "gradient": gradient,
        "force": force,
        "stiffness": stiffness,
        "field_score": math.tanh(field / 2.0),
        "force_score": math.tanh(force * h),
        "stiffness_score": math.tanh(stiffness * h * h),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "distance_to_call_wall": None if call_wall is None else call_wall - spot,
        "distance_to_put_wall": None if put_wall is None else put_wall - spot,
        "formula": {
            "field": "sum(g_i * exp(-(S-K_i)^2/(2h^2)))",
            "force": "-dFIELD/dS",
            "stiffness": "d2FIELD/dS2",
        },
    }

