import math
import pytest
from seiltanzer.core.wavelet import compute_wavelet_analysis


def test_wavelet_empty():
    res = compute_wavelet_analysis([])
    assert res["available"] is False
    assert res["summary"]["authority"] == "derived_price_context"
    assert res["summary"]["independent_vote"] is False


def test_wavelet_basic():
    # Генерируем 50 точек цен с 4-часовым синусоидальным циклом
    prices = [{"ts": 1000 + i * 300, "price": 100.0 * (1.0 + 0.005 * math.sin(i / 6.0))} for i in range(50)]

    res = compute_wavelet_analysis(prices)
    assert res["available"] is True
    assert len(res["period_grid_hours"]) == 10
    assert len(res["spectrogram"]) == 10
    assert len(res["dominant_ridge"]) == 49
    assert res["summary"]["authority"] == "derived_price_context"
    assert (res["summary"]["micro_energy_pct"] + res["summary"]["intraday_energy_pct"] + res["summary"]["macro_energy_pct"]) > 95.0
