import math
from typing import Any, Dict, List, Optional
import numpy as np


def compute_wavelet_analysis(
    price_series: List[Dict[str, Any]],
    sampling_minutes: float = 5.0,
) -> Dict[str, Any]:
    """
    Time-Frequency Wavelet Cycle Map:
    CWT Morlet analysis on 5m log-price series for dominant period extraction & energy breakdown.
    """
    if not price_series or len(price_series) < 15:
        return {
            "available": False,
            "reason": "Недостаточно исторический данных цен для CWT аналитики",
            "period_grid": [],
            "timestamps": [],
            "spectrogram": [],
            "dominant_ridge": [],
            "summary": {
                "dominant_period_hours": 0.0,
                "micro_energy_pct": 33.3,
                "intraday_energy_pct": 33.3,
                "macro_energy_pct": 33.4,
                "persistence": 0.0,
                "authority": "derived_price_context",
                "independent_vote": False,
            },
        }

    # 1. Извлекаем временной ряд цен и timestamps
    pts = sorted(price_series, key=lambda p: float(p.get("ts", 0)))
    prices = np.asarray([float(p["price"]) for p in pts if p.get("price") is not None and math.isfinite(float(p["price"]))], dtype=float)
    timestamps = [float(p["ts"]) for p in pts if p.get("ts") is not None]

    if len(prices) < 15:
        return {"available": False, "reason": "Недостаточно валидных цен"}

    # Вычисляем ряды log-доходностей
    log_prices = np.log(np.maximum(prices, 1e-6))
    returns = np.diff(log_prices)
    returns = returns - np.mean(returns)

    # 2. Определяем сетку периодов (от 30 минут до 5 дней)
    periods_hours = [0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 24.0, 48.0, 72.0, 120.0]
    scales = [(p * 60.0) / sampling_minutes for p in periods_hours]

    w0 = 6.0
    spectrogram = []
    ridge = []

    num_t = len(returns)
    ts_subset = timestamps[1:]

    # 3. Вычисление CWT с морлетовским вейвлетом для каждого масштаба s
    power_matrix = np.zeros((len(scales), num_t), dtype=float)

    for idx, s in enumerate(scales):
        t_vec = np.arange(-int(3 * s), int(3 * s) + 1)
        if len(t_vec) == 0:
            t_vec = np.array([0])
        # Morlet wavelet
        psi = (np.pi ** -0.25) * np.exp(1j * w0 * (t_vec / s)) * np.exp(-0.5 * (t_vec / s) ** 2) / math.sqrt(s)
        conv = np.convolve(returns, psi, mode="same")
        if len(conv) > num_t:
            start = (len(conv) - num_t) // 2
            conv = conv[start : start + num_t]
        power = np.abs(conv) ** 2
        power_matrix[idx, :] = power
        spectrogram.append([round(float(v), 4) for v in power])

    # 4. Извлечение доминирующего хребта (Dominant Ridge)
    for t_idx in range(num_t):
        col_powers = power_matrix[:, t_idx]
        max_s_idx = int(np.argmax(col_powers))
        dom_period = periods_hours[max_s_idx]
        ridge.append({
            "ts": ts_subset[t_idx],
            "period_hours": dom_period,
            "power": round(float(col_powers[max_s_idx]), 4),
        })

    # 5. Сводная разбивка энергии по диапазонам циклов
    # Micro: < 4h (индексы 0..3), Intraday: 4h - 24h (индексы 3..6), Macro: > 24h (индексы 6..9)
    micro_power = np.sum(power_matrix[:3, :])
    intraday_power = np.sum(power_matrix[3:6, :])
    macro_power = np.sum(power_matrix[6:, :])
    total_power = micro_power + intraday_power + macro_power + 1e-9

    micro_pct = round(float(micro_power / total_power * 100.0), 1)
    intraday_pct = round(float(intraday_power / total_power * 100.0), 1)
    macro_pct = round(float(macro_power / total_power * 100.0), 1)

    # Доминирующий цикл на текущем шаге
    latest_ridge = ridge[-1] if ridge else {"period_hours": 12.0, "power": 0.0}
    dom_period_latest = latest_ridge["period_hours"]

    # Рассчитываем устойчивость/персистентность цикла
    last_periods = [r["period_hours"] for r in ridge[-10:]]
    if last_periods:
        most_freq = max(set(last_periods), key=last_periods.count)
        persistence = round(last_periods.count(most_freq) / len(last_periods), 2)
    else:
        persistence = 0.5

    return {
        "available": True,
        "period_grid_hours": periods_hours,
        "timestamps": ts_subset,
        "spectrogram": spectrogram,
        "dominant_ridge": ridge,
        "summary": {
            "dominant_period_hours": dom_period_latest,
            "micro_energy_pct": micro_pct,
            "intraday_energy_pct": intraday_pct,
            "macro_energy_pct": macro_pct,
            "persistence": persistence,
            "authority": "derived_price_context",
            "independent_vote": False,
        },
    }
