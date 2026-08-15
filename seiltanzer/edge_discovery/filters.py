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
from .registry import FEATURES
from .research_policy import interaction_feature_pairs


QUANTILE_LABELS = ("Q0_20", "Q20_40", "Q40_60", "Q60_80", "Q80_100")
QUANTILE_FEATURES = ("rv15_over_rv60", "trend_efficiency_60")
MAX_CONDITIONS = 3
MAX_TEMPLATES = 248
PROSPECTIVE_NUMERIC_FEATURES = tuple(
    item.feature_id for item in FEATURES
    if item.research_scope == "G1S" and item.training_eligibility
    and item.feature_id not in {
        "price.ret_5m", "regime.asset", "regime.asset_family",
        "regime.session_utc", "regime.trend", "regime.volatility", "regime.macro",
        "cross.confirmation",
    }
)
MACRO_REGIMES = (
    "VOL SHOCK", "TREND EXPANSION", "COMPRESSION",
    "CALM TREND", "RECOVERY", "CHOP",
)
TREND_REGIMES = ("TREND_UP", "TREND_DOWN", "CHOP")
VOLATILITY_REGIMES = ("EXPANDING", "CONTRACTING", "NORMAL")


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


def _policy_mixed_templates(
    prospective: Iterable[CandidateTemplate], eligible_feature_ids: Iterable[str],
) -> list[CandidateTemplate]:
    """Expand only feature pairs admitted by the bounded family policy."""
    by_feature: dict[str, list[ConditionTemplate]] = {}
    for item in prospective:
        if len(item.conditions) != 1:
            continue
        condition = item.conditions[0]
        by_feature.setdefault(condition.feature_id, []).append(condition)
    mixed: list[CandidateTemplate] = []
    for left_id, right_id, _policy_id in interaction_feature_pairs(
        FEATURES, eligible_feature_ids=eligible_feature_ids,
        activation="CURRENT_SELECTIVE",
    ):
        for left, right in product(by_feature.get(left_id, ()), by_feature.get(right_id, ())):
            mixed.append(CandidateTemplate((left, right)))
    return mixed


def candidate_templates(
        eligible_feature_ids: Iterable[str] | None = None) -> tuple[CandidateTemplate, ...]:
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
    base = [unique[key] for key in sorted(unique)]
    if eligible_feature_ids is None:
        return tuple(base)
    eligible = set(eligible_feature_ids)
    prospective: list[CandidateTemplate] = []
    for feature_id in PROSPECTIVE_NUMERIC_FEATURES:
        if feature_id not in eligible:
            continue
        prospective.extend((
            CandidateTemplate((ConditionTemplate(
                feature_id, "train_relative", "ABOVE_MEDIAN"),)),
            CandidateTemplate((ConditionTemplate(
                feature_id, "train_relative", "BELOW_MEDIAN"),)),
        ))
    categorical = {
        "regime.asset": tuple(INSTRUMENTS),
        "regime.asset_family": ASSET_FAMILIES,
        "regime.session_utc": SESSIONS,
        "regime.trend": TREND_REGIMES,
        "regime.volatility": VOLATILITY_REGIMES,
        "regime.macro": MACRO_REGIMES,
        "cross.confirmation": ("SAME", "OPPOSITE"),
    }
    for feature_id, states in categorical.items():
        if feature_id in eligible:
            prospective.extend(CandidateTemplate((condition,))
                               for condition in _categorical(feature_id, states))

    # Mixed-family templates are now generated from a bounded declarative
    # family policy. Adding a future RATES family therefore does not require a
    # new prefix check or custom Cartesian product in this discovery module.
    mixed = _policy_mixed_templates(prospective, eligible)

    mandatory_by_id = {item.template_id: item for item in prospective}
    if len(mandatory_by_id) > MAX_TEMPLATES:
        raise RuntimeError("available feature singles exceed bounded EDE cap")
    combined = [mandatory_by_id[key] for key in sorted(mandatory_by_id)]
    existing = set(mandatory_by_id)
    for item in sorted(mixed+base, key=lambda candidate: candidate.template_id):
        if item.template_id in existing:
            continue
        combined.append(item); existing.add(item.template_id)
        if len(combined) >= MAX_TEMPLATES:
            break
    if any(item.complexity > MAX_CONDITIONS for item in combined):
        raise RuntimeError("EDE candidate depth exceeded")
    return tuple(combined[:MAX_TEMPLATES])


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


def _finite_numeric_values(rows: list[dict[str, Any]], feature_id: str) -> list[float]:
    """Collect numeric train values without treating categorical/malformed values as zero.

    A registry/type mismatch must make that numeric rule inapplicable, not abort the
    entire discovery audit.  Categorical rules continue to use their exact strings.
    """
    values: list[float] = []
    for row in rows:
        value = _feature(row, feature_id)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            values.append(numeric)
    return values


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
        values = _finite_numeric_values(train, condition.feature_id)
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