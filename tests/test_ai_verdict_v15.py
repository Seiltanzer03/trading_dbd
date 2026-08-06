from seiltanzer.ai_verdict_v15 import _clarify_geometry


def test_clarifies_nearest_rung_and_final_take():
    text = (
        "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —\n"
        "Один набор из 6500 путей. Рубеж раньше стопа: 67.3% (4372/6500). "
        "Стоп раньше рубежа: 32.7% (2128/6500)."
    )
    manager = {
        "scenario_geometry": {"next_rung_r": 1.0},
        "inputs": {"T": 8.86},
        "evidence": {
            "option_barrier": {
                "p_take": 0.16,
                "p_stop": 0.82,
                "no_touch": 0.023,
            }
        },
    }

    out = _clarify_geometry(text, manager)

    assert "Ближайшая ступень +1.000R раньше стопа" in out
    assert "Стоп раньше ближайшей ступени" in out
    assert "финальный тейк +8.860R раньше стопа: 16.0%" in out
    assert "стоп раньше финального тейка: 82.0%" in out
    assert "ни один барьер не достигнут: 2.3%" in out


def test_labels_final_take_when_it_is_the_next_target():
    text = "Рубеж раньше стопа: 40.0%. Стоп раньше рубежа: 60.0%."
    manager = {
        "scenario_geometry": {"next_rung_r": 2.0},
        "inputs": {"T": 2.0},
        "evidence": {"option_barrier": {"p_take": 0.4}},
    }

    out = _clarify_geometry(text, manager)

    assert "Финальный тейк +2.000R раньше стопа: 40.0%." in out
    assert "Стоп раньше финального тейка: 60.0%." in out
    assert out.count("Финальный тейк") == 1
