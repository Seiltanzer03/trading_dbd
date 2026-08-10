"""Explicit as-of adapters for immutable research features."""
from __future__ import annotations

import math
from typing import Callable, Iterable, TypeVar


T = TypeVar("T")
AS_OF_CONTRACT_VERSION = "source-as-of-f1-v1"


def _timestamp(row: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def rows_as_of(rows: Iterable[dict], as_of_ts: float,
               *, timestamp_keys: tuple[str, ...] = ("ts", "timestamp", "captured_ts")) -> list[dict]:
    """Return a stable chronological slice containing no row newer than T."""
    cutoff = float(as_of_ts)
    selected = []
    for source in rows:
        row = dict(source)
        ts = _timestamp(row, timestamp_keys)
        if ts is not None and ts <= cutoff + 1e-9:
            selected.append(row)
    return sorted(selected, key=lambda row: _timestamp(row, timestamp_keys) or -math.inf)


def derive_as_of(rows: Iterable[dict], as_of_ts: float,
                 derive: Callable[[list[dict]], T],
                 *, timestamp_keys: tuple[str, ...] = ("ts", "timestamp", "captured_ts")) -> T:
    """Force rolling/derivative/regime features to operate on the safe slice."""
    return derive(rows_as_of(rows, as_of_ts, timestamp_keys=timestamp_keys))
