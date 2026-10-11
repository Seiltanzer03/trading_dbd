from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_functional_smoke.py"
_SPEC = importlib.util.spec_from_file_location("production_functional_smoke_under_test", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
smoke = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke)


def _ok_result() -> dict:
    return {
        "status": "OK",
        "no_placeholders": True,
        "production_authority": False,
        "errors": {},
    }


def test_isolated_unified_smoke_assesses_all_actions_without_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("isolated contract fixture must not request production or a provider")
    monkeypatch.setattr(smoke, "request", forbidden)
    summary = smoke.verify_isolated_unified_management_contract()
    assert summary["status"] == "PASS"
    assert summary["actions_assessed"] == 12
    assert summary["extended_actions_assessed"] == 7
    assert summary["real_position_mutations"] == summary["paid_provider_calls"] == summary["orders_sent"] == 0
    assert summary["real_profit_proven"] is False


@pytest.mark.parametrize('policy,lower,tail,allowed', [
    ('TIGHTEN_STOP', -.029, .05, True),
    ('TIGHTEN_STOP', -.04, .05, False),
    ('TIGHTEN_STOP', -.029, .049, False),
    ('EXTEND_TAKE', -.029, .05, False),
])
def test_live_management_smoke_checks_bounded_protection(policy, lower, tail, allowed):
    metrics = {'outcomes_include_execution_costs': True,
               'gross_expected_final_r': .1, 'execution_cost_r': .01,
               'expected_final_r_net': .09}
    candidate = {'policy': policy, 'status': 'eligible',
                 'expected_delta_vs_hold_r': -.025,
                 'paired_delta_ci95_lower_r': lower,
                 'matched_seed_tail_gain_lower_r': tail,
                 'materiality_band_r': .03,
                 'worst_seed_cvar10_net_r': -.1, 'hard_net_floor_r': -.2,
                 'automatic_execution_allowed': False}
    body = {'management_calculation_audit': {
        'version': 'management-calculation-audit-v1', 'status': 'AVAILABLE',
        'policies': {policy: dict(metrics) for policy in ('HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT')},
        'execution_cost_model': {'immediate_full_close_r': .01}, 'model_current_r': .1},
        'active_management_candidates': [candidate] + [{'status': 'blocked'}] * 6}
    if allowed:
        smoke.verify_management_calculation_audit(body)
    else:
        with pytest.raises(AssertionError):
            smoke.verify_management_calculation_audit(body)


def test_live_state_waits_for_price_source_materialization(monkeypatch):
    replies = [(503, {"detail": "live state snapshot is warming"}, 2.0),
               (200, {"feeds": {"price": {"value": 30273.5}}}, 3.0)]
    monkeypatch.setattr(smoke, "request", lambda path, **kwargs: replies.pop(0))
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    state = smoke.wait_for_live_state(wait_sec=5.0)
    assert state["feeds"]["price"]["value"] == 30273.5
    assert replies == []


def test_macro_refresh_immediate_ok_does_not_poll(monkeypatch):
    def _unexpected_poll(*_args, **_kwargs):
        raise AssertionError("completed refresh must not poll runtime")

    monkeypatch.setattr(smoke, "assert_route", _unexpected_poll)
    assert smoke._wait_for_macro_numeric_refresh(_ok_result(), wait_sec=1.0) == _ok_result()


def test_macro_refresh_in_progress_waits_for_existing_worker(monkeypatch):
    states = [
        {
            "numeric": {
                "running": True,
                "last_error": None,
                "last_result": None,
            }
        },
        {
            "numeric": {
                "running": False,
                "last_error": None,
                "last_result": _ok_result(),
            }
        },
    ]

    def _status(path: str, *, timeout: float = 5.0):
        assert path == "/api/research/macro/numeric/status"
        assert timeout == 5.0
        return states.pop(0)

    monkeypatch.setattr(smoke, "assert_route", _status)
    result = smoke._wait_for_macro_numeric_refresh(
        {"status": "IN_PROGRESS", "research_only": True},
        wait_sec=5.0,
        poll_sec=0.0,
    )
    assert result == _ok_result()
    assert states == []


def test_macro_refresh_timeout_remains_failure(monkeypatch):
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        smoke,
        "assert_route",
        lambda *_args, **_kwargs: {
            "numeric": {"running": True, "last_error": None, "last_result": None}
        },
    )

    with pytest.raises(AssertionError, match="macro_numeric_refresh_timeout"):
        smoke._wait_for_macro_numeric_refresh(
            {"status": "IN_PROGRESS", "research_only": True},
            wait_sec=1.0,
            poll_sec=0.0,
        )


def test_macro_refresh_worker_error_fails_closed(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "assert_route",
        lambda *_args, **_kwargs: {
            "numeric": {
                "running": False,
                "last_error": "official_source_failed",
                "last_result": _ok_result(),
            }
        },
    )

    with pytest.raises(AssertionError):
        smoke._wait_for_macro_numeric_refresh(
            {"status": "IN_PROGRESS", "research_only": True},
            wait_sec=1.0,
            poll_sec=0.0,
        )


def test_macro_refresh_placeholder_result_fails_closed(monkeypatch):
    bad = _ok_result()
    bad["no_placeholders"] = False
    monkeypatch.setattr(
        smoke,
        "assert_route",
        lambda *_args, **_kwargs: {
            "numeric": {"running": False, "last_error": None, "last_result": bad}
        },
    )

    with pytest.raises(AssertionError):
        smoke._wait_for_macro_numeric_refresh(
            {"status": "IN_PROGRESS", "research_only": True},
            wait_sec=1.0,
            poll_sec=0.0,
        )
