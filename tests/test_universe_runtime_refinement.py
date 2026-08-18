from __future__ import annotations

from types import SimpleNamespace

from seiltanzer import universe_runtime_refinement as refinement


def _correlation_state(ts: float) -> dict:
    return {
        "value": {
            "assets": ["NAS", "SP500"],
            "matrix": [[1.0, 0.4], [0.4, 1.0]],
            "matrix_delta": [[0.0, 0.1], [0.1, 0.0]],
            "asof": ts,
        },
        "status": "delayed",
        "ts": ts,
        "source": "observed-test-correlation",
    }


class _Feed:
    def __init__(self, ts: float, *, produces: bool = True):
        self.correlation = {"value": None, "status": "no_data", "ts": None}
        self.refresh_calls = 0
        self._ts = ts
        self._produces = produces

    def refresh_correlation(self):
        self.refresh_calls += 1
        if self._produces:
            self.correlation = _correlation_state(self._ts)


class _Engine:
    def __init__(self, feeds):
        self.feeds = feeds

    def _feed(self, instrument):
        return self.feeds[instrument]


def test_correlation_is_observed_once_then_shared_before_future_t0(monkeypatch):
    now = 1_800_000_000.0
    first = _Feed(now)
    second = _Feed(now)
    engine = _Engine({"USDCAD": first, "EURUSD": second})
    monkeypatch.setattr(refinement.time, "time", lambda: now)
    monkeypatch.setattr(refinement, "_SHARED_CORRELATION", None)
    monkeypatch.setattr(refinement, "_SHARED_CAPTURED_AT", 0.0)

    refinement._prepare_correlation_before_t0(engine, "USDCAD")
    assert first.refresh_calls == 1
    assert first.correlation["value"]["asof"] == now

    refinement._prepare_correlation_before_t0(engine, "EURUSD")
    assert second.refresh_calls == 0
    assert second.correlation == first.correlation
    assert refinement._SHARED_CAPTURED_AT == now


def test_failed_refresh_reuses_only_still_observed_recent_snapshot(monkeypatch):
    now = 1_800_000_000.0
    cached = _correlation_state(now - 600.0)
    failed = _Feed(now, produces=False)
    engine = _Engine({"USDCAD": failed})
    monkeypatch.setattr(refinement.time, "time", lambda: now)
    monkeypatch.setattr(refinement, "_SHARED_CORRELATION", cached)
    # Force a source refresh attempt rather than the 5-minute fast reuse path.
    monkeypatch.setattr(
        refinement, "_SHARED_CAPTURED_AT", now-refinement.CORRELATION_REFRESH_TTL_SEC-1.0)

    refinement._prepare_correlation_before_t0(engine, "USDCAD")

    assert failed.refresh_calls == 1
    assert failed.correlation == cached


def test_non_directional_matches_are_explicit_not_fabricated_into_votes():
    context = {
        "matched_structured_signal_n": 8,
        "supporting_position_n": 0,
        "opposing_position_n": 0,
        "matched_groups": [
            {"target_family": "VOLATILITY", "signal_horizon_minutes": 15,
             "supporting_n": 0, "opposing_n": 0, "net_vote": 0},
            {"target_family": "VOLATILITY", "signal_horizon_minutes": 60,
             "supporting_n": 0, "opposing_n": 0, "net_vote": 0},
        ],
    }

    result = refinement._annotate_active_context(context)

    assert result["directional_matched_signal_n"] == 0
    assert result["non_directional_matched_signal_n"] == 8
    assert result["directional_matched_group_n"] == 0
    assert result["non_directional_matched_group_n"] == 2
    assert result["directional_weight_available"] is False
    assert result["directional_weight_reason"] == "CURRENT_T0_MATCHES_ARE_NON_DIRECTIONAL"


def test_directional_matches_preserve_support_oppose_counts():
    context = {
        "matched_structured_signal_n": 6,
        "supporting_position_n": 4,
        "opposing_position_n": 1,
        "matched_groups": [
            {"target_family": "RETURN", "signal_horizon_minutes": 15,
             "supporting_n": 3, "opposing_n": 0, "net_vote": 3},
            {"target_family": "DIRECTION", "signal_horizon_minutes": 60,
             "supporting_n": 1, "opposing_n": 1, "net_vote": 0},
            {"target_family": "VOLATILITY", "signal_horizon_minutes": 60,
             "supporting_n": 0, "opposing_n": 0, "net_vote": 0},
        ],
    }

    result = refinement._annotate_active_context(context)

    assert result["directional_matched_signal_n"] == 5
    assert result["non_directional_matched_signal_n"] == 1
    assert result["directional_matched_group_n"] == 1
    assert result["non_directional_matched_group_n"] == 1
    assert result["directional_weight_available"] is True
    assert result["directional_weight_reason"] == "DIRECTIONAL_MATCHES_AVAILABLE"
