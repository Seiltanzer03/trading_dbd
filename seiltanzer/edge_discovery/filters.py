"""Frozen, bounded filter primitives for interpretable conditional discovery."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from itertools import product
from typing import Any, Iterable

import numpy as np

from seiltanzer.config import INSTRUMENTS
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import ASSET_FAMILIES, SESSIONS


QUANTILE_LABELS = ("Q0_20", "Q20_40", "Q40_60", "Q60_80", "Q80_100")
QUANTILE_FEATURES = ("rv15_over_rv60", "trend_efficiency_60")
MAX_CONDITIONS = 3


@dataclass(frozen=True)
class ConditionTemplate:
    feature_id: str
    kind: str
    state: str


@dataclass(frozen=True)
class FittedCondition:
    feature_id: str
    kind: str
    state: str
    lower: float | None = None
    upper: float | None = None
    train_cutoff_ts: float | None = None


@dataclass(frozen=True)
class CandidateTemplate:
    conditions: tuple[ConditionTemplate, ...]

    @property
    def complexity(self) -> int:
        return len(self.conditions)

    @property
    def template_id(self) -> str:
        raw = json.dumps([asdict(item) for item in self.conditions], sort_keys=True,
                         separators=(",", ":"))
        return "ede-template-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class FittedRule:
    template_id: str
    conditions: tuple[FittedCondition, ...]

    @property
    def complexity(self) -> int:
        return len(self.conditions)

    def as_dict(self) -> dict[str, Any]:
        return {"template_id": self.template_id,
                "conditions": [asdict(item) for item in self.conditions],
                "complexity": self.complexity}


def _categorical(feature: str, values: Iterable[str]) -> list[ConditionTemplate]:
    return [ConditionTemplate(feature, "categorical", str(value)) for value in values]


def _quantiles(feature: str) -> list[ConditionTemplate]:
    return [ConditionTemplate(feature, "train_quantile", label) for label in QUANTILE_LABELS]


def _templates(*groups: list[ConditionTemplate]) -> list[CandidateTemplate]:
    return [CandidateTemplate(tuple(items)) for items in product(*groups)]


def candidate_templates() -> tuple[CandidateTemplate, ...]:
    asset = _categorical("asset", tuple(INSTRUMENTS))
    family = _categorical("asset_family", ASSET_FAMILIES)
    session = _categorical("session_utc", SESSIONS)
    rv = _quantiles("rv15_over_rv60")
    trend = _quantiles("trend_efficiency_60")
    cross = _categorical("cross_confirmation", ("SAME", "OPPOSITE"))
    breadth = _categorical("family_breadth_state", ("POSITIVE", "NEGATIVE", "MIXED"))
    level1 = [CandidateTemplate((item,)) for group in
              (asset, family, session, rv, trend, cross, breadth) for item in group]
    level2 = (
        _templates(family, session) + _templates(session, rv) + _templates(family, rv)
        + _templates(rv, trend) + _templates(session, cross) + _templates(family, cross)
    )
    level3 = (
        _templates(family, session, rv) + _templates(session, rv, cross)
        + _templates(family, rv, cross)
    )
    values = level1 + level2 + level3
    if any(item.complexity > MAX_CONDITIONS for item in values):
        raise RuntimeError("EDE candidate depth exceeded")
    unique = {item.template_id: item for item in values}
    return tuple(unique[key] for key in sorted(unique))


def _feature(row: dict[str, Any], feature_id: str) -> Any:
    value = (row.get("ede_features") or {}).get(feature_id)
    if feature_id == "family_breadth_state":
        raw = (row.get("ede_features") or {}).get("family_breadth")
        if raw is None:
            return None
        if float(raw) >= 0.60:
            return "POSITIVE"
        if float(raw) <= 0.40:
            return "NEGATIVE"
        return "MIXED"
    return value


def fit_rule(template: CandidateTemplate, train: list[dict[str, Any]]) -> FittedRule | None:
    if not train:
        return None
    cutoff = max(float(row["captured_ts"]) for row in train)
    fitted: list[FittedCondition] = []
    for condition in template.conditions:
        if condition.kind in {"categorical", "sign"}:
            fitted.append(FittedCondition(
                condition.feature_id, condition.kind, condition.state,
                train_cutoff_ts=cutoff))
            continue
        values = [float(value) for row in train
                  if (value := _feature(row, condition.feature_id)) is not None
                  and math.isfinite(float(value))]
        if len(values) < 20:
            return None
        if condition.kind == "train_relative":
            if condition.state not in {"ABOVE_MEDIAN", "BELOW_MEDIAN"}:
                return None
            median = float(np.median(np.asarray(values, dtype=float)))
            fitted.append(FittedCondition(
                condition.feature_id, condition.kind, condition.state,
                lower=median, upper=median, train_cutoff_ts=cutoff))
            continue
        if condition.kind != "train_quantile":
            return None
        boundaries = np.quantile(np.asarray(values, dtype=float), [0, .2, .4, .6, .8, 1])
        index = QUANTILE_LABELS.index(condition.state)
        fitted.append(FittedCondition(
            condition.feature_id, condition.kind, condition.state,
            lower=float(boundaries[index]), upper=float(boundaries[index+1]),
            train_cutoff_ts=cutoff))
    return FittedRule(template.template_id, tuple(fitted))


def condition_matches(row: dict[str, Any], condition: FittedCondition) -> bool:
    value = _feature(row, condition.feature_id)
    if value is None:
        return False
    if condition.kind == "categorical":
        return str(value) == condition.state
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(numeric):
        return False
    if condition.kind == "sign":
        return numeric > 0 if condition.state == "POSITIVE" else numeric < 0
    if condition.kind == "train_relative":
        if condition.lower is None:
            return False
        return numeric > condition.lower if condition.state == "ABOVE_MEDIAN" else numeric <= condition.lower
    if condition.lower is None or condition.upper is None:
        return False
    final_bin = condition.state == QUANTILE_LABELS[-1]
    return numeric >= condition.lower and (numeric <= condition.upper if final_bin
                                            else numeric < condition.upper)


def rule_mask(rows: list[dict[str, Any]], rule: FittedRule) -> np.ndarray:
    return np.asarray([
        all(condition_matches(row, condition) for condition in rule.conditions)
        for row in rows
    ], dtype=bool)
