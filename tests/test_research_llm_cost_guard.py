import pytest

from seiltanzer.research_llm_cost_guard import ProviderRateGate, cost_guard_status


def test_provider_gate_rejects_burst_and_reports_retry_after():
    gate = ProviderRateGate(15.0)
    assert gate.reserve(now_mono=100.0) == 15.0
    with pytest.raises(RuntimeError, match=r"RESEARCH_LLM_RATE_LIMITED:retry_after_sec=10\.0"):
        gate.reserve(now_mono=105.0)
    assert gate.reserve(now_mono=115.0) == 15.0


def test_provider_gate_is_monotonic_and_does_not_extend_on_rejected_call():
    gate = ProviderRateGate(10.0)
    gate.reserve(now_mono=20.0)
    with pytest.raises(RuntimeError):
        gate.reserve(now_mono=29.0)
    # The rejection at 29 does not move the reservation forward.
    assert gate.reserve(now_mono=30.0) == 10.0


def test_cost_guard_is_separate_from_verdict_and_has_bounded_defaults():
    status = cost_guard_status()
    assert status["separate_from_ai_verdict_provider_guard"] is True
    assert status["cache_checked_before_provider_gate"] is True
    assert status["macro_write_burst_protection"] is True
    assert status["macro_min_ingest_interval_sec"] >= 30.0
    assert status["macro_min_provider_interval_sec"] >= 30.0
    assert status["analog_min_provider_interval_sec"] >= 5.0
