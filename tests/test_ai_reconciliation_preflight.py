import time

import pytest

from seiltanzer import app as app_module
from seiltanzer.ai_review_jobs import install_ai_review_jobs
from seiltanzer.ai_snapshot_materializer import install_ai_snapshot_materializer
from seiltanzer.ai_snapshot_runtime_guard import install_ai_snapshot_runtime_guard
from test_ai_verdict_api import _client
from test_decision_publication import _snapshot, _extended


def _production_stack(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('reconciliation must precede snapshot/provider work')
    app, client = _client(tmp_path, monkeypatch, forbidden)
    monkeypatch.setattr(app_module, 'build_snapshot', forbidden)
    materializer = install_ai_snapshot_materializer(app)
    install_ai_snapshot_runtime_guard(app, materializer)
    install_ai_review_jobs(app)
    return app, client, materializer


def _pending(engine, policy):
    trade = engine.journal.active_trade()
    snapshot = _snapshot(engine.position, trade,
                         policy if policy != 'TIGHTEN_STOP' else 'HOLD', time.time())
    if policy == 'TIGHTEN_STOP':
        result = engine.position.register_shadow_action(snapshot, 'prior', trade, _extended(95.))
        return result['action_id']
    return engine.position.register_decision(snapshot, 'prior', trade)['decision_id']


@pytest.mark.parametrize('reason', ['INITIAL_BUILD', 'COMPLETED_MARKET_BAR_CHANGED',
                                  'EXPERT_PUBLICATION_CHANGED'])
@pytest.mark.parametrize('policy', ['CLOSE_25', 'EXIT', 'TIGHTEN_STOP'])
def test_production_preflight_exposes_pending_before_retryable_warmup(
        tmp_path, monkeypatch, reason, policy):
    app, client, materializer = _production_stack(tmp_path, monkeypatch)
    engine = app.state.engine
    try:
        identity = _pending(engine, policy)
        trade = engine.journal.active_trade()
        before = engine.position.state(trade)
        materializer._invalidated_reason = reason
        materializer._building = True
        for _ in range(3):
            response = client.post('/api/ai/verdict')
            assert response.status_code == 409, response.text
            body = response.json()
            assert body['error']['code'] == 'broker_reconciliation_required'
            assert body['error']['retriable'] is False
            assert body['error']['request_id'].startswith('ai-')
            assert body['reconciliation']['trade_id'] == 1
            assert body['reconciliation']['broker_state_verified'] is False
            assert [(c['decision_id'], c['policy'], c['execution_status'])
                    for c in body['reconciliation']['commands']] == [
                        (identity, policy, 'pending_execution')]
        assert engine.position.state(trade) == before
        assert before['remaining_position_fraction'] == 1.
        assert engine.journal._conn.execute('SELECT COUNT(*) FROM ai_verdicts').fetchone()[0] == 0
        assert app.state.ai_review_jobs.task is None
        assert not materializer._wake.is_set()
    finally:
        engine.close()


def test_explicit_non_execution_releases_preflight_without_bypassing_warmup(tmp_path, monkeypatch):
    app, client, materializer = _production_stack(tmp_path, monkeypatch)
    engine = app.state.engine
    try:
        identity = _pending(engine, 'CLOSE_25')
        assert client.post('/api/ai/verdict').status_code == 409
        ack = client.post('/api/ai/decision/ack', json={
            'trade_id': 1, 'decision_id': identity, 'executed': False})
        assert ack.status_code == 200
        assert ack.json()['position_state']['remaining_position_fraction'] == 1.
        response = client.post('/api/ai/verdict')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'ai_snapshot_warming'
        assert response.json()['error']['retriable'] is True
        assert materializer._wake.is_set()
    finally:
        engine.close()


def test_clear_ledger_still_requires_ready_snapshot(tmp_path, monkeypatch):
    app, client, materializer = _production_stack(tmp_path, monkeypatch)
    try:
        response = client.post('/api/ai/verdict')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'ai_snapshot_warming'
        assert app.state.ai_review_jobs.task is None
    finally:
        app.state.engine.close()


def test_production_no_trade_keeps_existing_fast_error(tmp_path, monkeypatch):
    app, client, materializer = _production_stack(tmp_path, monkeypatch)
    try:
        app.state.engine.journal.close_trade(1, 0.)
        response = client.post('/api/ai/verdict')
        assert response.status_code == 400
        assert response.json()['error']['code'] == 'no_active_trade'
        assert app.state.ai_review_jobs.task is None
    finally:
        app.state.engine.close()
