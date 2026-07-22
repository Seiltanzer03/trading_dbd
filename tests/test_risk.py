import pytest

from seiltanzer.core import risk as R


class TestRiskMatrix:
    # контрольные точки из Excel-формулы (колонка G) и главы 2.1
    @pytest.mark.parametrize("bal,risk,rr", [
        (110.0, 1.50, 1.25),
        (106.0, 1.75, 1.50),
        (103.0, 2.00, 2.00),
        (101.0, 2.20, 1.75),
        (98.0,  2.00, 1.50),
        (96.0,  1.75, 2.20),
        (94.0,  1.50, 2.50),
        (90.0,  1.25, 3.00),
    ])
    def test_matrix_rows(self, bal, risk, rr):
        row = R.risk_matrix_row(bal, "funded")
        assert row.risk_pct == pytest.approx(risk)
        assert row.target_rr == pytest.approx(rr)

    def test_boundaries_match_excel(self):
        # Excel: >107 строго; >=105, >=102, >=100, >=97, >=95, иначе 93-95; <93 отдельно
        assert R.risk_matrix_row(107.0).base_risk_pct == 1.75   # не >107
        assert R.risk_matrix_row(105.0).base_risk_pct == 1.75
        assert R.risk_matrix_row(102.0).base_risk_pct == 2.00
        assert R.risk_matrix_row(100.0).base_risk_pct == 2.20
        assert R.risk_matrix_row(97.0).base_risk_pct == 2.00
        assert R.risk_matrix_row(95.0).base_risk_pct == 1.75
        assert R.risk_matrix_row(93.0).base_risk_pct == 1.50
        assert R.risk_matrix_row(92.99).base_risk_pct == 1.25

    def test_phase_adjustment(self):
        # 1ph: R+2, 2ph: R+1, funded: R
        assert R.risk_matrix_row(101.0, "1ph").risk_pct == pytest.approx(4.2)
        assert R.risk_matrix_row(101.0, "2ph").risk_pct == pytest.approx(3.2)
        assert R.risk_matrix_row(101.0, "funded").risk_pct == pytest.approx(2.2)

    def test_unknown_phase(self):
        with pytest.raises(ValueError):
            R.risk_matrix_row(100.0, "3ph")


class TestAtrPhase:
    @pytest.mark.parametrize("ratio,phase,k,mult", [
        (1.6, "shock", 0.5, 0.6),
        (1.51, "shock", 0.5, 0.6),
        (1.3, "impulse", 1.2, 1.2),
        (1.16, "impulse", 1.2, 1.2),
        (1.0, "normal", 1.0, 1.0),
        (0.85, "normal", 1.0, 1.0),
        (0.84, "flat", 0.7, 0.8),
        (0.5, "flat", 0.7, 0.8),
    ])
    def test_classification(self, ratio, phase, k, mult):
        p = R.classify_atr_phase(ratio)
        assert (p.phase, p.k, p.rr_mult) == (phase, k, mult)

    def test_atr_and_ratio(self):
        highs = [11, 12, 13, 12, 11, 12, 13, 14, 13, 12] * 3
        lows = [9, 10, 11, 10, 9, 10, 11, 12, 11, 10] * 3
        closes = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11] * 3
        a5 = R.atr(highs, lows, closes, 5)
        a20 = R.atr(highs, lows, closes, 20)
        assert a5 > 0 and a20 > 0
        assert R.atr_ratio(highs, lows, closes) == pytest.approx(a5 / a20)

    def test_atr_needs_enough_bars(self):
        with pytest.raises(ValueError):
            R.atr([1, 2], [0, 1], [1, 2], 5)


class TestEfficiency:
    def test_formula(self):
        assert R.setup_efficiency(7, 2) == pytest.approx(2 * 7 / 9)
        assert R.setup_efficiency(0, 0) is None

    def test_verdicts(self):
        assert R.efficiency_verdict(1.5) == "прибыльный"
        assert R.efficiency_verdict(0.8) == "на грани — мониторить"
        assert R.efficiency_verdict(0.5) == "слабый — снизить объём"
        assert R.efficiency_verdict(0.3) == "провальный — пересмотреть"
        assert R.efficiency_verdict(None) == "нет статистики"
