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


def _competing_bridge_probs(lower: np.ndarray,
                            upper: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Нормирует вероятности двух барьеров, когда их сумма численно > 1.

    Формула броуновского моста для каждого барьера выводится отдельно. На очень
    широком шаге обе оценки могут суммарно превысить единицу; без нормировки
    порядок проверок искусственно отдавал лишнюю массу нижнему барьеру.
    """
    total = lower + upper
    scale = np.maximum(total, 1.0)
    return lower / scale, upper / scale


def _centered_skew_noise(z: np.ndarray, skew: float) -> np.ndarray:
    """Асимметричный шум с E=0 и Var=1.

    Отрицательные и положительные полуоси масштабируются по-разному, чтобы
    сохранить skew хвостов. Простое разное масштабирование имеет ненулевое
    среднее, которое на мелкой сетке превращается в огромный ложный дрейф.
    Здесь этот сдвиг удалён аналитически, а дисперсия нормирована обратно к 1.
    """
    k = float(min(max(skew, -0.45), 0.45))
    if abs(k) < 1e-15:
        return z
    raw = np.where(z < 0.0, (1.0 + k) * z, (1.0 - k) * z)
    mean = -2.0 * k / math.sqrt(2.0 * math.pi)
    variance = 1.0 + k * k * (1.0 - 2.0 / math.pi)
    return (raw - mean) / math.sqrt(variance)


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
            bridge_sl, bridge_tp = _competing_bridge_probs(
                bridge_sl, bridge_tp)
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


def forward_distribution(r0: float, theta: float, sigma_R: float, T: float,
                         n_paths: int = 4000, horizon: float = 1.0,
                         dt: float = 0.005, seed: int | None = None) -> MCResult:
    """Распределение R к горизонту, где полный разброс за горизонт = sigma_R.

    В отличие от `simulate_remainder` (гонит до поглощения на длинном горизонте
    и даёт почти бинарный исход −1/T), здесь горизонт нормирован в 1, а
    `sigma_R` = ожидаемое СКО хода сделки в R-единицах за этот горизонт
    (берётся из implied move опционов / индекса волы). Поэтому распределение:
      • сдвигается с текущим r0 (движение цены),
      • раздувается при росте волатильности и сжимается при её падении,
      • копит атомы на −1 и T (поглощённые пути), но между ними есть плотность.

    theta = 2*mu/sigma^2 фиксирует «край» из винрейта (как в first-passage),
    так что доля зелёных сходится к модельной вероятности.
    """
    sigma = max(sigma_R, 1e-6)
    mu = 0.5 * theta * sigma * sigma
    return simulate_remainder(r0, mu, sigma, T, n_paths=n_paths, dt=dt,
                              horizon=horizon, seed=seed)


def cone_surface(r0: float, mu: float, sigma: float, T: float,
                 n_slices: int = 12, n_bins: int = 11, n_paths: int = 4000,
                 horizon: float = 16.0, dt: float = 0.01,
                 seed: int | None = None) -> dict:
    """Эволюция распределения R во времени — «конус неопределённости» first-passage.

    Симулирует пути dX = mu dt + sigma dW из r0 с поглощением на -1 (стоп) и T (тейк);
    на `n_slices` равных отсечках времени в (0, horizon] снимает срез:
      • density[j][b] — доля ВСЕХ путей, ещё ЖИВЫХ (не поглощённых) и попавших в
        корзину b оси R на момент t_j. Сумма по b = доля живых = 1 − уже дошедшие
        до барьеров. Так поверхность «теряет массу» стенам по мере времени;
      • p_take_by_t[j] / p_stop_by_t[j] — накопленная доля путей, уже поглощённых
        тейком/стопом к t_j (кривые «дошло до барьера», ползущие вверх по стенам).
    mu/sigma — те же, что калибруют P(тейк раньше стопа), поэтому к концу горизонта
    p_take_by_t → P(модели), p_stop_by_t → 1−P: дальние стены конуса и есть шапка-P.
    Ось времени — модельное «время до развязки» (нормируется на фронте в 0..100%),
    не календарь: календарного времени поглощения бесплатные данные не дают.
    """
    if T <= 0:
        raise ValueError("T (целевой RR) должен быть > 0")
    sigma = max(sigma, 1e-6)
    r0 = min(max(r0, -1.0 + 1e-9), T - 1e-9)
    rng = np.random.default_rng(seed)
    n_steps = max(int(round(horizon / dt)), n_slices)
    # строго возрастающие отсечки шагов (ровно n_slices штук), СГУЩЁННЫЕ к началу:
    # время поглощения право-скошено, поэтому ранняя динамика (разлёт + первый
    # слив к барьерам) интереснее — степенное распределение отсечек её показывает.
    checkpoints: list[int] = []
    for j in range(n_slices):
        frac = ((j + 1) / n_slices) ** 1.8
        c = int(round(frac * n_steps))
        c = max(c, (checkpoints[-1] + 1) if checkpoints else 1)
        checkpoints.append(min(c, n_steps))
    edges = np.linspace(-1.0, T, n_bins + 1)

    r = np.full(n_paths, r0, dtype=np.float64)
    alive = np.ones(n_paths, dtype=bool)
    took = np.zeros(n_paths, dtype=bool)
    stopped = np.zeros(n_paths, dtype=bool)
    sdt = sigma * math.sqrt(dt)
    var_dt = sigma * sigma * dt

    times, density, p_take_by_t, p_stop_by_t = [], [], [], []
    cp = 0
    for step in range(1, n_steps + 1):
        idx = np.flatnonzero(alive)
        if idx.size:
            prev = r[idx].copy()
            r[idx] = prev + mu * dt + sdt * rng.standard_normal(idx.size)
            sub = r[idx]
            tp = sub >= T
            sl = sub <= -1.0
            inside = ~tp & ~sl
            if inside.any():
                p_lo = prev[inside]
                p_hi = sub[inside]
                bridge_sl = np.exp(-2.0 * (p_lo + 1.0) * (p_hi + 1.0) / var_dt)
                bridge_tp = np.exp(-2.0 * (T - p_lo) * (T - p_hi) / var_dt)
                bridge_sl, bridge_tp = _competing_bridge_probs(
                    bridge_sl, bridge_tp)
                u = rng.random(inside.sum())
                hit_sl = u < bridge_sl
                hit_tp = ~hit_sl & (u < bridge_sl + bridge_tp)
                ii = np.flatnonzero(inside)
                sl[ii[hit_sl]] = True
                tp[ii[hit_tp]] = True
            if tp.any():
                j = idx[tp]
                took[j] = True
                alive[j] = False
            if sl.any():
                j = idx[sl & ~tp]
                stopped[j] = True
                alive[j] = False
        while cp < n_slices and step == checkpoints[cp]:
            counts = np.zeros(n_bins)
            alive_r = r[alive]
            if alive_r.size:
                bi = np.clip(np.searchsorted(edges, alive_r, side="right") - 1,
                             0, n_bins - 1)
                counts = np.bincount(bi, minlength=n_bins).astype(float)
            times.append(step / n_steps * horizon)
            density.append((counts / n_paths).tolist())
            p_take_by_t.append(float(took.sum()) / n_paths)
            p_stop_by_t.append(float(stopped.sum()) / n_paths)
            cp += 1

    return {
        "times": times,
        "edges": edges.tolist(),
        "density": density,             # n_slices × n_bins, доля живых путей
        "p_take_by_t": p_take_by_t,
        "p_stop_by_t": p_stop_by_t,
        "p_take": float(took.sum()) / n_paths,
        "p_stop": float(stopped.sum()) / n_paths,
        "r0": float(r0), "T": float(T),
    }


def _rebin_uniform(vals, n_out: int) -> list[float]:
    """Пересчёт равномерно-корзинного массива в меньшее число корзин (сохраняет массу)."""
    vals = np.asarray(vals, dtype=float)
    n_in = len(vals)
    out = np.zeros(n_out)
    for i, v in enumerate(vals):
        lo = i / n_in * n_out
        hi = (i + 1) / n_in * n_out
        b = int(lo)
        while b < hi and b < n_out:
            out[b] += v * (min(hi, b + 1) - max(lo, b))
            b += 1
    return out.tolist()


def _smooth_hist(vals, passes: int = 2) -> np.ndarray:
    """Лёгкое KDE-подобное сглаживание корзин с точным сохранением массы."""
    out = np.asarray(vals, dtype=float)
    total = float(out.sum())
    if out.size < 3 or total <= 0:
        return out
    for _ in range(max(0, passes)):
        padded = np.pad(out, (1, 1), mode="edge")
        out = np.convolve(padded, np.array([0.25, 0.5, 0.25]), mode="valid")
    smoothed = float(out.sum())
    return out * (total / smoothed) if smoothed > 0 else out


def _hist_quantile(probs: list[float], edges: np.ndarray, q: float) -> float | None:
    """Квантиль кусочно-равномерной гистограммы."""
    p = np.asarray(probs, dtype=float)
    total = float(p.sum())
    if total <= 0:
        return None
    target = min(max(float(q), 0.0), 1.0) * total
    cum = 0.0
    for i, mass in enumerate(p):
        nxt = cum + float(mass)
        if target <= nxt and mass > 0:
            frac = (target - cum) / float(mass)
            return float(edges[i] + frac * (edges[i + 1] - edges[i]))
        cum = nxt
    return float(edges[-1])


def rn_cone(r0: float, sigma_R: float, T: float, drift_R: float = 0.0,
            skew: float = 0.0, term_slope: float = 0.0,
            horizon_years: float | None = None,
            terminal_hit: float | None = None,
            ou_theta: float = 0.0, ou_mu: float = 0.0,
            heston_kappa: float = 0.0, heston_theta: float = 1.0,
            heston_xi: float = 0.0, heston_rho: float = 0.0,
            n_slices: int = 14, n_bins: int = 31, n_paths: int = 6000,
            n_steps: int = 400, seed: int | None = None) -> dict:
    """RISK-NEUTRAL конус: эволюция распределения R под волатильность — НЕ винрейт.

    Диффузия dX = drift_R·dt + σ·dW в R-координатах, где полный разброс за горизонт
    равен sigma_R. Снос drift_R — из скью (может быть 0). Барьеры −1 (стоп) и T (тейк)
    поглощают. Это стандартный «вероятностный конус» деска: где рынок (по своей воле)
    ждёт цену во времени.

    Ось времени АДАПТИВНАЯ и РЕАЛЬНАЯ: `horizon_years` = реальная длительность
    горизонта (выводится из σ и расстояния до барьеров — у скальпа это минуты, у
    свинга дни). Доля горизонта → годы/минуты на фронте. Возвращает:
      density[n_slices][n_bins] — плотность живых путей (гладкая, для 3D-поверхности),
      times_frac / times_days — время срезов,
      p_take_by_t / p_stop_by_t — накопленная вероятность дойти к моменту t (стены),
      p_take / p_stop — реально пересекли барьеры к горизонту;
      hit_ratio — option-implied P(тейк first-touch до текущего горизонта);
      p_take/p_stop/unresolved — отдельные touch/touch/no-touch на горизонте,
      median_days — медианное время развязки,
      slice_probs / slice_edges — полная, не обрезанная барьерами RND доски.
    """
    if T <= 0:
        raise ValueError("T должен быть > 0")
    sigma_R = max(float(sigma_R), 1e-3)
    r0 = min(max(r0, -1.0 + 1e-9), T - 1e-9)
    rng = np.random.default_rng(seed)
    dt = 1.0 / n_steps
    # АСИММЕТРИЯ по скью: сторона «страха» шире. skew>0 → шаги вниз (−R) крупнее
    # (толще нижний хвост, выше P стопа в лонге) — как реальная улыбка волы, а не
    # симметричный Блэк-Шоулз. Хвосты не занижаются.
    skew = float(min(max(skew, -0.45), 0.45))
    sqdt = math.sqrt(dt)
    var_dt = sigma_R * sigma_R * dt
    # TERM-STRUCTURE (форвардная вола): вола «дышит» по срокам, а не растёт как √t
    # линейно. term_slope>0 (контанго) — дальняя вола выше: конус узкий рано,
    # раскрывается позже; term_slope<0 (бэквордация) — движение фронтально, конус
    # раздувается сразу и выполаживается. RMS-нормировка сохраняет ПОЛНУЮ дисперсию
    # за горизонт (калибровка p_take/p_stop не меняется) — перераспределяется ТОЛЬКО
    # во времени. term_slope=0 → множитель ≡ 1 (полная обратная совместимость).
    term_slope = float(min(max(term_slope, -0.6), 0.6))
    tau_step = (np.arange(1, n_steps + 1) - 0.5) / n_steps          # центр шага
    g = np.clip(1.0 + term_slope * (2.0 * tau_step - 1.0), 0.35, 1.9)
    fstep = g / math.sqrt(float(np.mean(g * g)))                    # ∫f²dτ = 1
    edges = np.linspace(-1.0, T, n_bins + 1)
    # При implied move в несколько R линейная сетка пропускала всю раннюю форму:
    # уже на первом срезе большая часть путей была поглощена, поэтому 3D выглядел
    # как редкие иглы. Сгущаем реальные временные срезы у начала; times_frac ниже
    # сохраняет честные подписи времени.
    nearest_barrier = max(min(r0 + 1.0, T - r0), 0.10)
    urgency = sigma_R / nearest_barrier
    time_power = min(4.0, max(1.0, 1.0 + math.log10(max(urgency, 1.0))))
    checkpoints: list[int] = []
    for j in range(n_slices):
        frac = ((j + 1) / n_slices) ** time_power
        c = int(round(frac * n_steps))
        checkpoints.append(max(c, (checkpoints[-1] + 1) if checkpoints else 1))
    checkpoints[-1] = n_steps

    r = np.full(n_paths, r0, dtype=np.float64)
    v = np.ones(n_paths, dtype=np.float64)  # Heston variance process (starts at 1.0)
    alive = np.ones(n_paths, dtype=bool)
    took = np.zeros(n_paths, dtype=bool)
    stopped = np.zeros(n_paths, dtype=bool)
    hit_time = np.full(n_paths, np.nan)

    times, density, p_take_by_t, p_stop_by_t = [], [], [], []
    cp = 0
    for step in range(1, n_steps + 1):
        fi = fstep[step - 1]                    # форвардный множитель волы на шаге
        var_i = var_dt * fi * fi
        idx = np.flatnonzero(alive)
        if idx.size:
            prev = r[idx].copy()
            noise = rng.standard_normal(idx.size)
            
            # Heston Stochastic Volatility
            v_step = 1.0
            if heston_kappa > 0.0 or heston_xi > 0.0:
                noise_v = rng.standard_normal(idx.size)
                if heston_rho != 0.0:
                    noise_v = heston_rho * noise + math.sqrt(1.0 - heston_rho**2) * noise_v
                v_prev = v[idx].copy()
                # Full Truncation scheme for Heston
                v_pos = np.maximum(v_prev, 0.0)
                v_next = v_prev + heston_kappa * (heston_theta - v_pos) * dt + heston_xi * np.sqrt(v_pos) * sqdt * noise_v
                v[idx] = np.maximum(v_next, 0.0)
                v_step = v_pos

            # Skew меняет форму хвостов, но не должен сам создавать направленный
            # снос: E[step_r]=0 и Var[step_r]=sigma_R²·dt·fi².
            skew_noise = _centered_skew_noise(noise, skew)
            step_r = sigma_R * sqdt * fi * skew_noise * np.sqrt(v_step)
            
            # Ornstein-Uhlenbeck (Mean Reversion) + Base Drift
            drift_step = (drift_R + ou_theta * (ou_mu - prev)) * dt
            
            r[idx] = prev + drift_step + step_r
            sub = r[idx]
            tp = sub >= T
            sl = sub <= -1.0
            inside = ~tp & ~sl
            if inside.any():
                plo, phi = prev[inside], sub[inside]
                bsl = np.exp(-2.0 * (plo + 1.0) * (phi + 1.0) / var_i)
                btp = np.exp(-2.0 * (T - plo) * (T - phi) / var_i)
                bsl, btp = _competing_bridge_probs(bsl, btp)
                u = rng.random(inside.sum())
                hs = u < bsl
                ht = ~hs & (u < bsl + btp)
                ii = np.flatnonzero(inside)
                sl[ii[hs]] = True
                tp[ii[ht]] = True
            t_now = step * dt
            if tp.any():
                j = idx[tp]; took[j] = True; alive[j] = False; hit_time[j] = t_now
            if sl.any():
                j = idx[sl & ~tp]; stopped[j] = True; alive[j] = False; hit_time[j] = t_now
        while cp < n_slices and step == checkpoints[cp]:
            counts = np.zeros(n_bins)
            ar = r[alive]
            if ar.size:
                bi = np.clip(np.searchsorted(edges, ar, side="right") - 1, 0, n_bins - 1)
                counts = np.bincount(bi, minlength=n_bins).astype(float)
            times.append(step / n_steps)
            counts = _smooth_hist(counts, passes=2)
            density.append((counts / n_paths).tolist())
            p_take_by_t.append(float(took.sum()) / n_paths)
            p_stop_by_t.append(float(stopped.sum()) / n_paths)
            cp += 1

    p_take = float(took.sum()) / n_paths
    p_stop = float(stopped.sum()) / n_paths
    resolved = ~np.isnan(hit_time)
    med_frac = float(np.median(hit_time[resolved])) if resolved.any() else None
    # На конечном опционном горизонте есть ТРИ исхода: take-touch, stop-touch и
    # no-touch.  Terminal BL tail-ratio нельзя использовать как first-passage:
    # P(S_T за тейком) / tail mass не говорит, какой барьер был первым, а его
    # дополнение тем более не является P(stop). Headline P — только фактическая
    # first-touch масса к заданному опционному горизонту.
    resolved = min(max(p_take + p_stop, 0.0), 1.0)
    unresolved = 1.0 - resolved
    if terminal_hit is not None and math.isfinite(terminal_hit):
        # Headline probability is the actual competing first-touch probability
        # inside the option horizon. It comes from barrier MC driven by IV/skew/
        # term/forward/gamma. No distance-ratio and no normalization of no-touch.
        hit_ratio = p_take
        hit_source = "option_barrier_first_touch"
        stop_anchored = p_stop
    else:
        hit_ratio = None
        hit_source = "no_option_anchor"
        stop_anchored = None
    p_take_anchored_by_t = list(p_take_by_t)  # compatibility: walls are touches
    p_stop_anchored_by_t = list(p_stop_by_t)
    unresolved_anchored_by_t = [
        max(0.0, 1.0 - pt - ps)
        for pt, ps in zip(p_take_by_t, p_stop_by_t)
    ]

    # Доска показывает ПОЛНУЮ option-implied RND на горизонте, а не killed
    # density внутри [-1,T]. Иначе при цене рядом с тейком колокол обрезался о
    # барьер и выглядел как полураспределение. Барьеры теперь лежат ВНУТРИ оси.
    alive_frac = [sum(d) for d in density]
    if ou_theta > 1e-9:
        decay = math.exp(-ou_theta)
        free_mean = (ou_mu + (r0 - ou_mu) * decay
                     + drift_R * (1.0 - decay) / ou_theta)
        free_sigma = sigma_R * math.sqrt(
            max((1.0 - math.exp(-2.0 * ou_theta)) / (2.0 * ou_theta), 1e-4))
    else:
        free_mean = r0 + drift_R
        free_sigma = sigma_R
    free_sigma = max(float(free_sigma), 0.08)
    margin = 0.14 * (T + 1.0)
    # 4.25σ оставляет визуальный ноль на обоих краях: оранжевая огибающая
    # естественно сходит к оси и не выглядит срезанной последней колонкой.
    bell_lo = min(-1.0 - margin, free_mean - 4.25 * free_sigma)
    bell_hi = max(T + margin, free_mean + 4.25 * free_sigma)
    bell_edges = np.linspace(bell_lo, bell_hi, 12)
    free_rng = np.random.default_rng((seed if seed is not None else 0x5A5A) ^ 0x6C8E)
    free_noise = _centered_skew_noise(free_rng.standard_normal(max(n_paths, 12000)), skew)
    free_terminal = free_mean + free_sigma * free_noise
    bell = np.histogram(free_terminal, bins=bell_edges)[0].astype(float)
    bell = _smooth_hist(bell, passes=1).tolist()
    tot = sum(bell) or 1.0
    bell = [b / tot for b in bell]
    mode_idx = int(np.argmax(bell))
    mode_r = float((bell_edges[mode_idx] + bell_edges[mode_idx + 1]) / 2.0)

    out = {
        "r0": float(r0), "T": float(T), "sigma_R": sigma_R, "drift_R": float(drift_R),
        "skew": float(skew), "term_slope": float(term_slope),
        "edges": edges.tolist(),
        "density": density,
        "times_frac": times,
        "p_take_by_t": p_take_by_t, "p_stop_by_t": p_stop_by_t,
        "p_take_anchored_by_t": p_take_anchored_by_t,
        "p_stop_anchored_by_t": p_stop_anchored_by_t,
        "unresolved_anchored_by_t": unresolved_anchored_by_t,
        "p_take": p_take, "p_stop": p_stop, "hit_ratio": hit_ratio,
        "p_take_anchored": hit_ratio,
        "p_stop_anchored": stop_anchored,
        "unresolved_anchored": unresolved,
        "unresolved": unresolved, "hit_source": hit_source,
        "probability_available": hit_ratio is not None,
        "scenario_only": hit_ratio is None,
        "time_scale_power": time_power,
        "slice_probs": bell,
        "slice_edges": bell_edges.tolist(),
        "surviving_mass": alive_frac,
        "n_paths": int(n_paths),
        "slice_alive": alive_frac[-1],
        "slice_time_frac": 1.0,
        "slice_mode_r": mode_r,
        "slice_p10_r": _hist_quantile(bell, bell_edges, 0.10),
        "slice_median_r": _hist_quantile(bell, bell_edges, 0.50),
        "slice_p90_r": _hist_quantile(bell, bell_edges, 0.90),
        "lattice_kind": "full_horizon_option_rnd",
        "touch_take_horizon": p_take,
        "touch_stop_horizon": p_stop,
        "no_touch_horizon": unresolved,
    }
    if horizon_years and horizon_years > 0:
        out["horizon_years"] = float(horizon_years)
        out["times_years"] = [t * horizon_years for t in times]
        out["median_years"] = med_frac * horizon_years if med_frac is not None else None
    else:
        out["horizon_years"] = None
        out["times_years"] = None
        out["median_years"] = None
    return out


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
