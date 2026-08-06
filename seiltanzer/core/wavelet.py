from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def compute_wavelet_analysis(
    price_series: List[Dict[str, Any]],
    sampling_minutes: float = 5.0,
    *,
    source_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Morlet CWT on observed, detrended log-price history.

    Periods are expressed in *trading-bar hours*.  Overnight/session gaps are
    not interpolated into fake observations.  A period is exposed only when the
    available bar history contains at least ~2 cycles at that scale.
    """
    source_meta = dict(source_meta or {})
    pts = []
    for p in price_series or []:
        if _finite(p.get("ts")) and _finite(p.get("price")) and float(p["price"]) > 0:
            pts.append({"ts": float(p["ts"]), "price": float(p["price"])})
    pts.sort(key=lambda p: p["ts"])
    unique = {p["ts"]: p["price"] for p in pts}
    pts = [{"ts": ts, "price": unique[ts]} for ts in sorted(unique)]

    if len(pts) < 36:
        return {
            "available": False,
            "reason": f"Недостаточно реальной 5m истории для CWT: {len(pts)} точек (нужно ≥36)",
            "period_grid_hours": [], "timestamps": [], "spectrogram": [],
            "dominant_ridge": [],
            "summary": {
                "available": False,
                "authority": "derived_price_context",
                "independent_vote": False,
                "source": source_meta,
            },
        }

    prices = np.asarray([p["price"] for p in pts], dtype=float)
    timestamps = np.asarray([p["ts"] for p in pts], dtype=float)
    log_prices = np.log(np.maximum(prices, 1e-12))

    # Remove slow linear drift before cycle extraction.  This prevents a trend
    # from masquerading as a giant low-frequency wavelet component.
    x = np.arange(len(log_prices), dtype=float)
    slope, intercept = np.polyfit(x, log_prices, 1)
    signal = log_prices - (slope * x + intercept)
    signal -= float(np.mean(signal))
    std = float(np.std(signal))
    if std <= 1e-12:
        return {
            "available": False,
            "reason": "История цены почти постоянна: спектр неразрешим",
            "period_grid_hours": [], "timestamps": timestamps.tolist(),
            "spectrogram": [], "dominant_ridge": [],
            "summary": {
                "available": False,
                "authority": "derived_price_context",
                "independent_vote": False,
                "source": source_meta,
            },
        }
    signal /= std

    requested = [0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0,
                 18.0, 24.0, 36.0, 48.0, 72.0, 120.0]
    history_hours = len(signal) * sampling_minutes / 60.0
    # Require at least two complete cycles in the observed bar history.
    periods_hours = [p for p in requested if p * 2.0 <= history_hours]
    if len(periods_hours) < 3:
        return {
            "available": False,
            "reason": f"Истории хватает лишь на {history_hours:.1f} торговых часов",
            "period_grid_hours": periods_hours,
            "timestamps": timestamps.tolist(),
            "spectrogram": [], "dominant_ridge": [],
            "summary": {
                "available": False,
                "history_hours_trading": round(history_hours, 1),
                "authority": "derived_price_context",
                "independent_vote": False,
                "source": source_meta,
            },
        }

    w0 = 6.0
    fourier_factor = 4 * math.pi / (w0 + math.sqrt(2 + w0 * w0))
    samples_per_hour = 60.0 / sampling_minutes
    scales = [(p * samples_per_hour) / fourier_factor for p in periods_hours]

    power = np.zeros((len(scales), len(signal)), dtype=float)
    edge_widths: list[int] = []
    for idx, scale in enumerate(scales):
        half = max(4, int(math.ceil(3.5 * scale)))
        t = np.arange(-half, half + 1, dtype=float)
        u = t / scale
        wavelet = ((np.pi ** -0.25) / math.sqrt(scale)
                   * np.exp(1j * w0 * u) * np.exp(-0.5 * u * u))
        conv = np.convolve(signal, np.conjugate(wavelet[::-1]), mode="same")
        if len(conv) != len(signal):
            start = max(0, (len(conv) - len(signal)) // 2)
            conv = conv[start:start + len(signal)]
        power[idx] = np.abs(conv) ** 2
        edge_widths.append(min(len(signal) // 3, max(1, int(math.ceil(math.sqrt(2) * scale)))))

    # Normalize visual power robustly.  Raw CWT energy spans orders of magnitude;
    # the first prototype used raw linear power and therefore looked uniformly
    # pale.  log1p + robust percentiles reveals the actual time/scale structure.
    positive = power[power > 0]
    median = float(np.median(positive)) if positive.size else 1.0
    transformed = np.log1p(power / max(median, 1e-12))
    lo = float(np.percentile(transformed, 5))
    hi = float(np.percentile(transformed, 99))
    if hi <= lo:
        hi = lo + 1.0
    visual = np.clip((transformed - lo) / (hi - lo), 0.0, 1.0)

    ridge = []
    for col in range(len(signal)):
        # Penalize cone-of-influence edges for each scale when picking the ridge.
        candidates = visual[:, col].copy()
        for row, edge in enumerate(edge_widths):
            if col < edge or col >= len(signal) - edge:
                candidates[row] *= 0.35
        best = int(np.argmax(candidates))
        ridge.append({
            "ts": float(timestamps[col]),
            "period_hours": periods_hours[best],
            "power": round(float(candidates[best]), 4),
        })

    # Energy families are based on scale-normalized power so long-period scales
    # do not win solely because their raw wavelet support is larger.
    mean_by_scale = np.mean(visual, axis=1)
    micro_idx = [i for i, p in enumerate(periods_hours) if p < 4]
    intra_idx = [i for i, p in enumerate(periods_hours) if 4 <= p <= 24]
    macro_idx = [i for i, p in enumerate(periods_hours) if p > 24]

    def family_energy(indices: list[int]) -> float:
        return float(np.sum(mean_by_scale[indices])) if indices else 0.0

    micro = family_energy(micro_idx)
    intraday = family_energy(intra_idx)
    macro = family_energy(macro_idx)
    total = micro + intraday + macro
    if total <= 1e-12:
        micro_pct = intra_pct = macro_pct = 0.0
    else:
        micro_pct = round(micro / total * 100, 1)
        intra_pct = round(intraday / total * 100, 1)
        macro_pct = round(macro / total * 100, 1)

    recent = ridge[-min(18, len(ridge)):]
    recent_periods = np.asarray([r["period_hours"] for r in recent], dtype=float)
    dominant = float(np.median(recent_periods)) if recent_periods.size else periods_hours[0]
    tolerance = max(0.5, dominant * 0.25)
    persistence = float(np.mean(np.abs(recent_periods - dominant) <= tolerance)) if recent_periods.size else 0.0

    current_col = visual[:, -1]
    concentration = float(np.max(current_col) / max(np.sum(current_col), 1e-12))
    old = ridge[-36:-18] if len(ridge) >= 36 else ridge[:max(1, len(ridge) // 2)]
    old_med = float(np.median([r["period_hours"] for r in old])) if old else dominant
    if dominant < old_med * 0.75:
        shift = "LONG → SHORT"
    elif dominant > old_med * 1.33:
        shift = "SHORT → LONG"
    elif concentration < 0.18:
        shift = "DIFFUSE"
    else:
        shift = "STABLE"

    # Only return a bounded recent surface to keep the endpoint light.
    max_cols = 360
    start = max(0, len(signal) - max_cols)
    ts_out = timestamps[start:]
    visual_out = visual[:, start:]
    ridge_out = ridge[start:]

    summary = {
        "dominant_period_hours": round(dominant, 2),
        "micro_energy_pct": micro_pct,
        "intraday_energy_pct": intra_pct,
        "macro_energy_pct": macro_pct,
        "persistence": round(persistence, 3),
        "spectral_concentration": round(concentration, 3),
        "cycle_shift": shift,
        "history_hours_trading": round(history_hours, 1),
        "period_max_hours": max(periods_hours),
        "points": len(signal),
        "time_basis": "trading_bars",
        "source": source_meta,
        "authority": "derived_price_context",
        "independent_vote": False,
    }
    return {
        "version": "wavelet-v2-real-history",
        "available": True,
        "period_grid_hours": periods_hours,
        "timestamps": [float(v) for v in ts_out],
        "spectrogram": [[round(float(v), 4) for v in row] for row in visual_out],
        "dominant_ridge": ridge_out,
        "edge_widths": edge_widths,
        "summary": summary,
    }
