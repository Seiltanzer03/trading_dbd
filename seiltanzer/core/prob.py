"""Математическое ядро: вероятность первого достижения, калибровка, Монте-Карло.

Модель пути сделки в R-координатах (1R = размер стопа):
    dX = mu*dt + sigma*dW,  стоп = -1, тейк = +T (целевой RR).

Вероятность достижения тейка раньше стопа для процесса с дрейфом
(классический результат через функцию масштаба s(x) = exp(-2*mu*x/sigma^2)):

    P(тейк раньше стопа | X=x) = (s(x) - s(-1)) / (s(T) - s(-1))
                               = expm1(-theta*(x+1)) / expm1(-theta*(T+1)),
    theta = 2*mu/sigma^2  (вторая форма численно устойчива).

При mu -> 0 предел: P = (x+1)/(T+1).

Все функции чистые и детерминированные (МК — при фиксированном seed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# z для двустороннего 90% интервала Уилсона (ТЗ, п.3 ядра)
Z90 = 1.6448536269514722

MU_MAX = 25.0  # предохранитель бисекции: |theta| <= 2*MU_MAX при sigma=1


def first_passage_prob(x: float, mu: float, sigma: float, T: float) -> float:
    """P(достичь +T раньше, чем -1 | текущее положение x) для dX = mu dt + sigma dW."""
    if T <= 0:
        raise ValueError("T (целевой RR) должен быть > 0")
    if sigma <= 0:
        raise ValueError("sigma должна быть > 0")
    if x <= -1.0:
        return 0.0
    if x >= T:
        return 1.0
    theta = 2.0 * mu / (sigma * sigma)
    if abs(theta) < 1e-12:
        return (x + 1.0) / (T + 1.0)
    # ограничиваем показатель, чтобы не переполнить exp; при таких theta P уже 0/1
    a = max(min(-theta * (x + 1.0), 700.0), -700.0)
    b = max(min(-theta * (T + 1.0), 700.0), -700.0)
    num = math.expm1(a)
    den = math.expm1(b)
    if den == 0.0:
        return (x + 1.0) / (T + 1.0)
    p = num / den
    return min(max(p, 0.0), 1.0)


def calibrate_mu(winrate: float, T: float, sigma: float = 1.0) -> float:
    """Подбор mu бисекцией из условия first_passage_prob(0, mu, sigma, T) == winrate.

    winrate обрезается в [0.005, 0.995]: краевые значения не достижимы конечным mu.
    """
    w = min(max(winrate, 0.005), 0.995)
    lo, hi = -MU_MAX * sigma * sigma, MU_MAX * sigma * sigma
    # P монотонно растёт по mu
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if first_passage_prob(0.0, mid, sigma, T) < w:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


def wilson_interval(wins: int, n: int, z: float = Z90) -> tuple[float, float]:
    """Интервал Уилсона для биномиальной доли (по умолчанию 90%)."""
    if n <= 0:
        return (0.0, 1.0)
    if wins < 0 or wins > n:
        raise ValueError("wins должен быть в [0, n]")
    phat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    half = z / denom * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass(frozen=True)
class ProbBand:
    """Вероятность тейка с полосой неопределённости и следом вычисления."""
    p: float
    p_lo: float
    p_hi: float
    mu: float
    mu_lo: float
    mu_hi: float
    winrate: float
    wr_lo: float
    wr_hi: float
    n: int
    wins: int
    sigma_ratio: float
    T: float
    x: float


def prob_band(x: float, wins: int, n: int, T: float, sigma_ratio: float = 1.0) -> ProbBand:
    """Полный расчёт: винрейт -> Уилсон 90% -> mu_lo/mu/mu_hi -> полоса [p_lo, p_hi].

    sigma_ratio = sigma_implied / sigma_baseline (опционная поправка, п.4 ядра);
    mu калибруется при sigma=1, затем процесс идёт с sigma=sigma_ratio.
    """
    if n <= 0:
        raise ValueError("нет статистики сетапа (n=0)")
    sr = min(max(sigma_ratio, 0.25), 4.0)  # защита от вырожденной поправки
    wr = wins / n
    wr_lo, wr_hi = wilson_interval(wins, n)
    mu = calibrate_mu(wr, T)
    mu_lo = calibrate_mu(wr_lo, T)
    mu_hi = calibrate_mu(wr_hi, T)
    p = first_passage_prob(x, mu, sr, T)
    p_lo = first_passage_prob(x, mu_lo, sr, T)
    p_hi = first_passage_prob(x, mu_hi, sr, T)
    if p_lo > p_hi:
        p_lo, p_hi = p_hi, p_lo
    return ProbBand(p=p, p_lo=p_lo, p_hi=p_hi, mu=mu, mu_lo=mu_lo, mu_hi=mu_hi,
                    winrate=wr, wr_lo=wr_lo, wr_hi=wr_hi, n=n, wins=wins,
                    sigma_ratio=sr, T=T, x=x)


# ------------------------------------------------------------- Монте-Карло

@dataclass
class MCResult:
    """Результат симуляции остатка сделки.

    terminal — R в момент поглощения (-1 или T) либо на горизонте;
    max_r    — максимум пути (для лестницы фиксации и безубытка);
    hit_time — время поглощения в модельных единицах (nan, если не поглощён).
    Единица времени — абстрактное «диффузионное» время: при sigma=1 путь
    проходит расстояние ~1R за ~1 единицу.
    """
    terminal: np.ndarray
    max_r: np.ndarray
    hit_time: np.ndarray
    T: float
    dt: float
    horizon: float

    @property
    def p_take(self) -> float:
        return float(np.mean(self.terminal >= self.T - 1e-12))

    @property
    def p_stop(self) -> float:
        return float(np.mean(self.terminal <= -1.0 + 1e-12))


def simulate_remainder(r0: float, mu: float, sigma: float, T: float,
                       n_paths: int = 3000, dt: float = 0.01,
                       horizon: float = 12.0, seed: int | None = None) -> MCResult:
    """N путей dX = mu dt + sigma dW из r0 с поглощением на -1 и T (ТЗ, п.8)."""
    if not (-1.0 < r0 < T):
        r0 = min(max(r0, -1.0 + 1e-9), T - 1e-9)
    rng = np.random.default_rng(seed)
    n_steps = int(round(horizon / dt))
    r = np.full(n_paths, r0, dtype=np.float64)
    max_r = np.full(n_paths, r0, dtype=np.float64)
    terminal = np.full(n_paths, np.nan)
    hit_time = np.full(n_paths, np.nan)
    alive = np.ones(n_paths, dtype=bool)
    sdt = sigma * math.sqrt(dt)
    var_dt = sigma * sigma * dt
    for step in range(1, n_steps + 1):
        idx = np.flatnonzero(alive)
        if idx.size == 0:
            break
        prev = r[idx].copy()
        r[idx] = prev + mu * dt + sdt * rng.standard_normal(idx.size)
        max_r[idx] = np.maximum(max_r[idx], r[idx])
        sub = r[idx]
        tp = sub >= T
        sl = sub <= -1.0
        # поправка броуновского моста: вероятность пересечь барьер ВНУТРИ шага,
        # даже если обе точки шага по одну сторону (устраняет смещение дискретизации)
        inside = ~tp & ~sl
        if inside.any():
            p_lo = prev[inside]
            p_hi_ = sub[inside]
            bridge_sl = np.exp(-2.0 * (p_lo + 1.0) * (p_hi_ + 1.0) / var_dt)
            bridge_tp = np.exp(-2.0 * (T - p_lo) * (T - p_hi_) / var_dt)
            u = rng.random(inside.sum())
            hit_sl_b = u < bridge_sl
            hit_tp_b = ~hit_sl_b & (u < bridge_sl + bridge_tp)
            ii = np.flatnonzero(inside)
            sl[ii[hit_sl_b]] = True
            tp[ii[hit_tp_b]] = True
            # тейк, задетый внутри шага, учитываем и в max_r (для лестницы)
            max_r[idx[ii[hit_tp_b]]] = np.maximum(max_r[idx[ii[hit_tp_b]]], T)
        if tp.any():
            j = idx[tp]
            terminal[j] = T
            max_r[j] = np.maximum(max_r[j], T)
            hit_time[j] = step * dt
            alive[j] = False
        if sl.any():
            j = idx[sl & ~tp]
            terminal[j] = -1.0
            hit_time[j] = step * dt
            alive[j] = False
    # не поглощённые к горизонту — фиксируем текущий R
    terminal[alive] = r[alive]
    return MCResult(terminal=terminal, max_r=max_r, hit_time=hit_time,
                    T=T, dt=dt, horizon=horizon)


def ev_hold(mc: MCResult) -> float:
    """EV удержания до стопа/тейка: среднее терминального R."""
    return float(np.mean(mc.terminal))


def ev_ladder(mc: MCResult, rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
              fraction: float = 0.10, be_after: float = 1.5) -> float:
    """EV лестницы фиксации (глава 2.2): по `fraction` позиции на каждом рубеже,
    стоп в безубыток после be_after.

    Приближения (модельные, честно указываются в tooltip):
    - рубеж исполняется точно на уровне rung, если max_r пути >= rung;
    - после max_r >= be_after путь, ушедший в минус, закрывается по 0R.
    """
    rungs_arr = np.asarray(rungs, dtype=np.float64)
    crossed = mc.max_r[:, None] >= rungs_arr[None, :] - 1e-12
    realized = fraction * (crossed * rungs_arr[None, :]).sum(axis=1)
    frac_closed = fraction * crossed.sum(axis=1)
    remaining = np.maximum(0.0, 1.0 - frac_closed)
    exit_r = mc.terminal.copy()
    be_armed = mc.max_r >= be_after - 1e-12
    exit_r = np.where(be_armed & (exit_r < 0.0), 0.0, exit_r)
    return float(np.mean(realized + remaining * exit_r))


def horizon_probs(mc: MCResult, horizons=(1.0, 2.0, 4.0, 8.0)) -> list[dict]:
    """P(тейк достигнут к горизонту h) и P(стоп к h) — по временам поглощения МК."""
    out = []
    took = mc.terminal >= mc.T - 1e-12
    stopped = mc.terminal <= -1.0 + 1e-12
    n = len(mc.terminal)
    for h in horizons:
        within = mc.hit_time <= h + 1e-12
        out.append({
            "h": h,
            "p_take": float(np.sum(took & within) / n),
            "p_stop": float(np.sum(stopped & within) / n),
        })
    return out


def terminal_histogram(mc: MCResult, n_bins: int = 9) -> dict:
    """Гистограмма терминального R для доски Гальтона: n_bins корзин на [-1, T].

    Крайние корзины включают атомы поглощения (-1 и T).
    Возвращает edges (n_bins+1), counts и probs (нормированные доли).
    """
    edges = np.linspace(-1.0, mc.T, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, mc.terminal, side="right") - 1, 0, n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins)
    total = counts.sum()
    return {
        "edges": edges.tolist(),
        "counts": counts.tolist(),
        "probs": (counts / total).tolist() if total else [0.0] * n_bins,
    }


def r_coordinate(price: float, entry: float, stop: float, direction: str) -> float:
    """Текущее положение сделки в R: (цена-вход)/(вход-стоп) со знаком направления."""
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("вход и стоп совпадают")
    if direction == "long":
        return (price - entry) / risk
    if direction == "short":
        return (entry - price) / risk
    raise ValueError(f"неизвестное направление: {direction}")


def target_rr_from_levels(entry: float, stop: float, take: float, direction: str) -> float:
    """T (целевой RR) из уровней сделки."""
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("вход и стоп совпадают")
    if direction == "long":
        return (take - entry) / risk
    return (entry - take) / risk
