import pytest
import time
from fastapi.testclient import TestClient
from seiltanzer.app import create_app
from seiltanzer.config import Settings
from seiltanzer.app import _acknowledged_execution, _extended_manual_decision


def test_extended_action_cannot_replace_a_pending_partial_close():
    base = {"policy": "CLOSE_25", "manual_execution_required": True,
            "remaining_fraction_before_action": 1.0}
    action = {"policy": "TIGHTEN_STOP", "action_id": "shadow-action-1",
              "execution_status": "pending_execution"}
    assert _extended_manual_decision(base, action) is base


def test_broker_fill_price_supplies_r_when_market_quote_is_absent():
    trade = {"entry": 100.0, "stop": 90.0, "direction": "long"}
    assert _acknowledged_execution(trade, {"feeds": {}, "prob": {}}, 115.0) == (115.0, 1.5)

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    engine.market.refresh_price()
    price = engine.market.price["value"]
    trade = engine.journal.open_trade(3, "NAS100", "long", price,
                                      price*.99, price*1.025)
    engine.position.open_trade(trade)
    engine.on_trade_opened(trade)
    with TestClient(app) as c:
        yield c

def test_fallback_returns_structured_management_decision(client):
    response = client.post("/api/ai/verdict")
    assert response.status_code == 200
    body = response.json()
    decision = body["management_decision"]
    assert decision["policy"] in {"HOLD","CLOSE_10","CLOSE_25","CLOSE_50","EXIT"}
    assert decision["fraction_semantics"] == "fraction_of_current_remaining_position"
    assert body["mode"] == "deterministic_fallback"
    edge = body["edge_management"]
    assert edge["action_now"] == decision["policy"]
    assert edge["hard_risk_cvar_preserved"] is True
    assert edge["may_widen_stop"] is False
    assert edge["may_increase_position"] is False
    assert edge["automatic_execution_allowed"] is False


def test_extended_shadow_action_is_registered_and_manually_acknowledged(
    client, monkeypatch,
):
    from seiltanzer.app import _refresh_management_decision

    initial = True
    def legacy_close(engine, snapshot, trade):
        nonlocal initial
        if initial:
            # A legacy close does not suppress evaluation of extended actions.
            snapshot["policy_manager"]["management_decision"]["policy"] = "CLOSE_25"
            initial = False
        return _refresh_management_decision(engine, snapshot, trade)

    monkeypatch.setattr("seiltanzer.app._refresh_management_decision", legacy_close)
    _install_adverse_frozen_candidates(monkeypatch, explicit_common_bank=True)

    def fake_verdict(snapshot):
        geometry = snapshot["trade_geometry"]
        current = float(geometry["current"])
        active_stop = float(geometry["active_risk_barrier"])
        target = active_stop + (current - active_stop) * 0.5
        return {
            "verdict": "test shadow action",
            "model": "test-provider",
                "llm_shadow_decision": {
                    "status": "ok",
                    "policy": "TIGHTEN_STOP",
                    "confidence": 0.8,
                    "policy_scores": {name: float(name == 'TIGHTEN_STOP') for name in (
                        'HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT', 'MOVE_TO_BE',
                        'TIGHTEN_STOP', 'TRAIL_GAMMA_FLIP', 'REDUCE_TAKE', 'EXTEND_TAKE',
                        'SCALE_OUT_ON_SPIKE', 'TIME_STOP')},
                    "production_authority": False,
                    "quant_evaluation": {"status": "eligible", "production_authority": True,
                                         "reason": "ROBUST_EXPECTED_GAIN_AND_CVAR_PASS"},
                "automatic_execution_allowed": False,
                "working_action": {
                    "contract_version": "llm-shadow-manual-action-v1",
                    "status": "READY_FOR_MANUAL_CONFIRMATION",
                    "policy": "TIGHTEN_STOP",
                    "confidence": 0.8,
                    "instruction_ru": f"Подтянуть стоп к {target:g}",
                    "parameters": {"stop_price": target, "anchor": "TEST"},
                    "manual_confirmation_required": True,
                    "automatic_execution_allowed": False,
                    "may_widen_stop": False,
                    "may_increase_position": False,
                },
            },
        }

    monkeypatch.setattr("seiltanzer.app.request_verdict", fake_verdict)
    response = client.post("/api/ai/verdict")
    assert response.status_code == 200
    body = response.json()
    # The independently supplied LLM opinion is retained. The common ranking
    # publishes its selected, quantified action in a different field.
    assert body["llm_shadow_decision"]["production_authority"] is False
    assert body["llm_shadow_decision"]["policy"] == "TIGHTEN_STOP"
    assert next(row for row in body['unified_edge_ensemble']['scheme_comparisons']
                if row['scheme'] == 'legacy_control')['selected_policy'] == 'CLOSE_25'
    assert len(body['unified_edge_ensemble']['candidates']) == 12
    action = body["selected_management_action"]["working_action"]
    assert body["selected_management_action"]["production_authority"] is True
    assert action["action_id"].startswith("management-action-")
    assert action["execution_status"] == "pending_execution"
    assert action["production_authority"] is True
    decision = body["management_decision"]
    assert decision["policy"] in {"TIME_STOP", "TIGHTEN_STOP", "MOVE_TO_BE"}
    assert decision["decision_id"] == action["action_id"]
    assert decision["quant_baseline_policy"] == "HOLD"
    assert decision["authority"] == "AI_RISK_OVERLAY_EXTENDED"
    assert decision["automatic_execution_allowed"] is False
    assert body["edge_management"]["action_now"] == decision["policy"]
    assert body["edge_management"]["instruction_ru"] == action["instruction_ru"]

    journal = client.app.state.engine.journal
    stored = journal._conn.execute(
        "SELECT review_id,production_policy,snapshot_json FROM decision_snapshots "
        "ORDER BY recorded_ts DESC LIMIT 1").fetchone()
    assert stored["production_policy"] == decision["policy"]
    assert journal._conn.execute(
        "SELECT review_id FROM management_decisions WHERE review_id=?",
        (stored["review_id"],)).fetchone()[0] == stored["review_id"]
    assert client.app.state.engine.position._conn.execute(
        "SELECT review_id FROM llm_shadow_manual_actions WHERE action_id=?",
        (action["action_id"],)).fetchone()[0] == stored["review_id"]

    acknowledged = client.post("/api/ai/decision/ack", json={
        "decision_id": decision["decision_id"],
        "trade_id": action["trade_id"],
        "executed": True,
        "execution_price": client.app.state.engine._current_instrument_price(
            client.app.state.engine.journal.active_trade()),
    })
    assert acknowledged.status_code == 200
    result = acknowledged.json()
    expected_status = "armed" if decision["policy"] == "TIME_STOP" else "executed"
    assert result["execution_status"] == expected_status
    if decision["policy"] == "TIGHTEN_STOP":
        assert result["position_state"]["active_stop_type"] == "TIGHTENED"
    assert client.get("/api/position").json()["shadow_actions"][-1]["status"] == expected_status


def test_unquantified_llm_action_cannot_replace_production_hold(client, monkeypatch):
    monkeypatch.setattr("seiltanzer.app.request_verdict", lambda snapshot: {
        "verdict": "Unverified candidate", "model": "test-provider",
        "llm_shadow_decision": {
            "status": "ok", "policy": "TIGHTEN_STOP", "confidence": .9,
            "working_action": {
                "status": "READY_FOR_MANUAL_CONFIRMATION", "policy": "TIGHTEN_STOP",
                "confidence": .9, "parameters": {"stop_price": 1},
                "manual_confirmation_required": True,
                "automatic_execution_allowed": False,
            },
        },
    })
    response = client.post("/api/ai/verdict")
    assert response.status_code == 200
    assert response.json()["management_decision"]["policy"] in {
        "HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"}
    assert client.get("/api/position").json()["shadow_actions"] == []


def test_deterministic_active_action_without_llm_is_saved_and_manually_confirmed(client, monkeypatch):
    from seiltanzer.app import _refresh_management_decision
    def hold(engine, snapshot, trade):
        snapshot['policy_manager']['management_decision']['policy'] = 'HOLD'
        snapshot['policy_manager']['selection_rule']['eligible'] = ['HOLD']
        return _refresh_management_decision(engine, snapshot, trade)
    monkeypatch.setattr('seiltanzer.app._refresh_management_decision', hold)
    _install_adverse_frozen_candidates(monkeypatch)
    def offline(snapshot):
        # A controlled adverse model fixture exercises the actual selector and
        # ledger; no candidate or quant gate is mocked.
        raise RuntimeError('OpenRouter connection failed: ReadTimeout')
    monkeypatch.setattr('seiltanzer.app.request_verdict', offline)
    response = client.post('/api/ai/verdict')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['mode'] == 'deterministic_fallback'
    action = body['management_decision']
    assert action['policy'] in {'TIME_STOP', 'TIGHTEN_STOP', 'MOVE_TO_BE'}
    assert action['automatic_execution_allowed'] is False
    assert '**РАСШИРЕННОЕ РЕШЕНИЕ' in body['verdict']
    assert len(body['active_management_candidates']) == 7
    assert body['management_calculation_audit']['status'] == 'AVAILABLE'
    result = client.post('/api/ai/decision/ack', json={
        'decision_id': action['decision_id'], 'trade_id': action['trade_id'],
        'executed': True,
        'execution_price': client.app.state.engine._current_instrument_price(
            client.app.state.engine.journal.active_trade()),
    })
    assert result.status_code == 200, result.text
    assert result.json()['execution_status'] == ('armed' if action['policy'] == 'TIME_STOP' else 'executed')
    assert result.json()['position_state']['remaining_position_fraction'] == 1


def _install_adverse_frozen_candidates(monkeypatch, explicit_common_bank=False):
    from seiltanzer.active_management import select_active_management
    def quantify(snapshot):
        # Set the model before the shared frozen candidate calculations. The
        # provider cannot retroactively replace the scenario inputs.
        manager = snapshot['policy_manager']
        manager['decision_reliability'] = {'level': 'высокая'}
        manager['evidence']['data_quality']['reliability'] = {'level': 'высокая'}
        snapshot['metric_coverage'] = {}
        manager['gate']['data_reliability'] = 'высокая'
        manager['input_audit']['rows']['instrument_price'].update(
            available=True, status='live', source='test direct', production_authority=True)
        manager['inputs'].update(drift_R=-2, sigma_R=.3)
        if explicit_common_bank:
            # Isolate the current model's vote from unrelated demo historical
            # opinions; all candidates still pass the real economic/risk gates.
            manager['active_edge_provisional_weight'] = {'available': False}
            manager['llm_edge_exploratory_weight'] = {'available': False}
            manager['mathematical_edge'] = {'available': False}
            start = manager['inputs']['r0']
            stop = manager['inputs']['stop_r']
            manager['unified_scenario_bank'] = {
                'captured_ts': snapshot['captured_ts'],
                'horizon_minutes': manager['inputs']['horizon_minutes'],
                'state_space': 'R_multiple_of_initial_risk',
                'r_paths': [[start, (start + stop) / 2, stop - .1],
                            [start, (start + stop) / 2 - .05, stop - .1]],
                'weights': [.5, .5], 'source': 'controlled_paired_execution_fixture',
                'measure': 'explicit_test_measure', 'distribution_kind': 'supplied_weighted_paths'}
        return select_active_management(snapshot)
    monkeypatch.setattr('seiltanzer.active_management.select_active_management', quantify)


def test_journal_failure_restores_prior_pending_decision(client, monkeypatch):
    engine = client.app.state.engine
    trade = engine.journal.active_trade()
    previous = {"captured_ts": time.time() - 1., "trade_id": trade["id"],
        "position_state": engine.position.state(trade),
        "policy_manager": {"recommendation": {"policy": "CLOSE_25"}}}
    old = engine.position.preview_decision(previous, trade)
    previous["policy_manager"]["management_decision"] = old
    engine.position.register_decision(previous, "previous-published-review", trade)
    monkeypatch.setattr('seiltanzer.app.request_verdict', lambda snapshot: {
        "verdict": "test", "model": "test", "llm_shadow_decision": {
            "policy": "HOLD", "status": "ok", "production_authority": False}})
    monkeypatch.setattr(engine.journal, 'record_ai_verdict',
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")))
    response = client.post('/api/ai/verdict')
    assert response.status_code == 500
    assert response.json()['error']['code'] == 'journal_error'
    rows = [dict(row) for row in engine.position._conn.execute(
        'SELECT decision_id,status FROM management_decisions')]
    assert next(row for row in rows if row['decision_id'] == old['decision_id'])['status'] == 'pending_execution'
    assert all(row['status'] == 'publication_failed' for row in rows
               if row['decision_id'] != old['decision_id'])
    acknowledged = engine.position.acknowledge(
        decision_id=old['decision_id'], trade=trade, executed=True,
        execution_price=trade['entry'], execution_r=0.)
    assert acknowledged['execution_status'] == 'executed'


def test_trade_edit_during_provider_call_rejects_stale_model(client, monkeypatch):
    engine = client.app.state.engine
    def provider(snapshot):
        trade = engine.journal.active_trade()
        engine.journal.edit_trade(trade['id'], entry=trade['entry'] + 1.)
        return {"verdict": "old model", "model": "test"}
    monkeypatch.setattr('seiltanzer.app.request_verdict', provider)
    response = client.post('/api/ai/verdict')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'stale_decision'
    assert engine.position._conn.execute('SELECT COUNT(*) FROM management_decisions').fetchone()[0] == 0
