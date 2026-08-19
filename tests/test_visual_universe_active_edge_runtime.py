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
        "independent_bucket_n": 1,
    }
    payload.update(overrides)
    return payload


def test_edge_universe_resolves_runtime_active_edge_wrappers(monkeypatch):
    """Universe must see wrappers installed after visual module import."""
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
