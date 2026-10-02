import json
import sqlite3

from seiltanzer.execution_ack_telemetry import ensure_execution_ack_schema, record_execution_ack


def test_observed_ack_stages_are_idempotent_and_do_not_invent_execution_costs():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    ensure_execution_ack_schema(connection)
    decision = {'action_id': 'action-1', 'trade_id': 1, 'review_id': 'review-1',
                'created_ts': 100., 'payload_json': '{}'}
    for stage, ts in [('armed', 120.), ('cancelled', 140.)]:
        result = record_execution_ack(connection, decision=decision, status=stage,
                                      acknowledged_ts=ts, execution_price=110.)
        assert result['recorded'] is True
        again = record_execution_ack(connection, decision=decision, status=stage,
                                     acknowledged_ts=ts + 1., execution_price=111.)
        assert again['idempotent'] is True
    rows = [dict(r) for r in connection.execute('SELECT * FROM execution_ack_observations ORDER BY acknowledged_ts')]
    assert len(rows) == 2
    assert [r['acknowledgement_delay_sec'] for r in rows] == [20., 40.]
    for row in rows:
        assert row['execution_price'] is None
        assert row['actual_broker_fill_ts'] is None
        assert row['actual_fee_currency'] is None
        assert row['actual_slippage_currency'] is None
        assert row['manual_latency_cost_r'] is None
        assert json.loads(row['payload_json'])['execution_evidence'] == 'NO_OBSERVED_FILL'


def test_user_reported_fill_is_separate_from_quote_at_ack_estimate():
    connection = sqlite3.connect(':memory:')
    ensure_execution_ack_schema(connection)
    for source, expected in [('user_supplied_broker_fill', 'USER_REPORTED_FILL'),
                             ('quote_at_acknowledgement_estimate', 'QUOTE_AT_ACK_ESTIMATE')]:
        result = record_execution_ack(connection,
            decision={'decision_id': source, 'trade_id': 1, 'created_ts': 100.},
            status='executed', acknowledged_ts=120., execution_price=110.,
            execution_r=1., execution_price_source=source)
        assert result['audit']['execution_evidence'] == expected
        assert result['audit']['broker_fill_independently_verified'] is False
    before = connection.total_changes
    result = record_execution_ack(connection, decision={'decision_id': 'bad', 'trade_id': 1, 'created_ts': 150.},
                                   status='executed', acknowledged_ts=120.)
    assert result['available'] is False
    assert connection.total_changes == before


def test_ledger_records_actual_decline_execution_and_retry_without_changing_fills(tmp_path):
    from seiltanzer.position_state import PositionLedger
    from test_position_state import trade, snapshot
    ledger = PositionLedger(str(tmp_path / 'trades.db'))
    row = trade()
    declined = snapshot(ledger, row, 'CLOSE_25')
    ledger.register_decision(declined, 'review-declined', row)
    declined_id = declined['policy_manager']['management_decision']['decision_id']
    ledger.acknowledge(decision_id=declined_id, trade=row, executed=False,
                       execution_price=105., execution_r=.5)
    accepted = snapshot(ledger, row, 'CLOSE_25', captured=2001.)
    ledger.register_decision(accepted, 'review-accepted', row)
    accepted_id = accepted['policy_manager']['management_decision']['decision_id']
    for _ in range(2):
        ledger.acknowledge(decision_id=accepted_id, trade=row, executed=True,
                           execution_price=105., execution_r=.5,
                           execution_price_source='user_supplied_broker_fill')
    assert ledger.state(row)['remaining_position_fraction'] == .75
    observed = [dict(r) for r in ledger._conn.execute('SELECT * FROM execution_ack_observations')]
    assert len(observed) == 2
    assert {r['stage'] for r in observed} == {'declined', 'executed'}
    assert all(r['acknowledgement_delay_sec'] >= 0 for r in observed)
    assert all(r['actual_broker_fill_ts'] is None for r in observed)
    ledger.close()


def test_extended_geometry_confirmation_is_not_a_cash_flow_fill(tmp_path):
    from seiltanzer.position_state import PositionLedger
    from test_llm_shadow_action_execution import _trade, _register, _shadow
    ledger = PositionLedger(str(tmp_path / 'trades.db'))
    trade = _trade()
    action = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}))
    ledger.acknowledge_shadow_action(action_id=action['action_id'], trade=trade, executed=True,
                                    execution_price=110., execution_r=1.,
                                    execution_price_source='user_supplied_broker_fill')
    observed = dict(ledger._conn.execute('SELECT * FROM execution_ack_observations').fetchone())
    assert observed['execution_price'] is None
    assert json.loads(observed['payload_json'])['execution_evidence'] == 'ACTION_GEOMETRY_CONFIRMED_NOT_FILL'
    assert ledger.state(trade)['remaining_position_fraction'] == 1.
    ledger.close()
