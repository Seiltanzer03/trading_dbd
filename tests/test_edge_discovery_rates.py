from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from seiltanzer.edge_discovery.rates import (
    RATE_FEATURE_IDS,
    RATES_MAX_STALENESS_SECONDS,
    build_rates_states,
    conservative_daily_asof,
    parse_treasury_daily_csv,
    rates_feature_values_at_t0,
    treasury_month_url,
)


def _ts(year: int, month: int, day: int, hour: int = 0) -> float:
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _csv(rows: list[tuple[str, float, float]]) -> str:
    body = ["Date,2 Yr,10 Yr"]
    body.extend(f"{date},{two:.2f},{ten:.2f}" for date, two, ten in rows)
    return "\n".join(body) + "\n"


def test_daily_treasury_parser_and_curve_contract() -> None:
    rates = parse_treasury_daily_csv(_csv([
        ("06/01/2026", 4.05, 4.47),
        ("06/02/2026", 4.05, 4.46),
    ]))
    assert len(rates) == 2
    assert rates[0].us02y == pytest.approx(4.05)
    assert rates[0].us10y == pytest.approx(4.47)
    assert rates[0].curve_2s10s == pytest.approx(0.42)
    assert rates[0].asof == _ts(2026, 6, 2)
    assert rates[1].asof == _ts(2026, 6, 3)


def test_same_day_close_is_not_available_before_next_utc_day() -> None:
    rates = parse_treasury_daily_csv(_csv([("06/01/2026", 4.05, 4.47)]))
    states = build_rates_states(rates)
    before = rates_feature_values_at_t0(
        states, instrument="NAS100", t0=_ts(2026, 6, 1, 23), horizon=30)
    after = rates_feature_values_at_t0(
        states, instrument="NAS100", t0=_ts(2026, 6, 2, 1), horizon=30)
    assert before == {}
    assert after["rates.us10y_yield"]["value"] == pytest.approx(4.47)
    assert after["rates.us10y_yield"]["asof"] == _ts(2026, 6, 2)
    assert after["rates.us10y_yield"]["future_points_used"] is False


def test_daily_source_does_not_fabricate_intraday_velocity() -> None:
    assert not any("velocity" in feature_id for feature_id in RATE_FEATURE_IDS)
    assert not any("acceleration" in feature_id for feature_id in RATE_FEATURE_IDS)
    rates = parse_treasury_daily_csv(_csv([
        ("06/01/2026", 4.05, 4.47),
        ("06/02/2026", 4.08, 4.49),
    ]))
    states = build_rates_states(rates)
    a = rates_feature_values_at_t0(
        states, instrument="NAS100", t0=_ts(2026, 6, 3, 2), horizon=30)
    b = rates_feature_values_at_t0(
        states, instrument="NAS100", t0=_ts(2026, 6, 3, 20), horizon=30)
    assert a["rates.us10y_yield"]["value"] == b["rates.us10y_yield"]["value"]
    assert a["rates.us10y_change_1d"]["value"] == pytest.approx(0.02)
    assert a["rates.us02y_change_1d"]["value"] == pytest.approx(0.03)
    assert a["rates.us10y_yield"]["intraday_velocity_claim"] is False


def test_rates_are_stale_after_explicit_daily_context_budget() -> None:
    rates = parse_treasury_daily_csv(_csv([("06/01/2026", 4.05, 4.47)]))
    states = build_rates_states(rates)
    t0 = rates[0].asof + RATES_MAX_STALENESS_SECONDS + 1
    records = rates_feature_values_at_t0(
        states, instrument="NAS100", t0=t0, horizon=60)
    assert records["rates.us10y_yield"]["stale"] is True
    assert records["rates.us10y_yield"]["training_eligible"] is False


def test_research_join_is_not_prospective_confirmation_without_frozen_t0_capture() -> None:
    rates = parse_treasury_daily_csv(_csv([("06/01/2026", 4.05, 4.47)]))
    states = build_rates_states(rates)
    records = rates_feature_values_at_t0(
        states, instrument="NAS100", t0=_ts(2026, 6, 2, 1), horizon=15,
        confirmatory_frozen_at_t0=False)
    assert records["rates.us10y_yield"]["training_eligible"] is True
    assert records["rates.us10y_yield"]["live_available"] is False
    assert records["rates.us10y_yield"]["prospective_confirmation_eligible"] is False


def test_twenty_day_transforms_use_only_causal_daily_history() -> None:
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(20):
        date = (start + timedelta(days=index)).strftime("%m/%d/%Y")
        rows.append((date, 4.00 + index * 0.01, 4.40 + index * 0.02))
    states = build_rates_states(parse_treasury_daily_csv(_csv(rows)))
    last = states[-1].values
    assert last["rates.us10y_rolling_zscore_20d"] is not None
    assert last["rates.us02y_rolling_rank_20d"] == pytest.approx(1.0)
    assert last["rates.curve_2s10s_rolling_rank_20d"] == pytest.approx(1.0)
    assert build_rates_states(parse_treasury_daily_csv(_csv(rows[:19])))[-1].values[
        "rates.us10y_rolling_zscore_20d"] is None


def test_official_month_url_is_fixed_to_treasury_host() -> None:
    url = treasury_month_url("202606")
    assert url.startswith("https://home.treasury.gov/")
    assert "type=daily_treasury_yield_curve" in url
    with pytest.raises(ValueError):
        treasury_month_url("2026-06")


def test_conservative_asof_is_next_utc_day() -> None:
    assert conservative_daily_asof(_ts(2026, 6, 1, 17)) == _ts(2026, 6, 2)
