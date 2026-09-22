from types import SimpleNamespace

import seiltanzer.ai_policy_v2 as policy_v2
import seiltanzer.ai_policy_base as policy_base
from seiltanzer.engine import Engine


def test_main_policy_run_reuses_engine_authoritative_path_bank(monkeypatch):
    inputs = object()
    sim = object()
    distributions = {"HOLD": object(), "EXIT": object()}
    calls = []

    class Engine:
        def authoritative_execution_mc(self, received):
            calls.append(received)
            return sim

    monkeypatch.setattr(
        policy_v2._base, "build_policy_distributions",
        lambda received_sim, received_inputs: distributions,
    )
    monkeypatch.setattr(
        policy_v2._base, "policy_metrics",
        lambda distribution, received_sim, received_inputs: {
            "distribution": distribution,
        },
    )
    monkeypatch.setattr(
        policy_v2._base, "_run_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("uncached simulation should not run")
        ),
    )

    metrics, received_sim = policy_v2._main_policy_run(Engine(), inputs)

    assert calls == [inputs]
    assert received_sim is sim
    assert metrics == {
        "HOLD": {"distribution": distributions["HOLD"]},
        "EXIT": {"distribution": distributions["EXIT"]},
    }


def test_main_policy_run_keeps_standalone_fallback(monkeypatch):
    expected = ({"HOLD": {}}, object())
    monkeypatch.setattr(policy_v2._base, "_run_once", lambda *a, **k: expected)

    assert policy_v2._main_policy_run(SimpleNamespace(), object()) is expected


def _cache_inputs(*, r0=1.2345):
    return SimpleNamespace(
        r0=r0, T=2.5, sigma_R=0.8, drift_R=0.01, skew_R=-0.1,
        term_slope=0.0, horizon_minutes=600.0, max_r=r0,
        rungs=(1.0, 1.5, 2.0), rung_fraction=0.1, be_after=1.5,
        option_available=True,
    )


def test_live_clock_seeds_exact_policy_cache_without_stale_bucket_reuse(monkeypatch):
    engine = object.__new__(Engine)
    engine._execution_mc_cache_key = None
    engine._execution_mc_cache = None
    engine._live_clock_mc_cache_key = None
    engine._live_clock_mc_cache = None
    generated = []

    def fake_simulate(inputs, **kwargs):
        result = object()
        generated.append((inputs.r0, result, kwargs))
        return result

    monkeypatch.setattr(policy_base, "simulate_option_paths", fake_simulate)
    first_inputs = _cache_inputs(r0=1.2345)
    first = engine.authoritative_execution_mc(first_inputs, purpose="live_clock")
    assert engine.authoritative_execution_mc(first_inputs) is first
    assert len(generated) == 1

    # Same two-decimal clock bucket, but the exact policy input changed.
    second_inputs = _cache_inputs(r0=1.2346)
    assert engine.authoritative_execution_mc(second_inputs, purpose="live_clock") is first
    second = engine.authoritative_execution_mc(second_inputs)
    assert second is not first
    assert len(generated) == 2
