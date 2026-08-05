"""Stable public facade for the quantitative AI verdict v4."""
from __future__ import annotations

from . import ai_verdict_v4 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})
