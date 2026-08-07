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

    Besides the spectrogram, v3 exposes time-varying energy families and ridge
    dynamics.  These are transformations of the same price history and remain
    context-only; they are not an independent vote in the policy engine.
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
            "dominant_ridge": [], "energy_flow": [],
            "summary": {"available": False, "authority": "derived_price_context",
                        "independent_vote": False, "source": source_meta},
        }

    prices = np.asarray([p["price"] for p in pts], dtype=float)
    timestamps = np.asarray([p["ts"] for p in pts], dtype=float)
    log_prices = np.log(np.maximum(prices, 1e-12))
    x = np.arange(len(log_prices), dtype=float)
    slope, intercept = np.polyfit(x, log_prices, 1)
    signal = log_prices - (slope * x + intercept)
    signal -= float(np.mean(signal))
    std = float(np.std(signal))
    if std <= 1e-12:
        return {
            "available": False, "reason": "История цены почти постоянна: спектр неразрешим",
            "period_grid_hours": [], "timestamps": timestamps.tolist(),
            "spectrogram": [], "dominant_ridge": [], "energy_flow": [],
            "summary": {"available": False, "authority": "derived_price_context",
                        "independent_vote": False, "source": source_meta},
        }
    signal /= std

    requested = [0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0,
                 18.0, 24.0, 36.0, 48.0, 72.0, 120.0]
    history_hours = len(signal) * sampling_minutes / 60.0
    periods_hours = [p for p in requested if p * 2.0 <= history_hours]
    if len(periods_hours) < 3:
        return {
            "available": False,
            "reason": f"Истории хватает лишь на {history_hours:.1f} торговых часов",
            "period_grid_hours": periods_hours, "timestamps": timestamps.tolist(),
            "spectrogram": [], "dominant_ridge": [], "energy_flow": [],
            "summary": {
                "available": False, "history_hours_trading": round(history_hours, 1),
                "authority": "derived_price_context", "independent_vote": False,
                "source": source_meta,
            },
        }

    w0 = 6.0
    fourier_factor = 4 * math.pi / (w0 + math.sqrt(2 + w0 * w0))
    samples_per_hour = 60.0 / sampling_minutes
    scales = [(p * samples_per_hour) / fourier_factor for p in periods_hours]

    power = np.zeros((len(scales), len(signal)), dtype=float)
    phase = np.zeros((len(scales), len(signal)), dtype=float)
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
        phase[idx] = np.angle(conv)
        edge_widths.append(min(len(signal) // 3, max(1, int(math.ceil(math.sqrt(2) * scale)))))

    positive = power[power > 0]
    median = float(np.median(positive)) if positive.size else 1.0
    transformed = np.log1p(power / max(median, 1e-12))
    lo = float(np.percentile(transformed, 5))
    hi = float(np.percentile(transformed, 99))
    if hi <= lo:
        hi = lo + 1.0
    visual = np.clip((transformed - lo) / (hi - lo), 0.0, 1.0)

    ridge = []
    ridge_rows: list[int] = []
    second_rows: list[int] = []
    for col in range(len(signal)):
        candidates = visual[:, col].copy()
        for row, edge in enumerate(edge_widths):
            if col < edge or col >= len(signal) - edge:
                candidates[row] *= 0.35
        order = np.argsort(candidates)[::-1]
        best = int(order[0])
        second = int(order[1]) if len(order) > 1 else best
        ridge_rows.append(best)
        second_rows.append(second)
        ridge.append({
            "ts": float(timestamps[col]),
            "period_hours": periods_hours[best],
            "power": round(float(candidates[best]), 4),
            "secondary_period_hours": periods_hours[second],
            "secondary_ratio": round(float(candidates[second] / max(candidates[best], 1e-9)), 3),
        })

    micro_idx = [i for i, p in enumerate(periods_hours) if p < 4]
    intra_idx = [i for i, p in enumerate(periods_hours) if 4 <= p <= 24]
    macro_idx = [i for i, p in enumerate(periods_hours) if p > 24]

    def family_at(col: int, indices: list[int]) -> float:
        return float(np.sum(visual[indices, col])) if indices else 0.0

    energy_flow = []
    for col in range(len(signal)):
        micro = family_at(col, micro_idx)
        intra = family_at(col, intra_idx)
        macro = family_at(col, macro_idx)
        total = micro + intra + macro
        if total <= 1e-12:
            mp = ip = xp = 0.0
        else:
            mp, ip, xp = 100 * micro / total, 100 * intra / total, 100 * macro / total
        energy_flow.append({
            "ts": float(timestamps[col]),
            "micro": round(mp, 2), "intraday": round(ip, 2), "macro": round(xp, 2),
        })

    # Global family mix remains useful as a summary, while the flow carries the
    # important time evolution used by the visualization.
    mean_by_scale = np.mean(visual, axis=1)
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
    order_now = np.argsort(current_col)[::-1]
    top_row = int(order_now[0])
    second_row = int(order_now[1]) if len(order_now) > 1 else top_row
    secondary_ratio = float(current_col[second_row] / max(current_col[top_row], 1e-9))
    secondary_period = float(periods_hours[second_row])

    old = ridge[-36:-18] if len(ridge) >= 36 else ridge[:max(1, len(ridge) // 2)]
    old_med = float(np.median([r["period_hours"] for r in old])) if old else dominant
    if secondary_ratio >= 0.82 and abs(math.log(max(secondary_period, 1e-6) / max(dominant, 1e-6))) > 0.35:
        shift = "BIFURCATED"
    elif dominant < old_med * 0.75:
        shift = "LONG → SHORT"
    elif dominant > old_med * 1.33:
        shift = "SHORT → LONG"
    elif concentration < 0.18:
        shift = "DIFFUSE"
    else:
        shift = "STABLE"

    # Ridge-period velocity in log-hours per trading hour.
    ridge_velocity = 0.0
    if len(recent) >= 3:
        t_recent = np.asarray([r["ts"] for r in recent], dtype=float)
        p_recent = np.log(np.maximum([r["period_hours"] for r in recent], 1e-6))
        t_hours = (t_recent - t_recent[0]) / 3600.0
        if float(np.ptp(t_hours)) > 1e-9:
            ridge_velocity = float(np.polyfit(t_hours, p_recent, 1)[0])

    # Power half-life estimate only when recent dominant-ridge power is actually
    # decaying.  This is labelled an estimate, never a forecast of price reversal.
    recent_power = ridge[-min(24, len(ridge)):]
    power_slope = 0.0
    half_life = None
    if len(recent_power) >= 6:
        t_pow = np.asarray([r["ts"] for r in recent_power], dtype=float)
        p_pow = np.log(np.maximum([r["power"] for r in recent_power], 1e-4))
        th = (t_pow - t_pow[0]) / 3600.0
        if float(np.ptp(th)) > 1e-9:
            power_slope = float(np.polyfit(th, p_pow, 1)[0])
            if power_slope < -1e-6:
                half_life = math.log(2.0) / -power_slope

    # Phase stability along the ridge: compare observed phase increments with
    # the increment implied by the selected period.  1.0 = very coherent.
    phase_scores: list[float] = []
    start_phase = max(1, len(signal) - 24)
    dt_hours = sampling_minutes / 60.0
    for col in range(start_phase, len(signal)):
        row = ridge_rows[col]
        prev_row = ridge_rows[col - 1]
        if row != prev_row:
            continue
        observed = math.atan2(
            math.sin(float(phase[row, col] - phase[row, col - 1])),
            math.cos(float(phase[row, col] - phase[row, col - 1])),
        )
        expected = 2 * math.pi * dt_hours / max(periods_hours[row], dt_hours)
        err = math.atan2(math.sin(observed - expected), math.cos(observed - expected))
        phase_scores.append((math.cos(err) + 1.0) / 2.0)
    phase_stability = float(np.mean(phase_scores)) if phase_scores else 0.0

    max_cols = 360
    start = max(0, len(signal) - max_cols)
    ts_out = timestamps[start:]
    visual_out = visual[:, start:]
    ridge_out = ridge[start:]
    flow_out = energy_flow[start:]

    summary = {
        "dominant_period_hours": round(dominant, 2),
        "secondary_period_hours": round(secondary_period, 2),
        "secondary_power_ratio": round(secondary_ratio, 3),
        "micro_energy_pct": micro_pct, "intraday_energy_pct": intra_pct,
        "macro_energy_pct": macro_pct,
        "persistence": round(persistence, 3),
        "phase_stability": round(phase_stability, 3),
        "spectral_concentration": round(concentration, 3),
        "cycle_shift": shift,
        "ridge_velocity_log_per_hour": round(ridge_velocity, 4),
        "ridge_power_slope_log_per_hour": round(power_slope, 4),
        "decay_half_life_estimate_hours": round(half_life, 2) if half_life is not None else None,
        "history_hours_trading": round(history_hours, 1),
        "period_max_hours": max(periods_hours),
        "points": len(signal), "time_basis": "trading_bars",
        "source": source_meta,
        "authority": "derived_price_context", "independent_vote": False,
    }
    return {
        "version": "wavelet-v3-cycle-flow",
        "available": True,
        "period_grid_hours": periods_hours,
        "timestamps": [float(v) for v in ts_out],
        "spectrogram": [[round(float(v), 4) for v in row] for row in visual_out],
        "dominant_ridge": ridge_out,
        "energy_flow": flow_out,
        "edge_widths": edge_widths,
        "summary": summary,
    }
