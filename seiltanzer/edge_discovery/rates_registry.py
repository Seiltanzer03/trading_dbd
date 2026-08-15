"""Declarative RATES family prepared for the EDE feature registry.

PASS 4 intentionally keeps the first source as slow official Treasury context.
The definitions are separate until PASS 5 wires the family into structured EDE
outcome searches; this avoids pretending that daily public data is already an
immutable prospective T0 capture.
"""
from __future__ import annotations

from .registry import FeatureDefinition, _f


_DAILY_SOURCE = "edge_discovery.rates.TREASURY_SOURCE"
_DAILY_FREQUENCY = "official U.S. Treasury daily closing par yield"
_DAILY_ASOF = "source date is usable only from next UTC calendar day <= T0"
_DAILY_STALENESS = "stale when latest safe daily asof is older than 5 calendar days"
_DAILY_NOTES = (
    "slow macro/rates context; historical discovery may use official archived data; "
    "prospective confirmation requires a value frozen at T0; no intraday velocity claim"
)


def _daily(feature_id: str, notes: str = "") -> FeatureDefinition:
    return _f(
        feature_id,
        "RATES",
        _DAILY_SOURCE,
        frequency=_DAILY_FREQUENCY,
        asof=_DAILY_ASOF,
        staleness=_DAILY_STALENESS,
        historical="AVAILABLE",
        live="LIMITED",
        eligible=True,
        dependency="rates_daily",
        notes=f"{_DAILY_NOTES}; {notes}" if notes else _DAILY_NOTES,
    )


RATES_FEATURE_DEFINITIONS: tuple[FeatureDefinition, ...] = (
    _daily("rates.us10y_yield", "10-year CMT par yield level"),
    _daily("rates.us02y_yield", "2-year CMT par yield level"),
    _daily("rates.curve_2s10s", "US10Y minus US02Y in percentage points"),
    _daily("rates.us10y_change_1d", "change versus previous available Treasury business-day observation"),
    _daily("rates.us02y_change_1d", "change versus previous available Treasury business-day observation"),
    _daily("rates.curve_2s10s_change_1d", "2s10s change versus previous available business-day observation"),
    _daily("rates.us10y_rolling_zscore_20d", "causal 20-observation daily rolling z-score"),
    _daily("rates.us02y_rolling_zscore_20d", "causal 20-observation daily rolling z-score"),
    _daily("rates.curve_2s10s_rolling_zscore_20d", "causal 20-observation daily rolling z-score"),
    _daily("rates.us10y_rolling_rank_20d", "causal 20-observation daily percentile rank"),
    _daily("rates.us02y_rolling_rank_20d", "causal 20-observation daily percentile rank"),
    _daily("rates.curve_2s10s_rolling_rank_20d", "causal 20-observation daily percentile rank"),
)


def rates_feature_definitions() -> tuple[FeatureDefinition, ...]:
    return RATES_FEATURE_DEFINITIONS
