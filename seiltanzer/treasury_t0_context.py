"""Official, causal U.S. Treasury context frozen into future live T0 rows.

The HTTP/AI path never fetches rates.  A low-frequency background runtime uses
the existing official Treasury daily curve adapter and keeps a process-local
materialization.  T0 capture then freezes only a snapshot that was both safely
published and actually fetched no later than T0.

This module is context-only.  It does not extend the EDE search space, enter the
current ML vector, or receive trading/policy authority.  Missing, partial or
stale observations remain unavailable; Yahoo proxies and synthetic fallbacks
are never accepted.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .edge_discovery.rates import (
    RATE_FEATURE_IDS,
    RATES_MAX_STALENESS_SECONDS,
    TREASURY_SOURCE,
    RatesState,
    TreasuryDailyRate,
    build_rates_states,
    fetch_treasury_daily_rates,
    treasury_month_url,
)
from .passive_learning import PassiveLearningEngine


TREASURY_T0_CONTEXT_VERSION = "treasury-live-t0-context-v1"
# The source is daily, but a successful fetch is required at least once per day.
# This prevents an old in-memory value from looking current during an unnoticed
# collector outage while allowing a bounded retry window.
TREASURY_FETCH_STALE_SECONDS = 26 * 60 * 60.0
TREASURY_REFRESH_SECONDS = 6 * 60 * 60.0
TREASURY_HISTORY_DAYS = 75

_INSTALLED = False
_AI_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unavailable(*, captured_ts: float, reason: str,
                 fetched_at: float | None = None,
                 last_source_date: str | None = None,
                 source_age_seconds: float | None = None,
                 fetch_age_seconds: float | None = None,
                 stale: bool = False) -> dict[str, Any]:
    return {
        "contract_version": TREASURY_T0_CONTEXT_VERSION,
        "available": False,
        "reason": str(reason),
        "captured_ts": float(captured_ts),
        "source": TREASURY_SOURCE,
        "official_source_verified": True,
        "last_source_date": last_source_date,
        "fetched_at": fetched_at,
        "stale": bool(stale),
        "staleness": {
            "source_age_seconds": source_age_seconds,
            "fetch_age_seconds": fetch_age_seconds,
            "source_max_age_seconds": RATES_MAX_STALENESS_SECONDS,
            "fetch_max_age_seconds": TREASURY_FETCH_STALE_SECONDS,
        },
        "features": {
            feature_id: {
                "value": None,
                "available": False,
                "reason": str(reason),
            }
            for feature_id in RATE_FEATURE_IDS
        },
        "context_vector": {},
        "missing_feature_ids": list(RATE_FEATURE_IDS),
        "causal_rule": "max(conservative_published_at,fetched_at)<=captured_ts",
        "publication_timestamp_kind": "CONSERVATIVE_NEXT_UTC_DAY",
        "historical_backfill_allowed": False,
        "research_only": True,
        "production_authority": False,
        "production_directional_authority": False,
        "current_ml_feature_vector_reads_treasury_context": False,
        "search_space_changed": False,
        "trained_model_changed": False,
        "yahoo_rates_allowed": False,
        "synthetic_fallback_allowed": False,
    }


def build_treasury_t0_context(
    states: Iterable[RatesState],
    *,
    fetched_at: float | None,
    captured_ts: float,
    fetch_error: str | None = None,
) -> dict[str, Any]:
    """Return a fail-closed official curve snapshot known at ``captured_ts``.

    ``RatesState.asof`` is the existing conservative next-UTC-day publication
    boundary.  ``fetched_at`` is independent provenance: old rows are not
    retrospectively inserted into an earlier immutable T0 merely because their
    source date predates it.
    """
    t0 = _finite(captured_ts)
    fetched = _finite(fetched_at)
    if t0 is None:
        raise ValueError("captured_ts must be finite")
    if fetched is None:
        return _unavailable(
            captured_ts=t0,
            reason="TREASURY_NOT_FETCHED",
        )
    if fetched > t0 + 1e-6:
        return _unavailable(
            captured_ts=t0,
            fetched_at=fetched,
            reason="TREASURY_FETCH_AFTER_T0",
        )

    official_states = sorted(
        (
            state for state in states
            if state.source == TREASURY_SOURCE
            and _finite(state.asof) is not None
            and float(state.asof) <= t0 + 1e-6
        ),
        key=lambda state: (float(state.asof), str(state.source_date)),
    )
    if not official_states:
        return _unavailable(
            captured_ts=t0,
            fetched_at=fetched,
            reason="NO_OFFICIAL_TREASURY_CURVE_AT_T0",
        )

    state = official_states[-1]
    source_asof = float(state.asof)
    source_age = max(0.0, t0 - source_asof)
    fetch_age = max(0.0, t0 - fetched)
    source_stale_after = source_asof + RATES_MAX_STALENESS_SECONDS
    fetch_stale_after = fetched + TREASURY_FETCH_STALE_SECONDS
    stale_after = min(source_stale_after, fetch_stale_after)
    stale_reasons: list[str] = []
    if source_age > RATES_MAX_STALENESS_SECONDS:
        stale_reasons.append("TREASURY_SOURCE_DATE_STALE")
    if fetch_age > TREASURY_FETCH_STALE_SECONDS:
        stale_reasons.append("TREASURY_FETCH_STALE")

    values = {feature_id: _finite(state.values.get(feature_id))
              for feature_id in RATE_FEATURE_IDS}
    base_ids = (
        "rates.us02y_yield",
        "rates.us10y_yield",
        "rates.curve_2s10s",
    )
    missing = [feature_id for feature_id, value in values.items() if value is None]
    missing_base = [feature_id for feature_id in base_ids if values[feature_id] is None]
    if stale_reasons:
        return _unavailable(
            captured_ts=t0,
            fetched_at=fetched,
            last_source_date=state.source_date,
            source_age_seconds=source_age,
            fetch_age_seconds=fetch_age,
            reason="|".join(stale_reasons),
            stale=True,
        )
    if missing_base:
        result = _unavailable(
            captured_ts=t0,
            fetched_at=fetched,
            last_source_date=state.source_date,
            source_age_seconds=source_age,
            fetch_age_seconds=fetch_age,
            reason="INCOMPLETE_OFFICIAL_TREASURY_BASE_CURVE",
        )
        result["missing_feature_ids"] = missing
        return result

    published_at = source_asof
    available_at = max(published_at, fetched)
    publication_date = datetime.fromtimestamp(
        published_at, tz=timezone.utc).date().isoformat()
    try:
        source_date = datetime.strptime(
            state.source_date, "%m/%d/%Y").replace(tzinfo=timezone.utc)
        source_url = treasury_month_url(f"{source_date.year:04d}{source_date.month:02d}")
    except ValueError:
        # A malformed source date invalidates provenance even if numeric fields
        # happen to be present.
        return _unavailable(
            captured_ts=t0,
            fetched_at=fetched,
            last_source_date=state.source_date,
            source_age_seconds=source_age,
            fetch_age_seconds=fetch_age,
            reason="INVALID_TREASURY_SOURCE_DATE",
        )

    feature_rows: dict[str, dict[str, Any]] = {}
    context_vector: dict[str, float] = {}
    for feature_id in RATE_FEATURE_IDS:
        value = values[feature_id]
        feature_rows[feature_id] = {
            "value": value,
            "available": value is not None,
            "reason": None if value is not None else "OFFICIAL_HISTORY_INSUFFICIENT",
            "asof": available_at if value is not None else None,
            "published_at": published_at,
            "fetched_at": fetched,
            "stale_after": stale_after,
            "stale": False,
            "source": TREASURY_SOURCE,
            "future_points_used": False,
            "intraday_velocity_claim": False,
        }
        if value is not None:
            context_vector[feature_id] = value

    result = {
        "contract_version": TREASURY_T0_CONTEXT_VERSION,
        "available": True,
        "reason": None,
        "captured_ts": t0,
        "source": TREASURY_SOURCE,
        "source_url": source_url,
        "official_source_verified": True,
        "observation_date": state.source_date,
        "publication_date": publication_date,
        "published_at": published_at,
        "asof": available_at,
        "fetched_at": fetched,
        "stale_after": stale_after,
        "stale": False,
        "staleness": {
            "source_age_seconds": source_age,
            "fetch_age_seconds": fetch_age,
            "source_stale_after": source_stale_after,
            "fetch_stale_after": fetch_stale_after,
            "source_max_age_seconds": RATES_MAX_STALENESS_SECONDS,
            "fetch_max_age_seconds": TREASURY_FETCH_STALE_SECONDS,
        },
        "features": feature_rows,
        "context_vector": context_vector,
        "missing_feature_ids": missing,
        "complete_base_curve": True,
        "collector_last_error": fetch_error,
        "causal_rule": "max(conservative_published_at,fetched_at)<=captured_ts",
        "publication_timestamp_kind": "CONSERVATIVE_NEXT_UTC_DAY",
        "historical_backfill_allowed": False,
        "slow_daily_context": True,
        "intraday_velocity_claim": False,
        "future_points_used": False,
        "research_only": True,
        "production_authority": False,
        "production_directional_authority": False,
        "current_ml_feature_vector_reads_treasury_context": False,
        "search_space_changed": False,
        "trained_model_changed": False,
        "yahoo_rates_allowed": False,
        "synthetic_fallback_allowed": False,
    }
    return result


class TreasuryLiveRuntime:
    """Low-frequency official collector; live T0 reads only its memory snapshot."""

    def __init__(
        self,
        *,
        fetch_rates: Callable[[float, float], list[TreasuryDailyRate]] | None = None,
        now: Callable[[], float] = time.time,
        refresh_seconds: float = TREASURY_REFRESH_SECONDS,
    ) -> None:
        self._fetch_rates = fetch_rates or fetch_treasury_daily_rates
        self._now = now
        self._refresh_seconds = max(60.0, float(refresh_seconds))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._states: tuple[RatesState, ...] = ()
        self._fetched_at: float | None = None
        self._last_attempt_at: float | None = None
        self._last_error: str | None = None

    def refresh(self, *, now: float | None = None) -> bool:
        attempted_at = float(self._now() if now is None else now)
        start_ts = (
            datetime.fromtimestamp(attempted_at, tz=timezone.utc)
            - timedelta(days=TREASURY_HISTORY_DAYS)
        ).timestamp()
        try:
            observations = list(self._fetch_rates(start_ts, attempted_at))
            if not observations:
                raise RuntimeError("official Treasury response contained no complete 2Y/10Y rows")
            if any(item.source != TREASURY_SOURCE for item in observations):
                raise RuntimeError("non-official Treasury source rejected")
            states = tuple(build_rates_states(observations))
            if not states:
                raise RuntimeError("official Treasury state materialization is empty")
        except Exception as exc:
            with self._lock:
                self._last_attempt_at = attempted_at
                self._last_error = f"{type(exc).__name__}:{exc}"
            return False

        with self._lock:
            self._states = states
            self._fetched_at = attempted_at
            self._last_attempt_at = attempted_at
            self._last_error = None
        return True

    def context_at(self, captured_ts: float) -> dict[str, Any]:
        with self._lock:
            states = self._states
            fetched_at = self._fetched_at
            error = self._last_error
        return build_treasury_t0_context(
            states,
            fetched_at=fetched_at,
            captured_ts=float(captured_ts),
            fetch_error=error,
        )

    def status(self) -> dict[str, Any]:
        now = float(self._now())
        with self._lock:
            fetched_at = self._fetched_at
            return {
                "contract_version": TREASURY_T0_CONTEXT_VERSION,
                "running": bool(self._thread and self._thread.is_alive()),
                "official_source": TREASURY_SOURCE,
                "official_source_verified": True,
                "observation_count": len(self._states),
                "fetched_at": fetched_at,
                "fetch_age_seconds": (
                    max(0.0, now - fetched_at) if fetched_at is not None else None
                ),
                "last_attempt_at": self._last_attempt_at,
                "last_error": self._last_error,
                "refresh_seconds": self._refresh_seconds,
                "fetch_stale_seconds": TREASURY_FETCH_STALE_SECONDS,
                "request_path_network_fetch": False,
                "yahoo_rates_allowed": False,
                "synthetic_fallback_allowed": False,
                "production_authority": False,
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self._refresh_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="official-treasury-live-context",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)


def freeze_treasury_context(
    features: dict[str, Any], runtime: TreasuryLiveRuntime | None, captured_ts: float,
) -> dict[str, Any]:
    """Return a copy with explicit Treasury availability; never mutate caller data."""
    frozen = dict(features)
    if runtime is None:
        frozen["treasury_context_v1"] = _unavailable(
            captured_ts=float(captured_ts),
            reason="TREASURY_RUNTIME_NOT_INSTALLED",
        )
    else:
        frozen["treasury_context_v1"] = runtime.context_at(float(captured_ts))
    return frozen


def project_treasury_ai_context(
    frozen_context: Any, *, observation_t0: float, snapshot_ts: float,
) -> dict[str, Any]:
    """Validate a frozen T0 snapshot before exposing it to AI explanation.

    This is deliberately a post-policy projection.  It cannot change an action,
    confidence modifier, candidate match or model input.  Revalidation at the AI
    snapshot timestamp prevents a once-fresh daily curve from surviving past its
    recorded ``stale_after`` deadline.
    """
    context = frozen_context if isinstance(frozen_context, dict) else {}
    t0 = _finite(observation_t0)
    now = _finite(snapshot_ts)

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "reason": reason,
            "context_vector": {},
            "research_only": True,
            "production_authority": False,
            "production_directional_authority": False,
            "confidence_modifier": 0.0,
            "current_ml_feature_vector_reads_treasury_context": False,
            "yahoo_rates_allowed": False,
            "synthetic_fallback_allowed": False,
        }

    if t0 is None or now is None:
        return unavailable("INVALID_AI_OR_OBSERVATION_TIMESTAMP")
    if context.get("available") is not True:
        return unavailable(str(context.get("reason") or "NO_FROZEN_TREASURY_CONTEXT"))
    if context.get("source") != TREASURY_SOURCE:
        return unavailable("NON_OFFICIAL_TREASURY_SOURCE")
    try:
        source_url = urlparse(str(context.get("source_url") or ""))
    except ValueError:
        return unavailable("INVALID_TREASURY_SOURCE_URL")
    if (source_url.scheme != "https"
            or (source_url.hostname or "").lower() != "home.treasury.gov"
            or context.get("official_source_verified") is not True):
        return unavailable("UNVERIFIED_TREASURY_SOURCE")

    published_at = _finite(context.get("published_at"))
    fetched_at = _finite(context.get("fetched_at"))
    asof = _finite(context.get("asof"))
    stale_after = _finite(context.get("stale_after"))
    if any(value is None for value in (published_at, fetched_at, asof, stale_after)):
        return unavailable("INCOMPLETE_TREASURY_CAUSAL_TIMESTAMPS")
    if max(published_at, fetched_at, asof) > t0 + 1e-6:
        return unavailable("TREASURY_CONTEXT_AFTER_OBSERVATION_T0")
    if now > stale_after + 1e-6:
        return unavailable("TREASURY_CONTEXT_STALE_AT_AI_SNAPSHOT")

    feature_rows = context.get("features")
    feature_rows = feature_rows if isinstance(feature_rows, dict) else {}
    base_ids = (
        "rates.us02y_yield",
        "rates.us10y_yield",
        "rates.curve_2s10s",
    )
    for feature_id in base_ids:
        row = feature_rows.get(feature_id)
        row = row if isinstance(row, dict) else {}
        if (row.get("available") is not True
                or row.get("source") != TREASURY_SOURCE
                or _finite(row.get("value")) is None):
            return unavailable("INCOMPLETE_OFFICIAL_TREASURY_BASE_CURVE")

    vector: dict[str, float] = {}
    for feature_id in RATE_FEATURE_IDS:
        row = feature_rows.get(feature_id)
        row = row if isinstance(row, dict) else {}
        value = _finite(row.get("value"))
        if (value is not None and row.get("available") is True
                and row.get("source") == TREASURY_SOURCE):
            vector[feature_id] = value

    return {
        "available": True,
        "reason": None,
        "source": TREASURY_SOURCE,
        "source_url": context.get("source_url"),
        "official_source_verified": True,
        "observation_t0": t0,
        "observation_date": context.get("observation_date"),
        "publication_date": context.get("publication_date"),
        "published_at": published_at,
        "fetched_at": fetched_at,
        "asof": asof,
        "stale_after": stale_after,
        "context_vector": vector,
        "missing_feature_ids": list(context.get("missing_feature_ids") or []),
        "slow_daily_context": True,
        "intraday_velocity_claim": False,
        "future_points_used": False,
        "analysis_role": "EXPLANATION_ONLY_SLOW_DAILY_CONTEXT",
        "research_only": True,
        "production_authority": False,
        "production_directional_authority": False,
        "confidence_modifier": 0.0,
        "current_ml_feature_vector_reads_treasury_context": False,
        "search_space_changed": False,
        "trained_model_changed": False,
        "yahoo_rates_allowed": False,
        "synthetic_fallback_allowed": False,
    }


def install_treasury_ai_context(ai_context_module: ModuleType | None = None) -> None:
    """Add official rates to explanation context without touching edge logic."""
    global _AI_INSTALLED
    if _AI_INSTALLED:
        return
    if ai_context_module is None:
        from .edge_discovery import ai_context as ai_context_module

    previous_build = ai_context_module.build_ai_ede_context

    def build_ai_ede_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
        result = dict(previous_build(engine, snapshot))
        frozen = ai_context_module._latest_frozen_context(engine, snapshot)
        raw = frozen.get("_raw_frozen") if isinstance(frozen, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        try:
            observation_t0 = float(frozen["observation_t0"])
            snapshot_ts = float(snapshot["captured_ts"])
        except (KeyError, TypeError, ValueError):
            projected = project_treasury_ai_context(
                {}, observation_t0=0.0, snapshot_ts=0.0)
        else:
            projected = project_treasury_ai_context(
                raw.get("treasury_context_v1"),
                observation_t0=observation_t0,
                snapshot_ts=snapshot_ts,
            )

        families = dict(result.get("families") or {})
        families["TREASURY"] = {
            "available": bool(projected.get("available")),
            "context": projected,
            "data_maturity": "INSUFFICIENT_DATA",
            "edge_maturity": "INSUFFICIENT_DATA",
            "authority": {
                "role": "EXPLANATION_ONLY_SLOW_DAILY_CONTEXT",
                "production_authority": False,
                "production_directional_authority": False,
                "confidence_modifier": 0.0,
            },
        }
        result["families"] = families
        result["treasury_context"] = projected
        # Do not alter result.confidence_modifier or any candidate fields.
        return result

    ai_context_module.build_ai_ede_context = build_ai_ede_context
    _AI_INSTALLED = True


def install_treasury_t0_context(engine: Any, runtime: TreasuryLiveRuntime) -> None:
    """Install one lightweight future-T0 freezer after the runtime is attached."""
    global _INSTALLED
    engine.passive._treasury_live_runtime = runtime
    if _INSTALLED:
        return
    _INSTALLED = True
    previous_capture = PassiveLearningEngine.capture_observation

    def capture_observation(self, *, instrument: str, captured_ts: float,
                            market_price: float, features: dict, forecast: dict,
                            provenance: dict, trigger_reason: str = "cadence",
                            evidence_eligible: bool = True,
                            observation_origin: str = "background_collector"):
        frozen = freeze_treasury_context(
            features,
            getattr(self, "_treasury_live_runtime", None),
            float(captured_ts),
        )
        return previous_capture(
            self,
            instrument=instrument,
            captured_ts=captured_ts,
            market_price=market_price,
            features=frozen,
            forecast=forecast,
            provenance=provenance,
            trigger_reason=trigger_reason,
            evidence_eligible=evidence_eligible,
            observation_origin=observation_origin,
        )

    PassiveLearningEngine.capture_observation = capture_observation
