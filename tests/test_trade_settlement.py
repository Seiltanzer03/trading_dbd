import time

import pytest
from fastapi.testclient import TestClient

from seiltanzer.app import create_app
from seiltanzer.config import Settings
from seiltanzer.journal import Journal
from seiltanzer.position_state import PositionLedger


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    monkeypatch.setattr(engine, 'tick_payload', lambda: {
        'feeds': {'price': {'value': 30833}}, 'prob': {'r': 333 / 104},
    })
    yield TestClient(app), engine
    engine.close()


def open_trade(engine, short=False):
    trade = engine.journal.open_trade(3, 'NAS100', 'short' if short else 'long',
                                     30500, 30604 if short else 30396,
                                     30230 if short else 30770)
    engine.position.open_trade(trade)
    return trade


def fill(client, trade, fraction, price, request='fill-1', **extra):
    return client.post('/api/trade/fill', json={
        'trade_id': trade['id'], 'request_id': request, 'close_fraction_current': fraction,
        'execution_price': price, **extra,
    })


@pytest.mark.parametrize('short', [False, True])
def test_manual_partials_and_final_price_close_once_with_weighted_total(terminal, short):
    client, engine = terminal
    trade = open_trade(engine, short)
    sign = -1 if short else 1
    first_price = 30500 + sign * 104
    final_price = 30500 + sign * 333
    first = fill(client, trade, .5, first_price)
    assert first.status_code == 200
    assert engine.journal.get_trade(trade['id'])['status'] == 'open'
    assert fill(client, trade, .5, first_price).json()['idempotent'] is True
    response = client.post('/api/trade/close', json={
        'trade_id': trade['id'], 'execution_price': final_price})
    assert response.status_code == 200, response.text
    closed = response.json()
    assert closed['result_r'] == pytest.approx(.5 + .5 * 333 / 104)
    assert closed['result_status'] == 'AVAILABLE'
    assert engine.journal.active_trade() is None
    count = len(engine.position.events(trade['id']))
    retry = client.post('/api/trade/close', json={'trade_id': trade['id'], 'execution_price': final_price})
    assert retry.json()['idempotent'] is True
    assert len(engine.position.events(trade['id'])) == count
    history = client.get('/api/trade/management', params={'trade_id': trade['id']}).json()
    assert history['summary']['fill_count'] == 2
    assert sum(e['fraction_closed'] for e in history['events']) == pytest.approx(1)
    journal = client.get('/api/journal').json()[0]
    assert journal['result_r'] == pytest.approx(closed['result_r'])
    assert journal['management_summary']['fill_count'] == 2
    assert 'ledger_weighted' in client.get('/api/journal.csv').text


def test_successive_half_closes_use_current_remainder_and_final_fill_autocloses(terminal):
    client, engine = terminal
    trade = open_trade(engine)
    assert fill(client, trade, .5, 30396).status_code == 200
    assert fill(client, trade, .5, 30604, 'fill-2', ladder=True).status_code == 200
    final = fill(client, trade, 1, 30708, 'fill-3')
    assert final.status_code == 200, final.text
    assert final.json()['trade_closed'] is True
    assert final.json()['journal_result_r'] == pytest.approx(-.5 + .25 + .5)
    assert engine.journal.get_trade(trade['id'])['result_r'] == pytest.approx(.25)
    assert [event['fraction_closed'] for event in engine.position.events(trade['id'])] == [0, .5, .25, .25]
    # Retrying a completed fill after opening another trade cannot affect it.
    next_trade = open_trade(engine)
    assert fill(client, trade, 1, 30708, 'fill-3').json()['idempotent'] is True
    assert engine.position.state(next_trade)['remaining_position_fraction'] == 1


def test_quote_estimate_is_labelled_and_missing_previous_fill_is_not_zero(terminal):
    client, engine = terminal
    trade = open_trade(engine)
    fill(client, trade, .5, 30396)
    response = client.post('/api/trade/close', json={'trade_id': trade['id']})
    assert response.json()['result_status'] == 'ESTIMATED'
    assert response.json()['result_r'] == pytest.approx(-.5 + .5 * 333 / 104)
    trade = open_trade(engine)
    with engine.position._lock, engine.position._conn:
        engine.position._event(trade=trade, event_type='MANUAL_REDUCTION', source='old_unknown',
                               before=1, closed=.5, after=.5)
    result = client.post('/api/trade/close', json={'trade_id': trade['id'], 'execution_price': 30833}).json()
    assert result['status'] == 'closed' and result['result_r'] is None
    assert result['result_status'] == 'UNAVAILABLE'
    assert engine.journal.journal_counts(3) == (1, 1)


def test_explicit_whole_trade_override_is_not_counted_as_residual_r(terminal):
    client, engine = terminal
    trade = open_trade(engine)
    fill(client, trade, .5, 30396)
    response = client.post('/api/trade/close', json={'trade_id': trade['id'], 'result_r': 1.2})
    assert response.status_code == 200
    assert response.json()['result_r'] == 1.2
    assert engine.position.state(trade)['realized_r_weighted'] == pytest.approx(1.2)
    engine.journal.reconcile_position_closures()
    assert engine.journal.get_trade(trade['id'])['result_r'] == 1.2


def test_invalid_or_stale_manual_fill_does_not_change_exposure(terminal):
    client, engine = terminal
    trade = open_trade(engine)
    version = engine.position.state(trade)['state_version']
    for fraction, price in [(0, 30604), (1.1, 30604), (.5, 0)]:
        assert fill(client, trade, fraction, price).status_code == 400
    assert fill(client, trade, .5, 30604, expected_state_version=version).status_code == 200
    stale = fill(client, trade, .5, 30604, 'new-fill', expected_state_version=version)
    assert stale.status_code == 409
    assert engine.position.state(trade)['remaining_position_fraction'] == .5


def test_legacy_ack_only_closure_is_repaired_but_manual_override_is_retained(tmp_path):
    path = str(tmp_path / 'trades.db')
    journal, ledger = Journal(path), PositionLedger(path)
    try:
        trade = journal.open_trade(3, 'NAS100', 'long', 30500, 30396, 30770)
        ledger.open_trade(trade)
        with ledger._lock, ledger._conn:
            ledger._event(trade=trade, event_type='AI_CLOSE_50', source='human_confirmed_ai',
                          before=1, closed=.5, after=.5, execution_price=30452, execution_r=-48 / 104)
            ledger._event(trade=trade, event_type='AI_EXIT', source='human_confirmed_ai',
                          before=.5, closed=.5, after=0, execution_price=30833, execution_r=333 / 104)
        # Simulate the old bug: the second close form stored residual-only R.
        with journal._lock, journal._conn:
            journal._conn.execute("UPDATE trades SET result_r=?,result_basis='legacy_manual_total' WHERE id=?",
                                  (333 / 104, trade['id']))
        assert journal.reconcile_position_closures() == [trade['id']]
        assert journal.get_trade(trade['id'])['result_r'] == pytest.approx(.5 * (-48 + 333) / 104)
        audit = journal._conn.execute('SELECT * FROM journal_result_reconciliations').fetchone()
        assert audit['previous_result_r'] == pytest.approx(333 / 104)
        assert journal.reconcile_position_closures() == []
        journal.edit_trade(trade['id'], result_r=1.25)
        assert journal.reconcile_position_closures() == []
        assert journal.get_trade(trade['id'])['result_r'] == 1.25
        # An ACK that previously left an open row is also repaired on restart.
        with journal._lock, journal._conn:
            journal._conn.execute("UPDATE trades SET status='open',result_basis='legacy_manual_total' WHERE id=?",
                                  (trade['id'],))
        assert journal.reconcile_position_closures() == [trade['id']]
        assert journal.active_trade() is None
    finally:
        ledger.close(); journal.close()


def test_final_fill_and_journal_close_roll_back_together(terminal):
    client, engine = terminal
    trade = open_trade(engine)
    with engine.position._lock, engine.position._conn:
        engine.position._conn.execute(
            "CREATE TRIGGER reject_settlement BEFORE UPDATE OF status ON trades "
            "WHEN NEW.status='closed' BEGIN SELECT RAISE(ABORT,'simulated write failure'); END")
    with pytest.raises(Exception, match='simulated write failure'):
        fill(client, trade, 1, 30708)
    assert engine.journal.get_trade(trade['id'])['status'] == 'open'
    assert engine.position.state(trade)['remaining_position_fraction'] == 1
    assert len(engine.position.events(trade['id'])) == 1


def test_legacy_manual_total_is_not_reconstructed_from_residual_event(terminal):
    _, engine = terminal
    trade = open_trade(engine)
    with engine.position._lock, engine.position._conn:
        engine.position._event(trade=trade, event_type='MANUAL_EXIT', source='real_user_trade',
                               before=1, closed=1, after=0, execution_r=2)
    with engine.journal._lock, engine.journal._conn:
        engine.journal._conn.execute(
            "UPDATE trades SET result_r=1.5,result_basis='legacy_manual_total' WHERE id=?", (trade['id'],))
    assert engine.journal.reconcile_position_closures() == []
    assert engine.journal.get_trade(trade['id'])['result_r'] == 1.5


def test_time_stop_confirmed_fill_finalizes_journal(terminal):
    from tests.test_llm_shadow_action_execution import _shadow
    client, engine = terminal
    trade = open_trade(engine)
    fill(client, trade, .5, 30604)
    snapshot = {'captured_ts': time.time(), 'position_state': engine.position.state(trade)}
    action = engine.position.register_shadow_action(snapshot, 'time-review', trade, _shadow(
        'TIME_STOP', {'deadline_ts': time.time() + 1, 'close_fraction': 1}))
    action_id = action['action_id']
    armed = client.post('/api/ai/shadow-action/ack', json={
        'trade_id': trade['id'], 'action_id': action_id, 'executed': True})
    assert armed.status_code == 200, armed.text
    assert engine.journal.get_trade(trade['id'])['status'] == 'open'
    with engine.position._lock, engine.position._conn:
        engine.position._conn.execute(
            "UPDATE llm_shadow_manual_actions SET parameters_json=? WHERE action_id=?",
            ('{"deadline_ts": 1, "close_fraction": 1}', action_id))
    payload = {'trade_id': trade['id'], 'action_id': action_id, 'executed': True, 'execution_price': 30708}
    result = client.post('/api/ai/shadow-action/ack', json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['journal_result_r'] == pytest.approx(1.5)
    assert result.json()['trade']['result_status'] == 'AVAILABLE'
    assert client.post('/api/ai/shadow-action/ack', json=payload).json()['idempotent'] is True
