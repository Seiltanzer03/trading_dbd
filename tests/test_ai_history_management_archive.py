import json

import pytest

from seiltanzer.journal import Journal


@pytest.fixture
def journal(tmp_path):
    value = Journal(str(tmp_path / 'archive.db'))
    yield value
    value.close()


def test_history_management_is_paired_with_exact_saved_text_and_read_only(journal):
    trade = journal.open_trade(3, 'NAS100', 'long', 100, 99, 102.5)
    for policy in ('HOLD', 'CLOSE_25'):
        snapshot = {'trade_id': trade['id'], 'captured_ts': 1700000000,
                    'policy_manager': {
                        'management_decision': {'policy': policy, 'status': 'pending',
                                                'decision_id': policy, 'private_extra': 'omit'},
                        'unified_edge_ensemble': {
                            'selected_policy': policy, 'selected_candidate_id': policy,
                            'candidates': [{'policy': policy, 'candidate_id': policy,
                                            'eligible': True, 'expected_net_r': .2}],
                            'paths': ['do not export']}}}
        journal.record_ai_verdict(trade['id'], snapshot, policy + ' report')
    before = journal._conn.total_changes
    plain = journal.recent_ai_verdicts(trade['id'], limit=10)
    assert 'management_archive' not in plain[0]
    rows = journal.recent_ai_verdicts(trade['id'], limit=10, include_management=True)
    for row, policy in zip(rows, ('HOLD', 'CLOSE_25')):
        archive = row['management_archive']
        assert row['verdict'] == policy + ' report'
        assert archive['available'] is True
        assert archive['execution_allowed'] is False
        assert archive['captured_ts'] == 1700000000
        assert archive['decision']['policy'] == policy
        assert 'private_extra' not in archive['decision']
        assert archive['unified_edge_ensemble']['selected_policy'] == policy
        assert 'paths' not in archive['unified_edge_ensemble']
        assert 'snapshot_json' not in row
    assert journal._conn.total_changes == before
    assert [r['verdict'] for r in journal.recent_ai_verdicts(trade['id'], limit=1,
            include_management=True)] == ['CLOSE_25 report']


@pytest.mark.parametrize('payload', ['bad json', '[]', '{}',
    json.dumps({'trade_id': 999, 'captured_ts': 1700000000, 'policy_manager': {}}),
    json.dumps({'trade_id': 1, 'captured_ts': 1700000000, 'policy_manager': []}),
    json.dumps({'trade_id': 1, 'captured_ts': 1700000000, 'policy_manager': {
        'unified_edge_ensemble': {'candidates': 'malformed'}}}),
])
def test_missing_invalid_or_wrong_trade_snapshot_does_not_invent_management(journal, payload):
    trade = journal.open_trade(3, 'NAS100', 'long', 100, 99, 102.5)
    journal.record_ai_verdict(trade['id'], {}, 'saved text')
    journal._conn.execute('UPDATE ai_verdicts SET snapshot_json=?', (payload,))
    row = journal.recent_ai_verdicts(trade['id'], include_management=True)[0]
    assert row['verdict'] == 'saved text'
    assert row['management_archive']['available'] is False
    assert row['management_archive']['execution_allowed'] is False
    assert 'snapshot_json' not in row


def test_history_api_reads_saved_management_without_quote_or_provider(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import seiltanzer.app as app_module
    from seiltanzer.config import Settings
    app = app_module.create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    try:
        trade = engine.journal.open_trade(3, 'NAS100', 'long', 100, 99, 102.5)
        engine.journal.record_ai_verdict(trade['id'], {
            'trade_id': trade['id'], 'captured_ts': 1700000000,
            'policy_manager': {'management_decision': {'policy': 'HOLD'}}}, 'same saved report')
        before = engine.journal._conn.total_changes
        def forbidden(*args, **kwargs):
            raise AssertionError('archive must not calculate, call provider, or publish')
        monkeypatch.setattr(app_module, 'build_snapshot', forbidden)
        monkeypatch.setattr(app_module, 'request_verdict', forbidden)
        monkeypatch.setattr(app_module, '_publish_unified_review', forbidden)
        with TestClient(app) as client:
            plain = client.get('/api/ai/history').json()
            assert 'management_archive' not in plain['items'][0]
            response = client.get('/api/ai/history?include_management=true')
            assert response.status_code == 200
            body = response.json()
            assert body['trade_id'] == trade['id']
            assert body['items'][0]['verdict'] == 'same saved report'
            assert body['items'][0]['management_archive']['decision']['policy'] == 'HOLD'
            assert engine.journal._conn.total_changes == before
    finally:
        engine.close()
