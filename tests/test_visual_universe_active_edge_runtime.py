from __future__ import annotations

from types import SimpleNamespace

import seiltanzer.visual_universe_routes as universe


class _Journal:
    def active_trade(self):
        return {"id": "trade-1", "instrument": "NAS100", "direction": "long"}


class _Engine:
    journal = _Journal()
    market = SimpleNamespace(instrument_code="NAS100")
    short_horizon = None
    management_local = None

    def cross_asset_payload(self):
        return {"available": False, "summary": {}, "break_alerts": []}


def _active(**overrides):
    payload = {
        "available": True,
        "measurement_available": True,
        "report_state": "CURRENT_SHA_REPORTS_COMPLETE",
        "source_report_n": 6,
        "expected_report_n": 6,
        "total_active_signal_n": 1,
        "matched_structured_signal_n": 1,
        "supporting_position_n": 1,
        "opposing_position_n": 0,
        "matched_groups": [{
            "target_id": "DIRECTION",
            "target_family": "DIRECTION",
            "signal_horizon_minutes": 30,
            "matched_n": 1,
            "supporting_n": 1,
            "opposing_n": 0,
            "net_vote": 1,
            "net_vote_ratio": 1.0,
        }],
    }
    payload.update(overrides)
    return payload


def _profile(**overrides):
    payload = {
        "available": True,
        "weight_fraction": 0.2,
        "max_weight_fraction": 0.3,
        "direction_score": 1.0,
        "strict_directional_share": 0.0,
        "independent_bucket_n": 1,
    }
    payload.update(overrides)
    return payload


def test_edge_universe_resolves_runtime_active_edge_wrappers(monkeypatch):
    active = _active(source_marker="runtime-wrapper")
    profile = _profile(profile_marker="runtime-wrapper")

    monkeypatch.setattr(
        universe.active_edge_ai,
        "build_active_edge_context",
        lambda engine, snapshot: active,
    )
    monkeypatch.setattr(
        universe.active_edge_weight,
        "edge_weight_profile",
        lambda context: profile,
    )
    monkeypatch.setattr(universe, "_latest_frozen_context", lambda engine, snapshot: {})

    payload = universe.build_edge_universe_payload(_Engine(), now=1234.0)

    assert payload["active_edge"]["source_marker"] == "runtime-wrapper"
    assert payload["production_weight"]["profile_marker"] == "runtime-wrapper"
    assert payload["production_weight"]["measurement_available"] is True
    assert payload["production_weight"]["decision_reason"] == {
        "code": "ACTIVE_MATCH",
        "label": "ACTIVE MATCH",
    }


def test_edge_decision_reason_distinguishes_honest_zero_states():
    assert universe._edge_decision_reason(
        _active(total_active_signal_n=0, matched_structured_signal_n=0,
                supporting_position_n=0, opposing_position_n=0),
        _profile(weight_fraction=0.0, independent_bucket_n=0),
    )["code"] == "NO_ACTIVE_EDGE"
    assert universe._edge_decision_reason(
        _active(matched_structured_signal_n=0,
                supporting_position_n=0, opposing_position_n=0),
        _profile(weight_fraction=0.0, independent_bucket_n=0),
    )["code"] == "NO_T0_MATCH"
    assert universe._edge_decision_reason(
        _active(supporting_position_n=0, opposing_position_n=0),
        _profile(weight_fraction=0.0, independent_bucket_n=0),
    )["code"] == "NON_DIRECTIONAL_ONLY"
    assert universe._edge_decision_reason(
        _active(matched_structured_signal_n=2,
                supporting_position_n=1, opposing_position_n=1),
        _profile(weight_fraction=0.0, independent_bucket_n=0),
    )["code"] == "ZERO_NET_DIRECTION"
    assert universe._edge_decision_reason(_active(), _profile())["code"] == "ACTIVE_MATCH"


def test_edge_decision_reason_distinguishes_missing_and_partial_reports():
    missing = _active(
        available=False,
        measurement_available=False,
        report_state="CURRENT_SHA_REPORTS_MISSING",
        source_report_n=0,
        total_active_signal_n=0,
        matched_structured_signal_n=0,
        supporting_position_n=0,
        opposing_position_n=0,
        matched_groups=[],
    )
    partial = {**missing, "report_state": "CURRENT_SHA_REPORTS_PARTIAL", "source_report_n": 3}
    assert universe._edge_decision_reason(missing, _profile())["code"] == "EDGE_REPORTS_MISSING"
    assert universe._edge_decision_reason(partial, _profile())["code"] == "EDGE_REPORTS_PARTIAL"


def test_visual_profile_uses_none_when_measurement_is_unavailable():
    active = _active(
        available=False,
        measurement_available=False,
        report_state="CURRENT_SHA_REPORTS_PARTIAL",
        source_report_n=2,
    )
    display = universe._visual_profile(active, _profile())
    assert display["measurement_available"] is False
    assert display["report_state"] == "CURRENT_SHA_REPORTS_PARTIAL"
    assert display["source_report_n"] == 2
    for key in (
        "weight_fraction", "max_weight_fraction", "direction_score",
        "strict_directional_share", "independent_bucket_n",
    ):
        assert display[key] is None


def test_visual_profile_preserves_measured_zero():
    display = universe._visual_profile(
        _active(total_active_signal_n=0, matched_structured_signal_n=0),
        _profile(
            available=False,
            weight_fraction=0.0,
            max_weight_fraction=0.0,
            direction_score=0.0,
            strict_directional_share=0.0,
            independent_bucket_n=0,
        ),
    )
    assert display["measurement_available"] is True
    assert display["weight_fraction"] == 0.0
    assert display["direction_score"] == 0.0
    assert display["independent_bucket_n"] == 0


def test_edge_universe_builder_exception_is_not_projected_as_zero(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("active edge failed")

    monkeypatch.setattr(universe.active_edge_ai, "build_active_edge_context", broken)
    monkeypatch.setattr(universe, "_latest_frozen_context", lambda engine, snapshot: {})
    payload = universe.build_edge_universe_payload(_Engine(), now=1234.0)
    profile = payload["production_weight"]
    assert payload["active_edge"]["measurement_available"] is False
    assert profile["measurement_available"] is False
    assert profile["weight_fraction"] is None
    assert profile["direction_score"] is None
    assert profile["independent_bucket_n"] is None
    assert profile["decision_reason"]["code"] == "EDGE_CONTEXT_UNAVAILABLE"
    assert payload["semantics"]["missing_active_edge_is_not_zero"] is True
