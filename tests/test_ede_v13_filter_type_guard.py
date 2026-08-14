from __future__ import annotations

from seiltanzer.edge_discovery.filters import (
    CandidateTemplate,
    ConditionTemplate,
    condition_matches,
    fit_rule,
)


def _rows(value: str = "SAME") -> list[dict]:
    return [
        {
            "captured_ts": float(index),
            "ede_features": {"cross.confirmation": value},
        }
        for index in range(40)
    ]


def test_numeric_rule_on_categorical_values_is_inapplicable_not_fatal():
    template = CandidateTemplate((ConditionTemplate(
        "cross.confirmation", "train_relative", "ABOVE_MEDIAN"),))
    assert fit_rule(template, _rows()) is None


def test_categorical_rule_still_matches_exact_value():
    template = CandidateTemplate((ConditionTemplate(
        "cross.confirmation", "categorical", "SAME"),))
    rule = fit_rule(template, _rows())
    assert rule is not None
    assert condition_matches(_rows()[0], rule.conditions[0])
