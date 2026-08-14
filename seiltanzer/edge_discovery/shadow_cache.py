"""Small atomic cache for prospective EDE shadow summaries.

The append-only shadow ledger is research evidence and may grow without bound.
Production/AI request paths must never scan that ledger.  The low-priority
research worker materializes this compact cache; readers consume only the cache.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SHADOW_SUMMARY_CACHE_VERSION = "g1s-ede-shadow-summary-cache-v1.3.1"


def shadow_summary_cache_path(engine: Any) -> Path:
    override = os.environ.get("SEILTANZER_EDE_SHADOW_SUMMARY_CACHE")
    if override:
        return Path(override)
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research" / "ede_shadow_v13_summary.json"


def write_shadow_summary_cache(
    engine: Any, *, summary: dict[str, Any], cutoff_ts: float,
) -> dict[str, Any]:
    """Atomically persist a compact summary produced off the request path."""
    path = shadow_summary_cache_path(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract_version": SHADOW_SUMMARY_CACHE_VERSION,
        "summary_cutoff_ts": float(cutoff_ts),
        "summary": summary,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False),
        encoding="utf-8")
    temporary.replace(path)
    return {
        "path": str(path),
        "summary_cutoff_ts": float(cutoff_ts),
        "bytes": path.stat().st_size,
        "contract_version": SHADOW_SUMMARY_CACHE_VERSION,
    }


def load_shadow_summary_cache(
    engine: Any, *, cutoff_ts: float | None = None,
) -> dict[str, Any] | None:
    """Read one bounded JSON cache and reject evidence newer than the caller."""
    path = shadow_summary_cache_path(engine)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("contract_version") != SHADOW_SUMMARY_CACHE_VERSION:
        return None
    if not isinstance(payload.get("summary"), dict):
        return None
    try:
        summary_cutoff = float(payload["summary_cutoff_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    if cutoff_ts is not None and summary_cutoff > float(cutoff_ts) + 1e-6:
        return None
    return payload
