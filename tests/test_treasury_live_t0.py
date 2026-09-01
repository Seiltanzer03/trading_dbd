from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from seiltanzer.edge_discovery.rates import (
    RATE_FEATURE_IDS,
    RATES_MAX_STALENESS_SECONDS,
    TREASURY_SOURCE,
    RatesState,
    TreasuryDailyRate,
    build_rates_states,
)
from seiltanzer.treasury_t0_context import (
    TREASURY_FETCH_STALE_SECONDS,
    TreasuryLiveRuntime,
    build_treasury_t0_context,
    freeze_treasury_context,
    project_treasury_ai_context,
)


def _ts(year: int, month: int, day: int, hour: int = 0) -> float:
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _observation(day: int, two: float, ten: float) -> TreasuryDailyRate:
    source_ts = _ts(2026, 8, day)
    return TreasuryDailyRate(
        source_date=f"08/{day:02d}/2026",
        source_date_ts=source_ts,
        asof=source_ts + 24 * 60 * 60,
        us02y=two,
        us10y=ten,
    )


def _states() -> tuple[RatesState, ...]:
    observations = [
        _observation(day, 4.00 + index * 0.01, 4.40 + index * 0.02)
        for index, day in enumerate(range(1, 22))
    ]
    return tuple(build_rates_states(observations))


def test_official_curve_is_frozen_with_separate_publication_and_fetch_times() -> None:
    states = _states()
    fetched_at = _ts(2026, 8, 22, 1)
    captured_ts = fetched_at + 60

    context = build_treasury_t0_context(
        states, fetched_at=fetched_at, captured_ts=captured_ts)

    assert context["available"] is True
    assert context["source"] == TREASURY_SOURCE
    assert context["source_url"].startswith("https://home.treasury.gov/")
    assert context["observation_date"] == "08/21/2026"
    assert context["publication_date"] == "2026-08-22"
    assert context["published_at"] == _ts(2026, 8, 22)
    assert context["fetched_at"] == fetched_at
    assert context["asof"] == fetched_at
    assert context["asof"] <= context["captured_ts"]
    assert context["stale_after"] > context["captured_ts"]
    assert context["context_vector"]["rates.us02y_yield"] == pytest.approx(4.20)
    assert context["context_vector"]["rates.us10y_yield"] == pytest.approx(4.80)
    assert context["context_vector"]["rates.curve_2s10s"] == pytest.approx(0.60)
    assert context["current_ml_feature_vector_reads_treasury_context"] is False
    assert context["search_space_changed"] is False
    assert context["production_authority"] is False
    assert context["yahoo_rates_allowed"] is False
    assert context["synthetic_fallback_allowed"] is False


@pytest.mark.parametrize(
    ("captured_ts", "expected_reason"),
    [
        (
            _ts(2026, 8, 22, 1) + RATES_MAX_STALENESS_SECONDS + 1,
            "TREASURY_SOURCE_DATE_STALE",
        ),
        (
            _ts(2026, 8, 22, 1) + TREASURY_FETCH_STALE_SECONDS + 1,
            "TREASURY_FETCH_STALE",
        ),
    ],
)
def test_stale_source_or_fetch_is_fail_closed(
    captured_ts: float, expected_reason: str,
) -> None:
    fetched_at = _ts(2026, 8, 22, 1)
    context = build_treasury_t0_context(
        _states(), fetched_at=fetched_at, captured_ts=captured_ts)

    assert context["available"] is False
    assert expected_reason in context["reason"]
    assert context["stale"] is True
    assert context["context_vector"] == {}
    assert all(item["value"] is None for item in context["features"].values())


def test_fetch_after_t0_is_never_retrospectively_backfilled() -> None:
    t0 = _ts(2026, 8, 22, 1)
    context = build_treasury_t0_context(
        _states(), fetched_at=t0 + 1, captured_ts=t0)

    assert context["available"] is False
    assert context["reason"] == "TREASURY_FETCH_AFTER_T0"
    assert context["context_vector"] == {}
    assert context["historical_backfill_allowed"] is False


def test_partial_base_curve_is_missing_not_numeric_zero() -> None:
    state = RatesState(
        source_date="08/21/2026",
        asof=_ts(2026, 8, 22),
        values={
            feature_id: (
                None if feature_id == "rates.us02y_yield" else 4.5
            )
            for feature_id in RATE_FEATURE_IDS
        },
    )
    context = build_treasury_t0_context(
        [state],
        fetched_at=_ts(2026, 8, 22, 1),
        captured_ts=_ts(2026, 8, 22, 2),
    )

    assert context["available"] is False
    assert context["reason"] == "INCOMPLETE_OFFICIAL_TREASURY_BASE_CURVE"
    assert "rates.us02y_yield" in context["missing_feature_ids"]
    assert context["context_vector"] == {}
    assert all(item["value"] is None for item in context["features"].values())


def test_non_official_state_is_rejected_even_with_plausible_numbers() -> None:
    state = RatesState(
        source_date="08/21/2026",
        asof=_ts(2026, 8, 22),
        values={feature_id: 4.5 for feature_id in RATE_FEATURE_IDS},
        source="Yahoo Finance Treasury proxy",
    )
    context = build_treasury_t0_context(
        [state],
        fetched_at=_ts(2026, 8, 22, 1),
        captured_ts=_ts(2026, 8, 22, 2),
    )

    assert context["available"] is False
    assert context["reason"] == "NO_OFFICIAL_TREASURY_CURVE_AT_T0"
    assert context["context_vector"] == {}


def test_runtime_refresh_uses_existing_official_history_adapter_and_t0_is_network_free() -> None:
    calls: list[tuple[float, float]] = []
    now = _ts(2026, 8, 22, 1)

    def fetch(start_ts: float, end_ts: float) -> list[TreasuryDailyRate]:
        calls.append((start_ts, end_ts))
        return [
            _observation(day, 4.00 + index * 0.01, 4.40 + index * 0.02)
            for index, day in enumerate(range(1, 22))
        ]

    runtime = TreasuryLiveRuntime(fetch_rates=fetch, now=lambda: now)
    assert runtime.refresh() is True
    calls_after_refresh = list(calls)

    context = runtime.context_at(now + 10)

    assert context["available"] is True
    assert calls == calls_after_refresh
    status = runtime.status()
    assert status["observation_count"] == 21
    assert status["request_path_network_fetch"] is False
    assert status["yahoo_rates_allowed"] is False


def test_collector_failure_preserves_only_bounded_last_success() -> None:
    now = _ts(2026, 8, 22, 1)
    attempts = 0

    def fetch(_start_ts: float, _end_ts: float) -> list[TreasuryDailyRate]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return [
                _observation(day, 4.00 + index * 0.01, 4.40 + index * 0.02)
                for index, day in enumerate(range(1, 22))
            ]
        raise TimeoutError("official source unavailable")

    runtime = TreasuryLiveRuntime(fetch_rates=fetch, now=lambda: now)
    assert runtime.refresh() is True
    assert runtime.refresh(now=now + 60) is False

    bounded = runtime.context_at(now + 120)
    expired = runtime.context_at(now + TREASURY_FETCH_STALE_SECONDS + 1)
    assert bounded["available"] is True
    assert "TimeoutError" in bounded["collector_last_error"]
    assert expired["available"] is False
    assert "TREASURY_FETCH_STALE" in expired["reason"]
    assert expired["context_vector"] == {}


def test_freezer_is_additive_and_explicit_when_runtime_is_missing() -> None:
    original = {"price": {"value": 100.0}}
    frozen = freeze_treasury_context(original, None, _ts(2026, 8, 22))

    assert frozen is not original
    assert original == {"price": {"value": 100.0}}
    assert frozen["treasury_context_v1"]["available"] is False
    assert frozen["treasury_context_v1"]["reason"] == "TREASURY_RUNTIME_NOT_INSTALLED"


def test_daily_context_never_claims_intraday_dynamics() -> None:
    context = build_treasury_t0_context(
        _states(),
        fetched_at=_ts(2026, 8, 22, 1),
        captured_ts=_ts(2026, 8, 22, 2),
    )

    assert context["intraday_velocity_claim"] is False
    assert not any("velocity" in feature_id for feature_id in context["context_vector"])
    assert not any("acceleration" in feature_id for feature_id in context["context_vector"])


def test_ai_projection_accepts_only_still_fresh_official_frozen_t0() -> None:
    fetched_at = _ts(2026, 8, 22, 1)
    observation_t0 = fetched_at + 60
    frozen = build_treasury_t0_context(
        _states(), fetched_at=fetched_at, captured_ts=observation_t0)

    projected = project_treasury_ai_context(
        frozen,
        observation_t0=observation_t0,
        snapshot_ts=observation_t0 + 60,
    )

    assert projected["available"] is True
    assert projected["context_vector"]["rates.us02y_yield"] == pytest.approx(4.20)
    assert projected["analysis_role"] == "EXPLANATION_ONLY_SLOW_DAILY_CONTEXT"
    assert projected["confidence_modifier"] == 0.0
    assert projected["production_authority"] is False
    assert projected["current_ml_feature_vector_reads_treasury_context"] is False


def test_ai_projection_revalidates_staleness_and_source_fail_closed() -> None:
    fetched_at = _ts(2026, 8, 22, 1)
    observation_t0 = fetched_at + 60
    frozen = build_treasury_t0_context(
        _states(), fetched_at=fetched_at, captured_ts=observation_t0)

    stale = project_treasury_ai_context(
        frozen,
        observation_t0=observation_t0,
        snapshot_ts=float(frozen["stale_after"]) + 1,
    )
    forged = dict(frozen)
    forged["source_url"] = "https://finance.yahoo.com/quote/%5ETNX"
    untrusted = project_treasury_ai_context(
        forged,
        observation_t0=observation_t0,
        snapshot_ts=observation_t0 + 60,
    )

    assert stale["available"] is False
    assert stale["reason"] == "TREASURY_CONTEXT_STALE_AT_AI_SNAPSHOT"
    assert stale["context_vector"] == {}
    assert untrusted["available"] is False
    assert untrusted["reason"] == "UNVERIFIED_TREASURY_SOURCE"
    assert untrusted["context_vector"] == {}
