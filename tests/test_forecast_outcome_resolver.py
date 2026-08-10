from seiltanzer.forecast_outcomes import resolve_forecast_outcome


def _forecast(**changes):
    value = {
        "id": 7, "ts": 1_000.0, "horizon_minutes": 60.0,
        "r": 0.0, "max_r": 0.0, "take_r": 2.0, "be_after_r": 1.5,
    }
    value.update(changes)
    return value


def test_take_after_forecast_horizon_is_no_touch_not_take():
    result = resolve_forecast_outcome(_forecast(), [
        {"ts": 1_000.0, "r": 0.0},
        {"ts": 4_600.0, "r": 0.5},
        {"ts": 19_000.0, "r": 2.0},
    ])
    assert result["event"] == "no_touch"
    assert result["horizon_end_ts"] == 4_600.0


def test_path_disappearing_after_manual_close_is_censored():
    result = resolve_forecast_outcome(_forecast(), [
        {"ts": 1_000.0, "r": 0.0}, {"ts": 2_200.0, "r": 0.4},
    ])
    assert result["event"] == "censored"
    assert result["resolved"] is False
    assert result["path_complete"] is False


def test_be_is_absorbing_forecast_resolution():
    result = resolve_forecast_outcome(_forecast(), [
        {"ts": 1_000.0, "r": 0.0}, {"ts": 1_600.0, "r": 1.6},
        {"ts": 2_200.0, "r": 0.0}, {"ts": 3_000.0, "r": 2.0},
    ])
    assert result["event"] == "stop_or_be"
    assert result["execution_reason"] == "breakeven"


def test_no_final_trade_record_can_enter_resolver_contract():
    forecast = _forecast(result_r=4.0, lifetime_max_r=9.0, close_notes="future")
    result = resolve_forecast_outcome(forecast, [
        {"ts": 1_000.0, "r": 0.0}, {"ts": 4_600.0, "r": 0.2},
    ])
    assert result["event"] == "no_touch"
