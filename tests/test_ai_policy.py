import math

from seiltanzer.ai_policy import (
    POLICY_FRACTIONS,
    PolicyInputs,
    _run_once,
    full_correlation_summary,
    local_iv_surface,
    metric_change_summary,
    metric_coverage,
)


def _inputs(r0=0.20):
    return PolicyInputs(
        r0=r0, T=2.5, sigma_R=1.15, drift_R=-0.08,
        skew_R=0.12, term_slope=-0.15, horizon_minutes=1440,
        max_r=0.40, rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10, be_after=1.5, option_available=True,
        chain_age_sec=120, chain_status="ok", proxy_quality="reference_proxy",
        source="option_barrier_first_touch",
    )


def test_policy_distributions_cover_all_actions_and_exit_is_exact():
    inputs = _inputs()
    metrics, _ = _run_once(inputs, n_paths=2200, n_steps=180, seed=123)
    assert set(metrics) == set(POLICY_FRACTIONS)
    exit_policy = metrics["EXIT"]
    assert exit_policy["expected_final_r"] == inputs.r0
    assert exit_policy["median_final_r"] == inputs.r0
    assert exit_policy["cvar10_r"] == inputs.r0
    assert exit_policy["p_giveback_0_25_from_now"] == 0
    assert exit_policy["p_giveback_0_50_from_now"] == 0


def test_immediate_reduction_monotonically_improves_left_tail():
    metrics, _ = _run_once(_inputs(), n_paths=2600, n_steps=200, seed=456)
    order = ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"]
    cvars = [metrics[name]["cvar10_r"] for name in order]
    assert cvars == sorted(cvars)
    for name in order:
        assert 0 <= metrics[name]["p_next_rung_before_stop"] <= 1
        assert 0 <= metrics[name]["p_stop_before_next_rung"] <= 1
        assert set(metrics[name]["no_event_probability"]) == {"15m", "30m", "60m", "120m"}


def test_local_iv_uses_same_total_variance_projection_as_frontend():
    strikes = list(range(88, 114, 2))
    payload = {
        "value": [
            {"spot_at_snapshot": 100, "days": 2, "strikes": strikes,
             "ivs": [0.20] * len(strikes), "expiry": "A"},
            {"spot_at_snapshot": 100, "days": 7, "strikes": strikes,
             "ivs": [0.20] * len(strikes), "expiry": "B"},
        ],
        "spot_current": 100,
        "spot_status": "live",
        "spot_source": "test",
    }
    surface = local_iv_surface(payload)
    assert surface["available"] is True
    assert surface["frontend_formula_match"] is True
    assert [x["hours"] for x in surface["local_24h"]] == [1, 2, 4, 8, 12, 18, 24]
    assert all(math.isclose(x["atm_iv_pct"], 20.0, abs_tol=1e-8)
               for x in surface["local_24h"])
    assert all(math.isclose(x["put_call_skew_pp"], 0.0, abs_tol=1e-8)
               for x in surface["local_24h"])


def test_full_correlation_matrix_is_not_reduced_to_selected_pairs():
    payload = {
        "status": "ok", "source": "test",
        "value": {
            "assets": ["NAS", "SP500", "VIX", "VXN"],
            "matrix_short": [
                [1, .8, -.5, -.6], [.8, 1, -.7, -.4],
                [-.5, -.7, 1, .7], [-.6, -.4, .7, 1],
            ],
            "matrix_baseline": [
                [1, .7, -.4, -.5], [.7, 1, -.6, -.3],
                [-.4, -.6, 1, .6], [-.5, -.3, .6, 1],
            ],
            "matrix_delta": [
                [0, .1, -.1, -.1], [.1, 0, -.1, -.1],
                [-.1, -.1, 0, .1], [-.1, -.1, .1, 0],
            ],
        },
    }
    summary = full_correlation_summary(payload, "NAS100")
    assert summary["available"] is True
    assert len(summary["all_pairs"]) == 6
    assert summary["instrument_relevant"]


def test_every_metric_family_has_an_explicit_decision_role():
    evidence = {
        "option_barrier": {"p_take": .2}, "cone_rnd": {"median_r": 0},
        "iv_surface": {"available": True}, "live_price": {"available": True},
        "atr_regime": {"atr": {}}, "levels": {"r": {}},
        "correlation": {"available": True}, "strike_oi_gex": {"available": True},
        "gamma_context": {"available": True}, "filters": [{"key": "atr"}],
        "data_quality": {"option_available": True},
    }
    coverage = metric_coverage(evidence, {"samples": 4})
    assert coverage["summary"]["total_groups"] == 12
    assert coverage["summary"]["all_groups_have_explicit_role"] is True
    for name, row in coverage.items():
        if name != "summary":
            assert row["decision_role"]


def test_dynamic_change_summary_covers_iv_levels_and_correlation():
    old = {
        "option_barrier": {"barrier_ev_r": -.1},
        "cone_rnd": {"median_r": -.2},
        "iv_surface": {"local_24h": [{"hours": 24.0, "atm_iv_pct": 20,
                                         "put_call_skew_pp": 1.0, "curvature_pp": .5}]},
        "live_price": {"moves": {"15m": {"directional_r": -.1},
                                  "60m": {"directional_r": -.2}}},
        "atr_regime": {"atr": {"ratio": 1}, "sigma": {"ratio": 1},
                       "vrp": {"iv_rv_ratio": 1}},
        "levels": {"r": {"vwap": 0.1},
                   "volume_profile_delta": {"directional_delta_ratio": -.1}},
        "correlation": {"all_pairs": [{"pair": "NAS-VXN", "rolling": -.5}]},
        "strike_oi_gex": {"skew_delta_snapshot": -.01},
        "gamma_context": {"strength": .2, "magnet_r": -.3},
    }
    new = {
        **old,
        "option_barrier": {"barrier_ev_r": -.2},
        "iv_surface": {"local_24h": [{"hours": 24.0, "atm_iv_pct": 22,
                                         "put_call_skew_pp": 1.5, "curvature_pp": .8}]},
        "levels": {"r": {"vwap": 0.2},
                   "volume_profile_delta": {"directional_delta_ratio": -.2}},
        "correlation": {"all_pairs": [{"pair": "NAS-VXN", "rolling": -.7}]},
    }
    out = metric_change_summary(new, old)
    names = {x["metric"] for x in out["changes"]}
    assert out["available"] is True
    assert "option.barrier_ev_r" in names
    assert "iv24h.atm_iv_pct" in names
    assert "level.vwap_r" in names
    assert "correlation.NAS-VXN" in names
