"""Logical feature view with explicit availability, quality and as-of time."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True)
class FeatureValue:
    instrument: str
    t0: float
    horizon: int
    feature_id: str
    value: float | str | None
    availability: str
    quality: float | None
    asof: float | None
    stale: bool
    historical_available: bool
    live_available: bool
    training_eligible: bool
    dependency_group: str

    def __post_init__(self) -> None:
        if self.availability == "AVAILABLE" and self.value is None:
            raise ValueError("AVAILABLE feature cannot have a null value")
        if self.asof is not None and self.asof > self.t0 + 1e-6:
            raise ValueError("feature asof cannot be after T0")
        if self.stale and self.training_eligible:
            raise ValueError("stale feature cannot be training eligible")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def feature_value(*, instrument: str, t0: float, horizon: int, feature_id: str,
                  value: float | str | None, asof: float | None,
                  quality: float | None = None, stale_after_seconds: float | None = None,
                  historical_available: bool = True, live_available: bool = True,
                  training_eligible: bool = True,
                  dependency_group: str = "market") -> FeatureValue:
    finite = not isinstance(value, float) or math.isfinite(value)
    available = value is not None and finite and asof is not None and asof <= t0 + 1e-6
    stale = bool(available and stale_after_seconds is not None
                 and t0 - float(asof) > stale_after_seconds)
    return FeatureValue(
        instrument=str(instrument), t0=float(t0), horizon=int(horizon),
        feature_id=str(feature_id), value=value if available else None,
        availability="AVAILABLE" if available else "UNAVAILABLE",
        quality=quality, asof=float(asof) if asof is not None else None,
        stale=stale, historical_available=bool(historical_available),
        live_available=bool(live_available),
        training_eligible=bool(training_eligible and available and not stale),
        dependency_group=str(dependency_group),
    )


class FeatureView:
    """Small in-memory view used by offline research matrix builders."""

    def __init__(self, values: Iterable[FeatureValue]):
        self._values = tuple(values)
        self._index = {(v.instrument, v.t0, v.horizon, v.feature_id): v
                       for v in self._values}
        if len(self._index) != len(self._values):
            raise ValueError("duplicate feature-view key")

    def get(self, instrument: str, t0: float, horizon: int,
            feature_id: str) -> FeatureValue:
        return self._index.get(
            (str(instrument), float(t0), int(horizon), str(feature_id)),
            feature_value(
                instrument=instrument, t0=t0, horizon=horizon,
                feature_id=feature_id, value=None, asof=None,
                historical_available=False, live_available=False,
                training_eligible=False, dependency_group="missing"),
        )

    def records(self) -> list[dict[str, Any]]:
        return [value.as_dict() for value in self._values]


def causal_dynamics(points: Iterable[tuple[float, float]], *, window: int = 20) -> list[dict[str, Any]]:
    """Causal generic dynamics; each row uses only points with timestamp <= T0."""
    ordered = sorted((float(ts), float(value)) for ts, value in points
                     if math.isfinite(float(ts)) and math.isfinite(float(value)))
    result: list[dict[str, Any]] = []
    previous_rate: float | None = None
    for index, (ts, value) in enumerate(ordered):
        history = ordered[max(0, index-window+1):index+1]
        values = np.asarray([item[1] for item in history], dtype=float)
        delta = None
        rate = None
        acceleration = None
        if index:
            prior_ts, prior_value = ordered[index-1]
            dt = ts-prior_ts
            if dt > 0:
                delta = value-prior_value
                rate = delta/dt
                if previous_rate is not None:
                    acceleration = (rate-previous_rate)/dt
                previous_rate = rate
        median = float(np.median(values))
        mean = float(values.mean())
        std = float(values.std())
        rank = float(np.mean(values <= value))
        consistency = None
        if len(values) >= 3:
            diffs = np.diff(values)
            consistency = float(abs(np.mean(np.sign(diffs))))
        result.append({
            "t0": ts, "value": value, "delta": delta, "rate": rate,
            "acceleration": acceleration, "rolling_rank": rank,
            "rolling_zscore": (value-mean)/std if std > 1e-12 else 0.0,
            "distance_from_rolling_median": value-median,
            "direction_consistency": consistency,
            "causal_window_start_ts": history[0][0],
            "causal_window_end_ts": history[-1][0],
            "future_points_used": False,
        })
    return result
