"""Small shared contract for canonical instrument quotes used by UI and AI."""
from __future__ import annotations

import math
from typing import Any


def canonical_instrument_code(value: Any) -> str:
    """Normalize display/feed aliases without changing the internal symbol set."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def price_quote_available(row: Any) -> bool:
    """A stale or explicitly unavailable quote cannot own current AI geometry."""
    if not isinstance(row, dict):
        return False
    try:
        value = float(row.get("value"))
    except (TypeError, ValueError):
        return False
    return bool(
        math.isfinite(value)
        and value > 0.0
        and row.get("status") not in {None, "no_data"}
        and row.get("fresh") is not False
    )


def price_quote_reason(row: Any) -> str | None:
    if not isinstance(row, dict):
        return "canonical_quote_missing"
    if row.get("fresh") is False:
        return "canonical_quote_stale"
    if row.get("error"):
        return str(row["error"])
    if row.get("status") in {None, "no_data"}:
        return "canonical_quote_no_data"
    if not price_quote_available(row):
        return "canonical_quote_invalid"
    return None
