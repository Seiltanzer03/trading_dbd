import pytest

from seiltanzer.position_state import PositionLedger
from seiltanzer.management_economics import attach_position_economics
from seiltanzer.ai_snapshot_budget_guard import _strict_authoritative_compaction
from seiltanzer.ai_verdict_v18 import _bounded_gate
from seiltanzer.ai_verdict_v19 import _position_economics_lines
from seiltanzer.app import _refresh_management_decision
from types import SimpleNamespace


def _snapshot(ledger, trade, captured):
    snapshot = {
        "captured_ts": captured, "position_state": ledger.state(trade),
        "trade_geometry": {"current_r": -.287},
        "policy_manager": {
            "recommendation": {"policy": "CLOSE_50"},
            "selection_rule": {"cvar_floor_r": -1.01, "indifference_band_r": .03},
            "policies": {
                "HOLD": {"expected_final_r": -.315, "median_final_r": -1., "cvar10_r": -1.},
                "CLOSE_50": {"expected_final_r": -.301, "median_final_r": -.644, "cvar10_r": -.644},
            },
        },
    }
    snapshot["policy_manager"]["management_decision"] = ledger.preview_decision(snapshot, trade)
    attach_position_economics(snapshot)
    return snapshot


def test_two_close50_fills_reprice_the_remainder_and_total_trade(tmp_path):
    ledger = PositionLedger(str(tmp_path / "position.db"))
    trade = {"id": 1, "entry": 30500., "stop": 30396., "take": 30770., "direction": "long"}
    try:
        first = _snapshot(ledger, trade, 1.)
        ledger.register_decision(first, "review-1", trade)
        first_id = first["policy_manager"]["management_decision"]["decision_id"]
        ledger.acknowledge(decision_id=first_id, trade=trade, executed=True,
                           execution_price=30470.1, execution_r=-.287)
        second = _snapshot(ledger, trade, 2.)
        manager = second["policy_manager"]
        decision = manager["management_decision"]
        assert decision["remaining_fraction_before_action"] == .5
        assert decision["closed_fraction_of_initial_position"] == .25
        assert decision["remaining_fraction_after_action"] == .25
        assert decision["repeat_reduction"] is True
        assert decision["previous_executed_reduction"]["decision_id"] == first_id
        assert decision["decision_id"] != first_id
        totals = manager["position_economics"]["policies"]
        assert totals["CLOSE_50"]["expected_delta_total_r"] == pytest.approx(.007)
        assert totals["CLOSE_50"]["cvar_gain_total_r"] == pytest.approx(.178)
        assert totals["CLOSE_50"]["expected_total_r"] == pytest.approx(-.294)
        assert totals["CLOSE_50"]["cvar10_total_r"] == pytest.approx(-.4655)
        assert manager["position_economics"]["total_cvar_floor_r"] == pytest.approx(-.6485)
        assert manager["position_economics"]["statistically_validated_advantage"] is False
        _strict_authoritative_compaction(second)
        report = "\n".join(_position_economics_lines(second))
        assert "закрыть 25.0%; после 25.0%" in report
        assert "новое сокращение обновлённого остатка" in report
        assert "издержки исполненных закрытий не учтены" in report
        ledger.register_decision(second, "review-2", trade)
        ledger.acknowledge(decision_id=decision["decision_id"], trade=trade, executed=True,
                           execution_price=30469., execution_r=-.3)
        state = ledger.state(trade)
        assert state["remaining_position_fraction"] == .25
        assert state["realized_r_weighted"] == pytest.approx(-.2185)
        # An old confirmation is harmless even after a new reduction.
        repeated = ledger.acknowledge(decision_id=first_id, trade=trade, executed=True,
                                      execution_price=30469., execution_r=-.3)
        assert repeated["idempotent"] is True
        assert repeated["position_state"]["remaining_position_fraction"] == .25
    finally:
        ledger.close()


def test_missing_execution_result_is_never_zero_realized_profit(tmp_path):
    ledger = PositionLedger(str(tmp_path / "position.db"))
    trade = {"id": 1, "entry": 100., "stop": 90., "take": 130.}
    try:
        snapshot = _snapshot(ledger, trade, 1.)
        ledger.register_decision(snapshot, "review", trade)
        ledger.acknowledge(decision_id=snapshot["policy_manager"]["management_decision"]["decision_id"],
                           trade=trade, executed=True, execution_price=None, execution_r=None)
        updated = _snapshot(ledger, trade, 2.)
        assert updated["position_state"]["realized_r_weighted"] is None
        row = updated["policy_manager"]["position_economics"]["policies"]["CLOSE_50"]
        assert row["expected_total_r"] is None
        assert row["expected_delta_total_r"] == pytest.approx(.007)
    finally:
        ledger.close()


def test_bounding_preserves_scalar_gate_failures_and_observed_metrics():
    gate = {"degraded_authority_overlay": {
        "candidate_summary": {"EXIT": {"failed": ["total_adverse", "live_adverse"]}},
        "evidence": {"observed_metrics": [{"metric": "live_60m_r", "value": -.2}]},
    }}
    assert _bounded_gate(gate) == gate


def test_unchanged_repeat_is_hold_but_fresh_adverse_review_can_cut_again(tmp_path):
    ledger = PositionLedger(str(tmp_path / "position.db"))
    trade = {"id": 1, "entry": 30500., "stop": 30396., "take": 30770., "direction": "long"}
    engine = SimpleNamespace(position=ledger)
    try:
        first = _snapshot(ledger, trade, 1.)
        _refresh_management_decision(engine, first, trade)
        ledger.register_decision(first, "review-1", trade)
        ledger.acknowledge(decision_id=first["policy_manager"]["management_decision"]["decision_id"],
                           trade=trade, executed=True, execution_price=30470.1, execution_r=-.287)
        unchanged = _snapshot(ledger, trade, 2.)
        decision = _refresh_management_decision(engine, unchanged, trade)
        assert decision["policy"] == "HOLD"
        assert decision["manual_execution_required"] is False
        assert decision["remaining_fraction_after_action"] == .5
        assert unchanged["policy_manager"]["management_arbiter"]["effective_policy"] == "HOLD"
        assert "Сейчас HOLD для остатка" in "\n".join(_position_economics_lines(unchanged))
        # The last executed basis, not the intermediate HOLD, remains the anchor.
        ledger.register_decision(unchanged, "review-2", trade)
        worsened = _snapshot(ledger, trade, 3.)
        worsened["trade_geometry"]["current_r"] = -.45
        decision = _refresh_management_decision(engine, worsened, trade)
        assert decision["policy"] == "CLOSE_50"
        assert decision["remaining_fraction_after_action"] == .25
        assert worsened["policy_manager"]["repeat_intervention_gate"]["reasons"] == ["adverse_move_0_15_r"]
    finally:
        ledger.close()
