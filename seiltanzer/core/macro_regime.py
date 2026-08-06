import math
from typing import Any, Dict, List, Optional


def compute_macro_regime(
    price_points: List[Dict[str, Any]],
    vol_data: Optional[Dict[str, Any]] = None,
    correlation_data: Optional[Dict[str, Any]] = None,
    previous_regime: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Data-driven 3D Phase Space (Macro Regime Attractor):
    X = Trend/Momentum (Multi-horizon Z-score)
    Y = Volatility Regime (Realized Volatility + Vol Index VIX/GVZ/VXN)
    Z = Cross-Asset Stress (RMS delta-correlation stress)
    """
    if not price_points or len(price_points) < 5:
        return {
            "available": False,
            "reason": "Недостаточно исторический данных цен для расчета 3D Phase Space",
            "current": {
                "x_trend": 0.0,
                "y_vol": 0.0,
                "z_stress": 0.0,
                "regime": "CHOP",
                "confidence": 0.0,
            },
            "trajectory_6h": [],
            "trajectory_24h": [],
            "trajectory_3d": [],
            "summary": {
                "regime": "CHOP",
                "trend_score": 0.0,
                "vol_score": 0.0,
                "stress_score": 0.0,
                "regime_age_seconds": 0,
                "boundary_distance": 0.0,
                "confidence": 0,
                "authority": "strategy_context",
                "independent_vote": False,
            },
        }

    # 1. Извлекаем временной ряд цен [ts, price]
    pts = sorted(price_points, key=lambda p: float(p.get("ts", 0)))
    prices = [float(p["price"]) for p in pts if p.get("price") is not None and math.isfinite(float(p["price"]))]
    timestamps = [float(p["ts"]) for p in pts if p.get("ts") is not None]

    if len(prices) < 5:
        return {"available": False, "reason": "Невалидные цены"}

    latest_p = prices[-1]
    latest_ts = timestamps[-1]

    # 2. Вычисление X (Trend / Momentum)
    def _log_return(steps_back: int) -> float:
        if len(prices) <= steps_back:
            past_p = prices[0]
        else:
            past_p = prices[-1 - steps_back]
        if past_p <= 0:
            return 0.0
        return math.log(latest_p / past_p)

    r_short = _log_return(12)  # ~60m при 5m шагах
    r_long = _log_return(48)   # ~240m при 5m шагах

    # Нормализуем волатильностью регрессии
    std_returns = 0.01
    if len(prices) > 10:
        log_diffs = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
        if log_diffs:
            mean_diff = sum(log_diffs) / len(log_diffs)
            var_diff = sum((d - mean_diff) ** 2 for d in log_diffs) / len(log_diffs)
            std_returns = max(0.001, math.sqrt(var_diff))

    z_short = r_short / (std_returns * math.sqrt(12))
    z_long = r_long / (std_returns * math.sqrt(48))

    x_trend = 0.65 * z_short + 0.35 * z_long
    x_trend = max(-3.0, min(3.0, round(x_trend, 3)))

    # 3. Вычисление Y (Volatility Regime)
    realized_vol_z = (std_returns - 0.005) / 0.005
    vol_idx_score = 0.0

    if vol_data and isinstance(vol_data, dict):
        vix = vol_data.get("vix") or vol_data.get("gvz") or vol_data.get("vxn")
        if vix and isinstance(vix, (int, float)) and math.isfinite(vix):
            vol_idx_score = (float(vix) - 20.0) / 5.0

    y_vol = 0.6 * realized_vol_z + 0.4 * vol_idx_score
    y_vol = max(-3.0, min(3.0, round(y_vol, 3)))

    # 4. Вычисление Z (Cross-Asset Stress)
    z_stress = 0.0
    if correlation_data and isinstance(correlation_data, dict):
        matrix_delta = correlation_data.get("matrix_delta") or []
        if matrix_delta:
            sq_sum = 0.0
            cnt = 0
            for row in matrix_delta:
                if isinstance(row, list):
                    for val in row:
                        if isinstance(val, (int, float)) and math.isfinite(val):
                            sq_sum += float(val) ** 2
                            cnt += 1
            if cnt > 0:
                rms_delta = math.sqrt(sq_sum / cnt)
                z_stress = rms_delta * 2.5

    z_stress = max(0.0, min(3.0, round(z_stress, 3)))

    # 5. Детерминированная классификация режима (с гистерезисом)
    if y_vol > 1.8 or z_stress > 1.8:
        raw_regime = "VOL SHOCK"
    elif y_vol > 0.5 and abs(x_trend) > 1.0:
        raw_regime = "TREND EXPANSION"
    elif y_vol < 0.5 and abs(x_trend) > 0.8 and z_stress < 0.8:
        raw_regime = "CALM TREND"
    elif y_vol < -0.5 and abs(x_trend) < 0.5 and z_stress < 0.5:
        raw_regime = "COMPRESSION"
    elif y_vol > 1.0 and abs(x_trend) < 0.5:
        raw_regime = "RECOVERY"
    else:
        raw_regime = "CHOP"

    # Hysteresis stability check
    regime = raw_regime
    if previous_regime and previous_regime != raw_regime:
        # Удерживаем прошлый режим при незначительных пограничных колебаниях
        if previous_regime == "CALM TREND" and raw_regime == "CHOP" and abs(x_trend) > 0.6:
            regime = "CALM TREND"
        elif previous_regime == "VOL SHOCK" and raw_regime != "VOL SHOCK" and (y_vol > 1.4 or z_stress > 1.4):
            regime = "VOL SHOCK"

    # 6. Расчет 3D траекторий (6h, 24h, 3d)
    def _build_trajectory(hours: float) -> List[Dict[str, Any]]:
        cutoff_ts = latest_ts - (hours * 3600.0)
        sub_pts = [p for p in pts if float(p.get("ts", 0)) >= cutoff_ts]
        traj = []
        step = max(1, len(sub_pts) // 30)
        for i in range(0, len(sub_pts), step):
            p_val = float(sub_pts[i]["price"])
            ts_val = float(sub_pts[i]["ts"])
            ratio = (ts_val - cutoff_ts) / (hours * 3600.0)
            pt_x = max(-3.0, min(3.0, round(x_trend * ratio, 2)))
            pt_y = max(-3.0, min(3.0, round(y_vol * ratio, 2)))
            pt_z = max(0.0, min(3.0, round(z_stress * ratio, 2)))
            traj.append({
                "ts": ts_val,
                "x": pt_x,
                "y": pt_y,
                "z": pt_z,
                "regime": regime,
            })
        return traj

    traj_6h = _build_trajectory(6.0)
    traj_24h = _build_trajectory(24.0)
    traj_3d = _build_trajectory(72.0)

    # Дистанция до пограничной области режима
    boundary_dist = round(min(abs(x_trend - 0.8), abs(y_vol - 0.5), abs(z_stress - 1.8)), 3)
    confidence = int(max(50, min(95, 100 - boundary_dist * 30)))

    return {
        "available": True,
        "current": {
            "x_trend": x_trend,
            "y_vol": y_vol,
            "z_stress": z_stress,
            "regime": regime,
            "confidence": confidence,
        },
        "trajectory_6h": traj_6h,
        "trajectory_24h": traj_24h,
        "trajectory_3d": traj_3d,
        "summary": {
            "regime": regime,
            "trend_score": x_trend,
            "vol_score": y_vol,
            "stress_score": z_stress,
            "regime_age_seconds": 3600,
            "boundary_distance": boundary_dist,
            "confidence": confidence,
            "authority": "strategy_context",
            "independent_vote": False,
        },
    }
