import math

import numpy as np
import pytest

from seiltanzer.core import prob as P


class TestFirstPassage:
    def test_boundaries(self):
        assert P.first_passage_prob(-1.0, 0.3, 1.0, 2.5) == 0.0
        assert P.first_passage_prob(2.5, 0.3, 1.0, 2.5) == 1.0

    def test_zero_drift_limit(self):
        # mu=0: P = (x+1)/(T+1)
        assert P.first_passage_prob(0.0, 0.0, 1.0, 2.5) == pytest.approx(1 / 3.5)
        assert P.first_passage_prob(1.0, 0.0, 1.0, 3.0) == pytest.approx(0.5)
        # непрерывность в mu=0
        assert P.first_passage_prob(0.0, 1e-9, 1.0, 2.5) == pytest.approx(1 / 3.5, abs=1e-6)

    def test_monotonic_in_x_and_mu(self):
        xs = np.linspace(-0.99, 2.49, 50)
        ps = [P.first_passage_prob(x, 0.4, 1.0, 2.5) for x in xs]
        assert all(b >= a for a, b in zip(ps, ps[1:]))
        mus = np.linspace(-2, 2, 41)
        pm = [P.first_passage_prob(0.0, m, 1.0, 2.5) for m in mus]
        assert all(b >= a for a, b in zip(pm, pm[1:]))

    def test_extreme_drift_saturates(self):
        assert P.first_passage_prob(0.0, 50.0, 1.0, 2.5) == pytest.approx(1.0)
        assert P.first_passage_prob(0.0, -50.0, 1.0, 2.5) == pytest.approx(0.0)

    def test_sigma_scaling_equivalent_to_theta(self):
        # P зависит от mu/sigma^2: (mu, sigma) и (mu/4, sigma/2) дают одно и то же
        p1 = P.first_passage_prob(0.3, 0.8, 1.0, 2.5)
        p2 = P.first_passage_prob(0.3, 0.2, 0.5, 2.5)
        assert p1 == pytest.approx(p2, rel=1e-9)


class TestCalibration:
    @pytest.mark.parametrize("wr", [0.3, 0.5, 0.68, 0.77, 0.87])
    @pytest.mark.parametrize("T", [1.5, 2.5, 2.7])
    def test_round_trip(self, wr, T):
        mu = P.calibrate_mu(wr, T)
        assert P.first_passage_prob(0.0, mu, 1.0, T) == pytest.approx(wr, abs=1e-6)

    def test_neutral_winrate_gives_zero_mu(self):
        # при winrate = 1/(T+1) дрейф должен быть ~0
        T = 2.5
        mu = P.calibrate_mu(1 / (T + 1), T)
        assert abs(mu) < 1e-6

    def test_clamps_extremes(self):
        assert math.isfinite(P.calibrate_mu(1.0, 2.5))
        assert math.isfinite(P.calibrate_mu(0.0, 2.5))


class TestWilson:
    def test_known_value(self):
        # 8 из 10, z=1.6449: интервал вокруг 0.8
        lo, hi = P.wilson_interval(8, 10)
        assert lo == pytest.approx(0.5408, abs=2e-3)
        assert hi == pytest.approx(0.9314, abs=2e-3)

    def test_bounds_and_degenerate(self):
        lo, hi = P.wilson_interval(0, 5)
        assert lo == 0.0 and 0 < hi < 1
        lo, hi = P.wilson_interval(5, 5)
        assert hi == 1.0 and 0 < lo < 1
        assert P.wilson_interval(0, 0) == (0.0, 1.0)

    def test_narrows_with_n(self):
        w1 = P.wilson_interval(7, 10)
        w2 = P.wilson_interval(70, 100)
        assert (w2[1] - w2[0]) < (w1[1] - w1[0])


class TestProbBand:
    def test_band_contains_p_and_ordered(self):
        b = P.prob_band(x=0.0, wins=14, n=16, T=2.5)
        assert 0 < b.p_lo <= b.p <= b.p_hi < 1
        assert b.p == pytest.approx(14 / 16, abs=1e-6)  # x=0, ratio=1 -> p == winrate

    def test_small_sample_wide_band(self):
        small = P.prob_band(0.0, wins=6, n=7, T=2.5)
        big = P.prob_band(0.0, wins=60, n=70, T=2.5)
        assert (small.p_hi - small.p_lo) > (big.p_hi - big.p_lo)

    def test_sigma_ratio_effect_direction(self):
        # при mu>0 сужение волы (ratio<1) повышает безлимитную по времени P;
        # честная потеря вероятности на горизонте проверяется в МК-тесте
        base = P.prob_band(0.5, wins=14, n=16, T=2.5, sigma_ratio=1.0)
        tight = P.prob_band(0.5, wins=14, n=16, T=2.5, sigma_ratio=0.6)
        assert tight.p > base.p


class TestMonteCarlo:
    def test_matches_analytic(self):
        T, wr = 2.5, 0.7
        mu = P.calibrate_mu(wr, T)
        mc = P.simulate_remainder(0.0, mu, 1.0, T, n_paths=8000, dt=0.005,
                                  horizon=40.0, seed=42)
        assert mc.p_take == pytest.approx(wr, abs=0.02)
        assert mc.p_take + mc.p_stop == pytest.approx(1.0, abs=0.01)

    def test_horizon_probs_monotone(self):
        mu = P.calibrate_mu(0.7, 2.5)
        mc = P.simulate_remainder(0.0, mu, 1.0, 2.5, n_paths=3000, seed=1)
        hp = P.horizon_probs(mc, horizons=(1, 2, 4, 8))
        takes = [h["p_take"] for h in hp]
        assert all(b >= a for a, b in zip(takes, takes[1:]))

    def test_compressed_vol_lowers_horizon_prob(self):
        # опционная поправка: в сжатом рынке далёкий тейк на горизонте теряет вероятность
        mu = P.calibrate_mu(0.7, 2.5)
        wide = P.simulate_remainder(0.0, mu, 1.0, 2.5, n_paths=6000, horizon=2.0, seed=7)
        tight = P.simulate_remainder(0.0, mu, 0.5, 2.5, n_paths=6000, horizon=2.0, seed=7)
        assert P.horizon_probs(tight, (1.0,))[0]["p_take"] < \
               P.horizon_probs(wide, (1.0,))[0]["p_take"]

    def test_ev_hold_definition(self):
        mu = P.calibrate_mu(0.7, 2.5)
        mc = P.simulate_remainder(0.0, mu, 1.0, 2.5, n_paths=8000, horizon=40.0, seed=3)
        # EV = p*T - (1-p) для полностью поглощённых путей
        expected = mc.p_take * 2.5 - mc.p_stop * 1.0
        assert P.ev_hold(mc) == pytest.approx(expected, abs=0.02)

    def test_ladder_ev_closed_form_when_all_win(self):
        # огромный дрейф: все пути доходят до T, лестница = 0.1*сумма рубежей + 0.4*T
        mc = P.simulate_remainder(0.0, 50.0, 1.0, 2.5, n_paths=500, seed=5)
        assert mc.p_take == 1.0
        rungs = (1.0, 1.25, 1.5, 1.75, 2.0, 2.2)
        expected = 0.1 * sum(rungs) + (1 - 0.6) * 2.5
        assert P.ev_ladder(mc) == pytest.approx(expected, abs=1e-9)

    def test_ladder_breakeven_protects(self):
        # для путей, дошедших до 1.5R и вернувшихся, выход остатка = 0, а не -1
        mu = P.calibrate_mu(0.55, 2.5)
        mc = P.simulate_remainder(0.0, mu, 1.0, 2.5, n_paths=6000, horizon=40.0, seed=9)
        lost_after_be = (mc.max_r >= 1.5) & (mc.terminal <= -1.0 + 1e-9)
        assert lost_after_be.any()  # такие пути существуют
        ev_l = P.ev_ladder(mc)
        # без безубытка лестница была бы хуже
        rungs = np.array([1.0, 1.25, 1.5, 1.75, 2.0, 2.2])
        crossed = mc.max_r[:, None] >= rungs[None, :] - 1e-12
        realized = 0.1 * (crossed * rungs).sum(axis=1)
        remaining = 1 - 0.1 * crossed.sum(axis=1)
        ev_no_be = float(np.mean(realized + remaining * mc.terminal))
        assert ev_l > ev_no_be

    def test_terminal_histogram(self):
        mu = P.calibrate_mu(0.7, 2.5)
        mc = P.simulate_remainder(0.0, mu, 1.0, 2.5, n_paths=4000, seed=11)
        h = P.terminal_histogram(mc, n_bins=9)
        assert len(h["edges"]) == 10 and len(h["probs"]) == 9
        assert sum(h["probs"]) == pytest.approx(1.0)
        # атомы поглощения в крайних корзинах
        assert h["probs"][0] > 0.1 and h["probs"][-1] > 0.3


class TestForwardDistribution:
    def test_not_binary_has_interior(self):
        # умеренный разброс -> заметная масса в середине (не только на барьерах)
        theta = 2 * P.calibrate_mu(0.68, 2.5)
        fwd = P.forward_distribution(0.0, theta, sigma_R=0.7, T=2.5,
                                     n_paths=6000, seed=1)
        h = P.terminal_histogram(fwd, n_bins=11)
        assert sum(h["probs"][1:-1]) > 0.5

    def test_wider_with_volatility(self):
        # выше sigma_R -> больше поглощений на барьерах (шире разброс)
        theta = 2 * P.calibrate_mu(0.68, 2.5)
        narrow = P.forward_distribution(0.0, theta, 0.4, 2.5, n_paths=6000, seed=2)
        wide = P.forward_distribution(0.0, theta, 1.4, 2.5, n_paths=6000, seed=2)
        hn = P.terminal_histogram(narrow, 11)
        hw = P.terminal_histogram(wide, 11)
        assert (hw["probs"][0] + hw["probs"][-1]) > (hn["probs"][0] + hn["probs"][-1])

    def test_shifts_with_position(self):
        # сдвиг r0 вправо -> масса распределения смещается к тейку
        theta = 2 * P.calibrate_mu(0.68, 2.5)
        left = P.forward_distribution(-0.3, theta, 0.7, 2.5, n_paths=6000, seed=3)
        right = P.forward_distribution(1.2, theta, 0.7, 2.5, n_paths=6000, seed=3)
        assert float(np.mean(right.terminal)) > float(np.mean(left.terminal))


class TestConeSurface:
    def test_walls_converge_to_first_passage(self):
        # дальние стены конуса = P(тейк)/P(стоп) первого достижения
        mu = P.calibrate_mu(0.60, 2.5)
        p = P.first_passage_prob(0.0, mu, 1.0, 2.5)
        c = P.cone_surface(0.0, mu, 1.0, 2.5, horizon=16.0, dt=16.0 / 1500,
                           n_paths=6000, seed=11)
        assert c["p_take"] == pytest.approx(p, abs=0.03)
        assert c["p_take"] + c["p_stop"] == pytest.approx(1.0, abs=0.03)

    def test_shape_and_monotone_walls(self):
        mu = P.calibrate_mu(0.58, 2.5)
        c = P.cone_surface(0.0, mu, 1.0, 2.5, seed=3)
        assert len(c["density"]) == 12 and len(c["density"][0]) == 11
        assert len(c["edges"]) == 12
        # накопленные кривые «дошло до барьера» не убывают
        assert all(b >= a - 1e-9 for a, b in zip(c["p_take_by_t"], c["p_take_by_t"][1:]))
        assert all(b >= a - 1e-9 for a, b in zip(c["p_stop_by_t"], c["p_stop_by_t"][1:]))
        # живая масса убывает со временем (сливается к стенам)
        alive = [sum(row) for row in c["density"]]
        assert alive[0] > alive[-1]

    def test_positive_drift_moves_crowd_up(self):
        # сильный перевес -> к тейку сливается больше, чем к стопу
        mu = P.calibrate_mu(0.75, 2.5)
        c = P.cone_surface(0.0, mu, 1.0, 2.5, seed=5)
        assert c["p_take"] > c["p_stop"]


class TestRnCone:
    def test_bell_not_degenerate(self):
        # risk-neutral колокол — нормальное распределение, не свалено в стоп
        c = P.rn_cone(0.0, 3.5, 3.0, drift_R=0.0, horizon_years=5 / 365, seed=1)
        bell = c["slice_probs"]
        assert len(bell) == 11
        assert abs(sum(bell) - 1.0) < 1e-6
        # пик не на самом краю (стоп/тейк), масса в середине
        peak = max(range(11), key=lambda b: bell[b])
        assert 1 <= peak <= 9
        assert sum(bell[3:8]) > 0.4

    def test_hit_ratio_matches_first_passage(self):
        # рыночный hit = аналитическая first-passage (drift 0 -> (r+1)/(T+1))
        c = P.rn_cone(0.0, 3.0, 3.0, drift_R=0.0, horizon_years=1 / 365, seed=2)
        assert c["hit_ratio"] == pytest.approx(1.0 / 4.0, abs=1e-6)

    def test_walls_monotone_and_realtime(self):
        c = P.rn_cone(0.2, 3.0, 2.5, drift_R=0.0, horizon_years=2 / 365, seed=3)
        assert all(b >= a - 1e-9 for a, b in zip(c["p_take_by_t"], c["p_take_by_t"][1:]))
        assert all(b >= a - 1e-9 for a, b in zip(c["p_stop_by_t"], c["p_stop_by_t"][1:]))
        assert c["horizon_years"] == pytest.approx(2 / 365)
        assert c["times_years"][-1] == pytest.approx(2 / 365, rel=0.01)

    def test_drift_tilts_toward_take(self):
        # положительный снос -> выше P дойти до тейка
        up = P.rn_cone(0.0, 3.0, 3.0, drift_R=0.2, horizon_years=1 / 52, seed=4)
        flat = P.rn_cone(0.0, 3.0, 3.0, drift_R=0.0, horizon_years=1 / 52, seed=4)
        assert up["hit_ratio"] > flat["hit_ratio"]


class TestRCoordinate:
    def test_long_short(self):
        assert P.r_coordinate(101, 100, 99, "long") == pytest.approx(1.0)
        assert P.r_coordinate(99, 100, 99, "long") == pytest.approx(-1.0)
        assert P.r_coordinate(99, 100, 101, "short") == pytest.approx(1.0)
        assert P.r_coordinate(101, 100, 101, "short") == pytest.approx(-1.0)

    def test_target_rr(self):
        assert P.target_rr_from_levels(100, 99, 102.5, "long") == pytest.approx(2.5)
        assert P.target_rr_from_levels(100, 101, 97.5, "short") == pytest.approx(2.5)

    def test_degenerate_raises(self):
        with pytest.raises(ValueError):
            P.r_coordinate(100, 100, 100, "long")
