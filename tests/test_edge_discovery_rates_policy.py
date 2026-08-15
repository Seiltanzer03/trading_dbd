from __future__ import annotations

from seiltanzer.edge_discovery.rates_registry import RATES_FEATURE_DEFINITIONS
from seiltanzer.edge_discovery.research_policy import feature_research_policy


def test_daily_rates_never_gain_synthetic_intraday_transforms() -> None:
    for feature in RATES_FEATURE_DEFINITIONS:
        policy = feature_research_policy(feature)
        assert policy.allowed_transforms == ("RAW",)
        assert "VELOCITY" not in policy.allowed_transforms
        assert "ACCELERATION" not in policy.allowed_transforms
