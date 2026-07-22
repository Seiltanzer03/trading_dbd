"""Риск-менеджмент: динамическая матрица (глава 2.1), ATR-фаза (2.9), 2α/(α+β) (2.6).

Формулы риска и целевого RR перенесены 1:1 из Excel-калькулятора
(Seiltanzer_Risk_Management.xlsx, колонки G и J).
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------- динамическая матрица рисков

# (нижняя граница %, риск %, целевой RR, режим) — глава 2.1 / Excel колонка G, J
_MATRIX = [
    (107.0, 1.50, 1.25, "Защита прибыли — консервативный рост"),
    (105.0, 1.75, 1.50, "Сбалансированный рост и стабильность"),
    (102.0, 2.00, 2.00, "Оптимальная производительность"),
    (100.0, 2.20, 1.75, "Активный рост — умеренная агрессия"),
    (97.0,  2.00, 1.50, "Амортизация просадки"),
    (95.0,  1.75, 2.20, "Режим выживания — только высокий RR"),
    (93.0,  1.50, 2.50, "Экстренное восстановление"),
    (None,  1.25, 3.00, "Критический режим — только A+ сетапы"),
]

PHASE_ADJ = {"1ph": 2.0, "2ph": 1.0, "funded": 0.0}  # R + x% по фазе проп-фирмы


@dataclass(frozen=True)
class RiskRow:
    balance_pct: float
    risk_pct: float        # риск на сделку с поправкой фазы
    base_risk_pct: float   # риск из матрицы без поправки
    target_rr: float       # целевой RR из матрицы (без ATR-поправки)
    mode: str
    phase: str


def risk_matrix_row(balance_pct: float, phase: str = "funded") -> RiskRow:
    """Строка динамической матрицы по балансу счёта в % от начального.

    Excel: G = f(Balance%) + (1ph: +2, 2ph: +1, funded: +0); J = RR(Balance%).
    Граница >107 строгая, остальные — нестрогие (>=), как в Excel-формуле.
    """
    if phase not in PHASE_ADJ:
        raise ValueError(f"неизвестная фаза: {phase}")
    b = balance_pct
    if b > 107.0:
        base, rr, mode = _MATRIX[0][1], _MATRIX[0][2], _MATRIX[0][3]
    else:
        base, rr, mode = _MATRIX[-1][1], _MATRIX[-1][2], _MATRIX[-1][3]
        for bound, risk, target, m in _MATRIX[1:-1]:
            if b >= bound:
                base, rr, mode = risk, target, m
                break
    return RiskRow(balance_pct=b, risk_pct=base + PHASE_ADJ[phase],
                   base_risk_pct=base, target_rr=rr, mode=mode, phase=phase)


# ------------------------------------------------------------- ATR-фаза 2.9

@dataclass(frozen=True)
class AtrPhase:
    ratio: float
    phase: str      # shock | impulse | flat | normal
    k: float        # k-буффер из стратегии
    rr_mult: float  # множитель целевого RR (Excel: 0.5->0.6, 0.7->0.8, 1.2->1.2, 1->1)


def classify_atr_phase(ratio: float) -> AtrPhase:
    """Фаза рынка по ratio = ATR(5)/ATR(20) на дневках (глава 2.9).

    Пороги как в Pine Script стратегии: >1.5 шок, >1.15 импульс, <0.85 флэт.
    Множитель RR — из Excel-калькулятора (колонка J).
    """
    if ratio > 1.5:
        return AtrPhase(ratio, "shock", 0.5, 0.6)
    if ratio > 1.15:
        return AtrPhase(ratio, "impulse", 1.2, 1.2)
    if ratio < 0.85:
        return AtrPhase(ratio, "flat", 0.7, 0.8)
    return AtrPhase(ratio, "normal", 1.0, 1.0)


def atr(highs, lows, closes, period: int) -> float:
    """ATR по Уайлдеру недоступен без длинной истории — используем SMA(TR, period),
    как в ta.atr-приближении на коротком окне. TR = max(H-L, |H-Cprev|, |L-Cprev|)."""
    n = len(closes)
    if n < period + 1:
        raise ValueError(f"нужно минимум {period + 1} баров, есть {n}")
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-period:]) / period


def atr_ratio(highs, lows, closes, fast: int = 5, slow: int = 20) -> float:
    """ratio = ATR(fast)/ATR(slow) на дневных барах."""
    a_slow = atr(highs, lows, closes, slow)
    if a_slow <= 0:
        raise ValueError("ATR(slow) = 0")
    return atr(highs, lows, closes, fast) / a_slow


# --------------------------------------------------------- эффективность 2.6

def setup_efficiency(alpha: int, beta: int) -> float | None:
    """Формула эффективности сетапа 2α/(α+β); None при пустой статистике."""
    if alpha + beta == 0:
        return None
    return 2.0 * alpha / (alpha + beta)


def efficiency_verdict(value: float | None) -> str:
    """Интерпретация по главе 2.6."""
    if value is None:
        return "нет статистики"
    if value > 1.0:
        return "прибыльный"
    if value >= 0.7:
        return "на грани — мониторить"
    if value > 0.4:
        return "слабый — снизить объём"
    return "провальный — пересмотреть"
