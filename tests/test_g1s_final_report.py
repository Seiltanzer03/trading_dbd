from __future__ import annotations

from seiltanzer.g1_short_horizon_final_report import (
    _combine_economic,
    _combine_statistical,
    _overall,
)


def test_statistical_verdict_requires_continuous_and_selected_probability_yes():
    assert _combine_statistical("YES", "YES", "YES") == "YES"
    assert _combine_statistical("YES", "YES", "INSUFFICIENT") == "INSUFFICIENT"
    assert _combine_statistical("YES", "NO", "YES") == "NO"
    assert _combine_statistical("NO", "YES", "YES") == "YES"
    assert _combine_statistical("YES", "YES", "NO") == "NO"


def test_economic_verdict_fails_closed_on_real_world_contradiction():
    assert _combine_economic("NOT_WORSE", "NOT_WORSE") == "NOT_WORSE"
    assert _combine_economic("INSUFFICIENT", "NOT_WORSE") == "INSUFFICIENT"
    assert _combine_economic("CONTRADICTED", "NOT_WORSE") == "CONTRADICTED"
    assert _combine_economic("NOT_WORSE", "CONTRADICTED") == "CONTRADICTED"


def test_overall_edge_verdict_never_promotes_statistical_only_result():
    assert _overall("YES", "NOT_WORSE") == "YES"
    assert _overall("YES", "INSUFFICIENT") == "INSUFFICIENT"
    assert _overall("YES", "CONTRADICTED") == "NO"
    assert _overall("NO", "NOT_WORSE") == "NO"
