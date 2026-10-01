"""Exercise the real ACK route with the production terminal guard installed."""
import time

import pytest
from fastapi.testclient import TestClient

from seiltanzer.app import create_app
from seiltanzer.config import Settings
from seiltanzer.position_state import PositionLedger
from seiltanzer import strategy_terminal_guard as guard


@pytest.mark.parametrize('broker_price', [None, 30833.0])
@pytest.mark.parametrize('executed', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
def test_ack_route_accepts_fill_provenance_with_production_guard(
    tmp_path, monkeypatch, broker_price, executed, terminal,
):
    # create_app alone does not install the __main__ production guards.
    monkeypatch.setattr(guard, '_INSTALLED', False)
    monkeypatch.setattr(PositionLedger, 'preview_decision', PositionLedger.preview_decision)
    monkeypatch.setattr(PositionLedger, 'acknowledge', PositionLedger.acknowledge)
    guard.install_strategy_terminal_guard()
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    try:
        trade = engine.journal.open_trade(3, 'NAS100', 'long', 30500, 30396, 30770)
        engine.position.open_trade(trade)
        # The screenshot has 50% of the original position left.
        with engine.position._lock, engine.position._conn:
            engine.position._event(trade=trade, event_type='MANUAL_REDUCTION',
                source='test_broker_fill', before=1, closed=.5, after=.5,
                execution_price=30470, execution_r=-30/104)
        quote = 30845.0 if terminal else 30590.0
        monkeypatch.setattr(engine, 'tick_payload', lambda: {
            'feeds': {'price': {'value': quote}}, 'prob': {'r': (quote-30500)/104}})
        snapshot = {'captured_ts': time.time(),
                    'position_state': engine.position.state(trade),
                    'trade_geometry': {'current': quote},
                    'policy_manager': {'recommendation': {'policy': 'CLOSE_50'}}}
        decision = engine.position.preview_decision(snapshot, trade)
        snapshot['policy_manager']['management_decision'] = decision
        engine.position.register_decision(snapshot, 'review-api', trade)
        assert decision['policy'] == ('EXIT' if terminal else 'CLOSE_50')
        request = {'trade_id': trade['id'], 'decision_id': decision['decision_id'],
                   'executed': executed}
        if broker_price is not None:
            request['execution_price'] = broker_price
        client = TestClient(app)
        response = client.post('/api/ai/decision/ack', json=request)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['execution_status'] == ('executed' if executed else 'recommended_not_executed')
        expected = (0.0 if terminal else .25) if executed else .5
        assert result['position_state']['remaining_position_fraction'] == expected
        if executed:
            event = engine.position.events(trade['id'])[-1]
            assert event['event_type'] == ('TAKE_EXIT' if terminal else 'AI_CLOSE_50')
            assert event['execution_price'] == (broker_price if broker_price is not None else quote)
            assert event['metadata']['execution_price_source'] == (
                'user_supplied_broker_fill' if broker_price is not None
                else 'quote_at_acknowledgement_estimate')
            count = len(engine.position.events(trade['id']))
            repeat = client.post('/api/ai/decision/ack', json=request)
            assert repeat.status_code == 200 and repeat.json()['idempotent'] is True
            assert len(engine.position.events(trade['id'])) == count
    finally:
        engine.close()
