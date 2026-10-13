"""Original observation clock, distinct from the finalized causal cutoff."""
from __future__ import annotations

import math


def market_observation_cutoff(snapshot):
    def finite(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if math.isfinite(value) and value > 0 else None

    captured = finite(snapshot.get('captured_ts'))
    audit = (snapshot.get('policy_manager') or {}).get('input_audit') or {}
    observed = finite(audit.get('snapshot_ts', captured))
    if captured is None or observed is None or observed > captured:
        return None
    return observed
