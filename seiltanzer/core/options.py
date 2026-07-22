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

def synth_chain(spot: float, sigma: float, t_years: float,
                n_strikes: int = 41, width: float = 0.12, r: float = 0.0,
                oi_skew: float = 0.0, seed: int | None = None) -> dict:
    """Синтетическая цепочка Блэка–Шоулза для тестов и демо-режима.

    oi_skew > 0 — путы концентрируются ниже спота, коллы выше (реалистичный кейс
    для проверки zero-gamma flip).
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
    return {
        "strikes": ks,
        "call_mid": calls,
        "put_mid": puts,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_iv": np.full(n_strikes, sigma),
        "put_iv": np.full(n_strikes, sigma),
        "t_years": t_years,
        "spot": spot,
    }
