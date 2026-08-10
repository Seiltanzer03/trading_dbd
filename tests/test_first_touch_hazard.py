from seiltanzer.core.prob import rn_cone


def _cone(r):
    return rn_cone(
        r, 0.8, 2.0, horizon_years=1 / 365, terminal_hit=0.5,
        n_paths=20_000, seed=123,
    )


def test_local_stop_hazard_rises_near_stop_and_take_hazard_rises_near_take():
    middle = _cone(0.0)["first_touch_hazard"]["next_window"]
    near_stop = _cone(-0.95)["first_touch_hazard"]["next_window"]
    near_take = _cone(1.95)["first_touch_hazard"]["next_window"]
    assert near_stop["h_stop"] > middle["h_stop"]
    assert near_stop["h_stop"] > near_stop["h_take"]
    assert near_take["h_take"] > middle["h_take"]
    assert near_take["h_take"] > near_take["h_stop"]


def test_take_and_stop_conditional_medians_are_not_the_mixed_compatibility_clock():
    cone = _cone(-0.7)
    assert cone["take_first_touch_median_years"] is not None
    assert cone["stop_first_touch_median_years"] is not None
    assert cone["median_years"] is not None
    assert cone["take_first_touch_median_years"] != cone["stop_first_touch_median_years"]
    assert cone["first_touch_hazard"]["family"] == "option_distribution"
    assert cone["first_touch_hazard"]["independent_vote"] is False

