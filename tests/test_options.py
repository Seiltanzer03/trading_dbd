import math

import numpy as np
import pytest

from seiltanzer.core import options as O


SPOT, SIGMA, T_YEARS = 500.0, 0.20, 7 / 365


@pytest.fixture(scope="module")
def chain():
    return O.synth_chain(SPOT, SIGMA, T_YEARS, n_strikes=61, width=0.10,
                         oi_skew=0.6, seed=42)


class TestBlackScholes:
    def test_put_call_parity(self):
        c = O.bs_call(100, 105, 0.5, 0.3)
        p = O.bs_put(100, 105, 0.5, 0.3)
        assert c - p == pytest.approx(100 - 105, abs=1e-9)

    def test_gamma_known_value(self):
        # S=K=100, t=1, sigma=0.2, r=0: d1=0.1, gamma = pdf(0.1)/(100*0.2)
        g = O.bs_gamma(100, 100, 1.0, 0.2)
        assert g == pytest.approx(math.exp(-0.005) / math.sqrt(2 * math.pi) / 20.0, rel=1e-9)

    def test_gamma_peaks_atm(self):
        gs = [O.bs_gamma(100, k, 0.1, 0.2) for k in (80, 100, 120)]
        assert gs[1] > gs[0] and gs[1] > gs[2]


class TestImpliedMove:
    def test_recovers_sigma(self, chain):
        im = O.implied_move(chain["strikes"], chain["call_mid"], chain["put_mid"],
                            SPOT, T_YEARS)
        # straddle ~= S*sigma*sqrt(2t/pi) -> sigma_annual должна восстановиться
        assert im.sigma_annual == pytest.approx(SIGMA, rel=0.03)
        assert im.atm_strike == pytest.approx(SPOT, rel=0.01)
        assert im.move_abs == pytest.approx(SPOT * SIGMA * math.sqrt(2 * T_YEARS / math.pi),
                                            rel=0.03)

    def test_empty_chain_raises(self):
        with pytest.raises(ValueError):
            O.implied_move([], [], [], SPOT, T_YEARS)

    def test_expired_raises(self, chain):
        with pytest.raises(ValueError):
            O.implied_move(chain["strikes"], chain["call_mid"], chain["put_mid"],
                           SPOT, -0.01)


class TestRealizedVol:
    def test_constant_returns(self):
        # лог-нормальный ряд с известной дневной сигмой
        rng = np.random.default_rng(0)
        daily = 0.01
        closes = 100 * np.exp(np.cumsum(rng.normal(0, daily, 400)))
        rv = O.realized_vol(closes, trading_days=200)
        assert rv == pytest.approx(daily * math.sqrt(252), rel=0.15)

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            O.realized_vol([100, 101], trading_days=20)


class TestBLDensity:
    def test_density_integrates_to_one(self, chain):
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        assert np.trapezoid(d.density, d.strikes) == pytest.approx(1.0, abs=1e-9)
        assert (d.density >= 0).all()

    def test_recovers_lognormal_tails(self, chain):
        # P(S_T > K) по BL-плотности должна совпасть с N(d2) Блэка–Шоулза
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        for lvl_mult in (0.98, 1.0, 1.02):
            lvl = SPOT * lvl_mult
            p_above, p_below = d.tail_probs(lvl)
            d2 = (math.log(SPOT / lvl) - 0.5 * SIGMA ** 2 * T_YEARS) / (SIGMA * math.sqrt(T_YEARS))
            expected = 0.5 * math.erfc(-d2 / math.sqrt(2))
            assert p_above == pytest.approx(expected, abs=0.03), f"level {lvl}"
            assert p_above + p_below == pytest.approx(1.0, abs=1e-9)

    def test_density_peak_near_spot(self, chain):
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        peak = d.strikes[int(np.argmax(d.density))]
        assert abs(peak - SPOT) / SPOT < 0.02

    def test_few_strikes_raises(self):
        with pytest.raises(ValueError):
            O.bl_density([100, 101, 102], [5, 4, 3], T_YEARS)

    def test_tail_outside_grid(self, chain):
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        assert d.tail_probs(SPOT * 2)[0] == 0.0
        assert d.tail_probs(SPOT * 0.5)[0] == 1.0


class TestMarketRDistribution:
    def test_distribution_and_tails(self, chain):
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        # лонг: вход=спот, стоп −1% , тейк +2.5% (в цене), RR=2.5
        entry, stop, take = SPOT, SPOT * 0.99, SPOT * 1.025
        md = O.market_r_distribution(d, 1.0, entry, stop, take, "long", 2.5)
        assert len(md["probs"]) == 11
        assert abs(sum(md["probs"]) - 1.0) < 1e-6
        assert 0 <= md["p_take"] <= 1 and 0 <= md["p_stop"] <= 1
        # hit_ratio согласован с хвостами
        assert md["hit_ratio"] == pytest.approx(
            md["p_take"] / (md["p_take"] + md["p_stop"]), rel=1e-6)

    def test_long_short_symmetry(self, chain):
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        # для симметричной ~лог-нормальной плотности: лонг вверх и шорт вниз с теми
        # же |расстояниями| дают зеркальные хвосты
        long = O.market_r_distribution(d, 1.0, SPOT, SPOT * 0.99, SPOT * 1.025, "long", 2.5)
        short = O.market_r_distribution(d, 1.0, SPOT, SPOT * 1.01, SPOT * 0.975, "short", 2.5)
        assert long["p_take"] == pytest.approx(short["p_take"], abs=0.05)

    def test_scale_maps_proxy_to_instrument(self, chain):
        d = O.bl_density(chain["strikes"], chain["call_mid"], T_YEARS)
        # тот же расклад в шкале инструмента (×10) должен дать тот же hit_ratio
        scale = 10.0
        base = O.market_r_distribution(d, 1.0, SPOT, SPOT * 0.99, SPOT * 1.025, "long", 2.5)
        scaled = O.market_r_distribution(d, scale, SPOT * scale, SPOT * scale * 0.99,
                                         SPOT * scale * 1.025, "long", 2.5)
        assert base["hit_ratio"] == pytest.approx(scaled["hit_ratio"], rel=1e-6)


class TestGex:
    def test_flip_sign_with_skewed_oi(self, chain):
        g = O.gex_profile(chain["strikes"], chain["call_oi"], chain["put_oi"],
                          chain["call_iv"], chain["put_iv"], SPOT, T_YEARS)
        # oi_skew>0: путы ниже спота (минус), коллы выше (плюс) -> флип около спота
        assert g.zero_flip is not None
        assert abs(g.zero_flip - SPOT) / SPOT < 0.03
        below = g.net_gex[g.strikes < SPOT * 0.97]
        above = g.net_gex[g.strikes > SPOT * 1.03]
        assert below.sum() < 0 < above.sum()

    def test_top_levels(self, chain):
        g = O.gex_profile(chain["strikes"], chain["call_oi"], chain["put_oi"],
                          chain["call_iv"], chain["put_iv"], SPOT, T_YEARS)
        assert 1 <= len(g.top_levels) <= 3
        max_abs = max(abs(v) for v in g.net_gex)
        assert any(abs(t["gex"]) == pytest.approx(max_abs) for t in g.top_levels)

    def test_zero_oi_gives_flat_profile(self):
        ks = np.linspace(90, 110, 21)
        g = O.gex_profile(ks, np.zeros(21), np.zeros(21),
                          np.full(21, 0.2), np.full(21, 0.2), 100.0, 0.02)
        assert np.allclose(g.net_gex, 0)
        assert g.zero_flip is None and g.top_levels == []


class TestSkewTerm:
    def test_skew_sign_from_iv_curve(self):
        ks = np.linspace(90, 110, 41)
        # крутой equity-скью: IV выше ниже спота -> RR отрицательный (медвежий)
        iv = 0.2 * (1 - 1.5 * (ks - 100) / 100)
        sk = O.risk_reversal_skew(ks, iv, iv, 100.0, otm=0.04)
        assert sk is not None and sk["rr"] < 0 and sk["tilt"] == "медвежий"
        # обратный наклон -> бычий
        iv2 = 0.2 * (1 + 1.5 * (ks - 100) / 100)
        sk2 = O.risk_reversal_skew(ks, iv2, iv2, 100.0, otm=0.04)
        assert sk2["rr"] > 0 and sk2["tilt"] == "бычий"

    def test_skew_none_on_bad_iv(self):
        assert O.risk_reversal_skew([100, 101], [np.nan, np.nan],
                                    [np.nan, np.nan], 100.0) is None

    def test_term_shapes(self):
        contango = O.term_structure([(2, 0.15), (9, 0.17), (30, 0.19)])
        assert contango["shape"] == "контанго" and contango["slope"] > 0
        backw = O.term_structure([(2, 0.25), (9, 0.20), (30, 0.18)])
        assert backw["shape"] == "бэквордация" and backw["slope"] < 0
        flat = O.term_structure([(2, 0.20), (30, 0.201)])
        assert flat["shape"] == "плоская"
        assert O.term_structure([(2, 0.2)]) is None


class TestGammaPin:
    def test_magnet_is_positive_wall(self):
        ks = np.linspace(90, 110, 21)
        net = np.zeros(21)
        net[15] = 5.0      # крупная положительная гамма-стена на 105
        net[5] = -3.0
        gp = O.gamma_pin(ks, net, 100.0, 100.0, 100.0, 99.0, 102.5, "long")
        assert gp["available"] is True
        assert gp["magnet"] == pytest.approx(105.0)
        assert gp["pull_dir"] == 1 and gp["toward"] == "тейку"

    def test_zone_sign_from_net_at_price(self):
        ks = np.linspace(90, 110, 21)
        net = np.linspace(-4, 4, 21)   # отрицательная ниже 100, положительная выше
        below = O.gamma_pin(ks, net, 100.0, 96.0, 96.0, 95.0, 99.0, "long")
        above = O.gamma_pin(ks, net, 100.0, 104.0, 104.0, 103.0, 107.0, "long")
        assert below["zone"] == "negative" and above["zone"] == "positive"

    def test_degenerate_returns_unavailable(self):
        assert O.gamma_pin([100], [1], None, 100, 100, 99, 102, "long")["available"] is False
