"""v1.3 adapter refinement for causal baseline-row recovery.

The base prospective adapter already recomputes 60m price/regime context from
retained passive bars that were demonstrably available when the T0 observation
was recorded. This refinement applies the same causal source contract to the
short returns and realized-volatility fields required by
GLOBAL_RET5_PERSISTENCE when older immutable T0 rows predate the V3 frozen
price block.

No option history is reconstructed and no future bar may participate.
"""
from __future__ import annotations

import bisect
import math
from typing import Any

from .prospective import ProspectiveFeatureAdapter as _BaseAdapter, _finite


class ProspectiveFeatureAdapter(_BaseAdapter):
    """Recover missing baseline price fields from already-retained causal bars."""

    def _recomputed_price_context(self, row: dict[str, Any]) -> dict[str, Any]:
        # Preserve the existing 60m causal price/regime calculation when its
        # stricter window contract is satisfiable. Baseline ret5/ret15 recovery
        # is deliberately independent of that 60m gate: a short return should
        # not be discarded merely because a full hour is not retained.
        #
        # Five horizon observations commonly share the same instrument/T0 and
        # capture record. The base adapter cache therefore returns the exact
        # subclass-populated causal block on horizons 2..5. Reuse it rather than
        # scanning the same retained bars again.
        result = dict(super()._recomputed_price_context(row))
        if (result.get("_meta") or {}).get("baseline_price_backfill") is True:
            return result

        instrument = str(row["instrument"])
        t0 = float(row["captured_ts"])
        capture_recorded_ts = _finite(row.get("created_ts")) or t0
        all_bars = self._causal_bars.get(instrument, [])
        eligible = [
            bar for bar in all_bars
            if float(bar["bar_end_ts"]) <= t0 + 1e-6
            and float(bar.get("created_ts") or bar["bar_end_ts"])
            <= capture_recorded_ts + 1e-6
            and (_finite(bar.get("close")) or 0.0) > 0.0
        ]
        if len(eligible) < 2:
            return result

        ends = [float(bar["bar_end_ts"]) for bar in eligible]
        end_index = bisect.bisect_right(ends, t0 + 1e-6) - 1
        if end_index < 1 or t0 - ends[end_index] > 5 * 60.0:
            return result
        end_ts = ends[end_index]
        end_price = float(eligible[end_index]["close"])

        def return_over(seconds: float) -> float | None:
            anchor_ts = end_ts - seconds
            index = bisect.bisect_right(
                ends, anchor_ts + 1e-6, hi=end_index) - 1
            if index < 0:
                return None
            # Reject an anchor that is materially older than one retained bar;
            # otherwise a data gap would masquerade as a 5m/15m return.
            if anchor_ts - ends[index] > 5 * 60.0 + 1e-6:
                return None
            start_price = float(eligible[index]["close"])
            if start_price <= 0.0 or end_price <= 0.0:
                return None
            return math.log(end_price / start_price)

        steps: list[tuple[float, float]] = []
        for index in range(1, end_index + 1):
            previous = float(eligible[index - 1]["close"])
            current = float(eligible[index]["close"])
            dt = ends[index] - ends[index - 1]
            if previous <= 0.0 or current <= 0.0 or dt <= 0.0 or dt > 10 * 60.0:
                continue
            steps.append((ends[index], math.log(current / previous)))

        def realized_vol(seconds: float) -> float | None:
            values = [
                value for step_ts, value in steps
                if end_ts - seconds < step_ts <= end_ts + 1e-6
            ]
            if len(values) < 2:
                return None
            return math.sqrt(sum(value * value for value in values))

        recovered = {
            "price.ret_5m": return_over(5 * 60.0),
            "price.ret_15m": return_over(15 * 60.0),
            "price.ret_60m": return_over(60 * 60.0),
            "vol.rv_15m": realized_vol(15 * 60.0),
            "vol.rv_60m": realized_vol(60 * 60.0),
        }
        recovered_any = False
        for feature_id, value in recovered.items():
            if value is not None and math.isfinite(float(value)):
                result[feature_id] = float(value)
                recovered_any = True
        if not recovered_any:
            return result

        meta = dict(result.get("_meta") or {})
        quality = dict(meta.get("quality") or {})
        quality.setdefault("source_ts", end_ts)
        quality.setdefault("source_quality", None)
        quality["stale"] = False
        meta.update({
            "quality": quality,
            "provenance": "CAUSAL_RECOMPUTED",
            "source": "passive_market_bars",
            "source_created_cutoff_ts": capture_recorded_ts,
            "source_window_end_ts": end_ts,
            "baseline_price_backfill": True,
            "baseline_price_backfill_version": "g1s-ede-baseline-price-backfill-v1.3",
            "future_points_used": False,
            "bar_end_ts_lte_t0": True,
            "bar_created_ts_lte_capture_record": True,
        })
        result["_meta"] = meta

        key = (instrument, t0, capture_recorded_ts)
        self._causal_bar_cache[key] = result
        return result
