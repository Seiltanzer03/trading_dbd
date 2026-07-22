"""Опционная математика: implied move, risk-neutral density (Бриден–Литценбергер), GEX.

Все функции работают с «сырыми» массивами цепочки (страйк, bid/ask или mid,
open interest, IV) — источник данных им безразличен, что позволяет тестировать
на синтетической цепочке Блэка–Шоулза.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_call(S: float, K: float, t: float, sigma: float, r: float = 0.0) -> float:
    """Цена колла Блэка–Шоулза (для синтетических цепочек и проверок)."""
    if t <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return S * _norm_cdf(d1) - K * math.exp(-r * t) * _norm_cdf(d2)


def bs_put(S: float, K: float, t: float, sigma: float, r: float = 0.0) -> float:
    return bs_call(S, K, t, sigma, r) - S + K * math.exp(-r * t)


def bs_gamma(S: float, K: float, t: float, sigma: float, r: float = 0.0) -> float:
    """Гамма Блэка–Шоулза (одинакова для колла и пута)."""
    if t <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    return _norm_pdf(d1) / (S * sigma * math.sqrt(t))


# --------------------------------------------------------------- implied move

@dataclass(frozen=True)
class ImpliedMove:
    """ATM straddle -> ожидаемое |движение| до экспирации -> sigma_implied.

    Формулы:
      move_frac = straddle_mid / spot            (E|dS/S| до экспирации)
      sigma_annual = move_frac * sqrt(pi / (2*t)) (из E|Z| = sigma*sqrt(2/pi))
    """
    spot: float
    atm_strike: float
    straddle: float
    move_frac: float       # ожидаемое |движение| как доля цены, до экспирации
    move_abs: float        # то же в пунктах цены
    sigma_annual: float    # годовая implied-вола
    t_years: float


def implied_move(strikes, call_mids, put_mids, spot: float, t_years: float) -> ImpliedMove:
    """ATM straddle ближайшей экспирации -> implied move (п.4 ядра).

    strikes/call_mids/put_mids — выровненные массивы одной экспирации.
    """
    strikes = np.asarray(strikes, dtype=float)
    calls = np.asarray(call_mids, dtype=float)
    puts = np.asarray(put_mids, dtype=float)
    if len(strikes) == 0:
        raise ValueError("пустая цепочка")
    if t_years <= 0:
        raise ValueError("экспирация в прошлом")
    ok = np.isfinite(calls) & np.isfinite(puts) & (calls > 0) & (puts > 0)
    if not ok.any():
        raise ValueError("нет валидных mid-цен для straddle")
    k_idx = int(np.argmin(np.abs(strikes[ok] - spot)))
    k = strikes[ok][k_idx]
    straddle = float(calls[ok][k_idx] + puts[ok][k_idx])
    move_frac = straddle / spot
    sigma_annual = move_frac * math.sqrt(math.pi / (2.0 * t_years))
    return ImpliedMove(spot=spot, atm_strike=float(k), straddle=straddle,
                       move_frac=move_frac, move_abs=move_frac * spot,
                       sigma_annual=sigma_annual, t_years=t_years)


def realized_vol(closes, trading_days: int = 20, annualize: int = 252) -> float:
    """Реализованная вола за trading_days по дневным закрытиям (baseline, п.4)."""
    c = np.asarray(closes, dtype=float)
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < trading_days + 1:
        raise ValueError(f"нужно >= {trading_days + 1} закрытий, есть {len(c)}")
    rets = np.diff(np.log(c[-(trading_days + 1):]))
    return float(np.std(rets, ddof=1) * math.sqrt(annualize))


# ------------------------------------------------- Breeden–Litzenberger density

@dataclass
class RNDensity:
    """Risk-neutral плотность q(K) ~ e^{rT} * d2C/dK2 (сглаженная, обрезанная)."""
    strikes: np.ndarray
    density: np.ndarray    # нормирована: интеграл по трапециям = 1
    t_years: float

    def tail_probs(self, level: float) -> tuple[float, float]:
        """(P(S_T > level), P(S_T < level)) по плотности, трапеции."""
        k, q = self.strikes, self.density
        above = k >= level
        if not above.any():
            return 0.0, 1.0
        if above.all():
            return 1.0, 0.0
        i = int(np.argmax(above))  # первый страйк >= level
        p_above = float(np.trapezoid(q[i:], k[i:]))
        if i > 0:
            # частичная трапеция между level и первым страйком >= level
            k0, k1 = k[i - 1], k[i]
            q_at = q[i - 1] + (q[i] - q[i - 1]) * (level - k0) / (k1 - k0)
            p_above += float(0.5 * (q_at + q[i]) * (k1 - level))
        p_above = min(max(p_above, 0.0), 1.0)
        return p_above, 1.0 - p_above


def market_r_distribution(density: "RNDensity", scale: float, entry: float,
                          stop: float, take: float, direction: str,
                          T: float, n_bins: int = 11) -> dict:
    """Risk-neutral распределение ИСХОДА сделки в R-координатах (из опционов).

    Плотность рынка q(S) (страйки прокси × scale -> шкала инструмента) переносится
    на ось R сделки: r = (S-entry)/risk для лонга, (entry-S)/risk для шорта.
    Возвращает 11-корзинное распределение по [-1, T] (масса за тейк -> правая
    корзина, за стоп -> левая), плюс:
      p_take  — P(S за тейком) по рынку,
      p_stop  — P(S за стопом),
      hit_ratio = p_take / (p_take + p_stop) — рыночный аналог «дойти до тейка
                  раньше стопа» (сопоставим с модельной P).
    Это и есть опционное преимущество: рынок против вашей статистики.
    """
    risk = abs(entry - stop)
    if risk <= 0 or T <= 0:
        raise ValueError("вырожденная сделка")
    # density.strikes в шкале прокси -> в шкалу инструмента
    dens = RNDensity(strikes=np.asarray(density.strikes) * scale,
                     density=np.asarray(density.density) / max(scale, 1e-9),
                     t_years=density.t_years)

    def s_of_r(rv):
        return entry + rv * risk if direction == "long" else entry - rv * risk

    # хвостовые массы за барьерами
    if direction == "long":
        p_take = dens.tail_probs(take)[0]   # S >= take
        p_stop = dens.tail_probs(stop)[1]   # S <= stop
    else:
        p_take = dens.tail_probs(take)[1]   # S <= take
        p_stop = dens.tail_probs(stop)[0]   # S >= stop

    edges = np.linspace(-1.0, T, n_bins + 1)
    probs = np.zeros(n_bins)
    for b in range(n_bins):
        r_lo, r_hi = edges[b], edges[b + 1]
        s_a, s_b = s_of_r(r_lo), s_of_r(r_hi)
        lo_s, hi_s = min(s_a, s_b), max(s_a, s_b)
        mass = dens.tail_probs(lo_s)[0] - dens.tail_probs(hi_s)[0]  # P(lo<=S<=hi)
        probs[b] = max(mass, 0.0)
    probs[0] += p_stop      # всё за стопом — в левую корзину
    probs[-1] += p_take     # всё за тейком — в правую
    total = probs.sum()
    if total > 0:
        probs = probs / total
    hit = p_take / (p_take + p_stop) if (p_take + p_stop) > 0 else None
    return {"edges": edges.tolist(), "probs": probs.tolist(),
            "p_take": float(p_take), "p_stop": float(p_stop),
            "hit_ratio": (float(hit) if hit is not None else None)}


def bl_density(strikes, call_mids, t_years: float, r: float = 0.0,
               window: int = 5) -> RNDensity:
    """Плотность Бридена–Литценбергера из mid-цен коллов (п.5 ядра).

    Метод: локальная квадратичная регрессия (окно `window` страйков) цены колла
    C(K); вторая производная = 2*a квадратичного члена. Это одновременно
    сглаживание сплайн-типа и численное дифференцирование. Отрицательные
    значения обрезаются, плотность нормируется на 1.
    """
    k = np.asarray(strikes, dtype=float)
    c = np.asarray(call_mids, dtype=float)
    ok = np.isfinite(k) & np.isfinite(c) & (c >= 0)
    k, c = k[ok], c[ok]
    order = np.argsort(k)
    k, c = k[order], c[order]
    # схлопываем дубликаты страйков
    uk, inv = np.unique(k, return_inverse=True)
    if len(uk) != len(k):
        cc = np.zeros(len(uk))
        cnt = np.zeros(len(uk))
        np.add.at(cc, inv, c)
        np.add.at(cnt, inv, 1)
        k, c = uk, cc / cnt
    if len(k) < max(window, 5):
        raise ValueError(f"слишком мало страйков для плотности: {len(k)}")
    half = max(window // 2, 2)
    dens = np.zeros(len(k))
    for i in range(len(k)):
        lo = max(0, i - half)
        hi = min(len(k), i + half + 1)
        if hi - lo < 3:
            continue
        kk = k[lo:hi] - k[i]
        coeffs = np.polyfit(kk, c[lo:hi], 2)
        dens[i] = 2.0 * coeffs[0] * math.exp(r * t_years)
    dens = np.clip(dens, 0.0, None)
    area = np.trapezoid(dens, k)
    if area <= 0:
        raise ValueError("плотность вырождена (нулевая площадь)")
    return RNDensity(strikes=k, density=dens / area, t_years=t_years)


# ----------------------------------------------------------------- GEX-уровни

@dataclass
class GexProfile:
    """ЭВРИСТИКА: гамма-экспозиция дилеров по страйкам.

    GEX(K) = gamma(K) * OI * 100 * S^2 * 0.01, коллы +, путы -
    (стандартное допущение «дилеры лонг коллы / шорт путы» — НЕ проверяемо,
    использовать только как контекст).
    """
    strikes: np.ndarray
    net_gex: np.ndarray
    zero_flip: float | None          # страйк смены знака сглаженного профиля у спота
    top_levels: list[dict] = field(default_factory=list)  # 3 крупнейших |GEX|


def gex_profile(strikes, call_oi, put_oi, call_iv, put_iv,
                spot: float, t_years: float, top_n: int = 3) -> GexProfile:
    k = np.asarray(strikes, dtype=float)
    coi = np.nan_to_num(np.asarray(call_oi, dtype=float))
    poi = np.nan_to_num(np.asarray(put_oi, dtype=float))
    civ = np.asarray(call_iv, dtype=float)
    piv = np.asarray(put_iv, dtype=float)
    order = np.argsort(k)
    k, coi, poi, civ, piv = k[order], coi[order], poi[order], civ[order], piv[order]
    net = np.zeros(len(k))
    scale = 100.0 * spot * spot * 0.01
    for i in range(len(k)):
        g_c = bs_gamma(spot, k[i], t_years, civ[i]) if np.isfinite(civ[i]) and civ[i] > 0 else 0.0
        g_p = bs_gamma(spot, k[i], t_years, piv[i]) if np.isfinite(piv[i]) and piv[i] > 0 else 0.0
        net[i] = (g_c * coi[i] - g_p * poi[i]) * scale
    # сглаживание профиля скользящим средним (3) и поиск смены знака ближе к споту
    if len(net) >= 3:
        sm = np.convolve(net, np.ones(3) / 3.0, mode="same")
    else:
        sm = net
    zero_flip = None
    best_dist = math.inf
    for i in range(1, len(k)):
        if sm[i - 1] == 0.0:
            continue
        if sm[i - 1] * sm[i] < 0:
            # линейная интерполяция нуля
            x0 = k[i - 1] + (k[i] - k[i - 1]) * abs(sm[i - 1]) / (abs(sm[i - 1]) + abs(sm[i]))
            if abs(x0 - spot) < best_dist:
                best_dist = abs(x0 - spot)
                zero_flip = float(x0)
    idx = np.argsort(-np.abs(net))[:top_n]
    top = [{"strike": float(k[i]), "gex": float(net[i])} for i in sorted(idx, key=lambda j: k[j])
           if abs(net[i]) > 0]
    return GexProfile(strikes=k, net_gex=net, zero_flip=zero_flip, top_levels=top)


# --------------------------------------------------------- синтетическая цепочка

def term_structure(points) -> dict | None:
    """Term-structure волы из ATM-IV нескольких экспираций.

    points: список (days, atm_iv). slope = (IV_дальней − IV_ближней)/IV_ближней.
      slope > 0 — КОНТАНГО (дальняя вола выше): рынок спокоен сейчас, ждёт
        нормализации; далёкие цели по времени реальнее.
      slope < 0 — БЭКВОРДАЦИЯ (ближняя выше): near-term стресс/событие —
        ожидается движение скоро.
    """
    pts = [(float(d), float(v)) for d, v in points if v and v > 0]
    if len(pts) < 2:
        return None
    pts.sort(key=lambda x: x[0])
    near, far = pts[0][1], pts[-1][1]
    slope = (far - near) / near if near > 0 else 0.0
    thr = 0.03
    shape = "контанго" if slope > thr else "бэквордация" if slope < -thr else "плоская"
    return {"points": [{"days": d, "atm_iv": v} for d, v in pts],
            "slope": slope, "shape": shape}


def risk_reversal_skew(strikes, call_iv, put_iv, spot: float,
                       otm: float = 0.04) -> dict | None:
    """Скью опционов (risk-reversal) как сигнал направления рынка.

    RR = IV(OTM call ~spot·(1+otm)) − IV(OTM put ~spot·(1−otm)).
      RR < 0 — путы дороже: рынок платит за защиту от падения (медвежий уклон);
      RR > 0 — коллы дороже: спрос на рост (бычий уклон).
    Возвращает {rr, call_iv_otm, put_iv_otm, atm_iv, tilt, otm} или None.
    """
    k = np.asarray(strikes, dtype=float)
    civ = np.asarray(call_iv, dtype=float)
    piv = np.asarray(put_iv, dtype=float)
    ok_c = np.isfinite(k) & np.isfinite(civ) & (civ > 0)
    ok_p = np.isfinite(k) & np.isfinite(piv) & (piv > 0)
    if ok_c.sum() < 3 or ok_p.sum() < 3 or spot <= 0:
        return None
    kc, ivc = k[ok_c], civ[ok_c]
    kp, ivp = k[ok_p], piv[ok_p]
    order_c, order_p = np.argsort(kc), np.argsort(kp)
    kc, ivc = kc[order_c], ivc[order_c]
    kp, ivp = kp[order_p], ivp[order_p]
    call_otm = float(np.interp(spot * (1 + otm), kc, ivc))
    put_otm = float(np.interp(spot * (1 - otm), kp, ivp))
    atm = float(0.5 * (np.interp(spot, kc, ivc) + np.interp(spot, kp, ivp)))
    rr = call_otm - put_otm
    thr = 0.01  # 1 пункт волы
    tilt = "бычий" if rr > thr else "медвежий" if rr < -thr else "нейтральный"
    return {"rr": rr, "call_iv_otm": call_otm, "put_iv_otm": put_otm,
            "atm_iv": atm, "tilt": tilt, "otm": otm}


def gamma_pin(strikes_instr, net_gex, zero_flip_instr, price: float,
              entry: float, stop: float, take: float, direction: str) -> dict:
    """Гамма-пиннинг: куда дилерское гамма-позиционирование тянет цену.

    Логика (стандартная эвристика, помечена как таковая):
      • net gamma в точке цены > 0  -> зона ПОЛОЖИТЕЛЬНОЙ гаммы: дилеры хеджируют
        контр-трендово, цена «пиннится» к крупным страйкам (меанреверсия, вялость);
      • net gamma < 0 -> зона ОТРИЦАТЕЛЬНОЙ гаммы: хедж усиливает движение (пробои
        чище, тренды резче);
      • zero-gamma flip — граница режимов.
    magnet — крупнейшая положительная гамма-стена (магнит пиннинга).
    Возвращает состояние + направление/силу тяги + R-координаты + подсказку.
    Всё непроверяемо (позиционирование дилеров не наблюдаемо) — контекст, не сигнал.
    """
    k = np.asarray(strikes_instr, dtype=float)
    g = np.asarray(net_gex, dtype=float)
    if len(k) < 3 or not np.isfinite(price):
        return {"available": False}
    order = np.argsort(k)
    k, g = k[order], g[order]
    gmax = float(np.max(np.abs(g))) or 1.0
    net_at = float(np.interp(price, k, g))
    positive = net_at > 0
    # магнит — крупнейшая ПОЛОЖИТЕЛЬНАЯ гамма-стена (к ней тянет в + зоне)
    pos_mask = g > 0
    if pos_mask.any():
        magnet = float(k[pos_mask][int(np.argmax(g[pos_mask]))])
    else:
        magnet = float(k[int(np.argmax(g))])
    risk = abs(entry - stop) or 1.0

    def to_r(px):
        return (px - entry) / risk if direction == "long" else (entry - px) / risk

    magnet_r = to_r(magnet)
    price_r = to_r(price)
    take_r = to_r(take)
    pull_dir = 1 if magnet > price else (-1 if magnet < price else 0)  # вверх/вниз в цене
    strength = min(abs(net_at) / gmax, 1.0)
    # тянет ли магнит к тейку или к стопу (в R-координатах сделки)
    toward = "тейку" if magnet_r > price_r else "стопу"
    if positive:
        note = (f"зона + гаммы: меанреверсия/пиннинг к {magnet:.0f} "
                f"({magnet_r:+.2f}R) — тянет к {toward}; далёкий тейк труднее, "
                f"фиксируй раньше")
    else:
        note = ("зона − гаммы: движения ускоряются, пробои и тренды чище — "
                "если импульс в вашу сторону, далёкий тейк реальнее")
    return {
        "available": True,
        "zone": "positive" if positive else "negative",
        "net_at_price": net_at,
        "strength": strength,          # 0..1
        "flip": zero_flip_instr,
        "magnet": magnet,
        "magnet_r": magnet_r,
        "pull_dir": pull_dir,          # +1 цена тянется вверх, -1 вниз
        "toward": toward,              # "тейку" | "стопу"
        "note": note,
    }


def synth_chain(spot: float, sigma: float, t_years: float,
                n_strikes: int = 41, width: float = 0.12, r: float = 0.0,
                oi_skew: float = 0.0, iv_skew: float = 0.0,
                seed: int | None = None) -> dict:
    """Синтетическая цепочка Блэка–Шоулза для тестов и демо-режима.

    oi_skew > 0 — путы концентрируются ниже спота, коллы выше (для zero-gamma flip).
    iv_skew — наклон IV по страйку (equity-скью: >0 путы дороже коллов). 0 = плоско
    (по умолчанию, чтобы не влиять на тесты implied_move/density).
    """
    rng = np.random.default_rng(seed)
    ks = np.linspace(spot * (1 - width), spot * (1 + width), n_strikes)
    calls = np.array([bs_call(spot, k, t_years, sigma, r) for k in ks])
    puts = np.array([bs_put(spot, k, t_years, sigma, r) for k in ks])
    base = 1000.0 * np.exp(-0.5 * ((ks - spot) / (0.05 * spot)) ** 2)
    call_oi = base * (1.0 + oi_skew * np.clip((ks - spot) / (0.05 * spot), -2, 2))
    put_oi = base * (1.0 - oi_skew * np.clip((ks - spot) / (0.05 * spot), -2, 2))
    call_oi = np.clip(call_oi + rng.uniform(0, 30, n_strikes), 1, None)
    put_oi = np.clip(put_oi + rng.uniform(0, 30, n_strikes), 1, None)
    # IV-кривая: наклон (скью) + лёгкая улыбка; ниже спота IV выше при iv_skew>0
    m = (ks - spot) / spot
    iv = sigma * (1.0 - iv_skew * m + 0.25 * m * m)
    iv = np.clip(iv, 0.01, None)
    return {
        "strikes": ks,
        "call_mid": calls,
        "put_mid": puts,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_iv": iv,
        "put_iv": iv,
        "t_years": t_years,
        "spot": spot,
    }
