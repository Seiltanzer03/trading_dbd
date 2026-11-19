from copy import deepcopy

import pytest

from scripts import production_functional_smoke as smoke
from seiltanzer.ai_api import AI_API_VERSION


def _case(monkeypatch):
    context = {'required': True, 'trade_id': 144, 'broker_state_verified': False,
               'commands': [{'decision_id': 'prior-close', 'policy': 'CLOSE_25',
                             'execution_status': 'pending_execution'}]}
    position = {'trade_id': 144, 'execution_reconciliation': context}
    body = {'ok': False, 'api_version': AI_API_VERSION,
            'error': {'code': 'broker_reconciliation_required', 'retriable': False},
            'reconciliation': deepcopy(context)}
    # These separate existing isolated-sandbox checks are outside this reader.
    for name in ('verify_management_ack_guard_contract', 'verify_trade_settlement_contract',
                 'verify_isolated_unified_management_contract'):
        monkeypatch.setattr(smoke, name, lambda: None)
    def read(path):
        assert path == '/api/position'
        return deepcopy(position)
    monkeypatch.setattr(smoke, 'assert_route', read)
    return position, body


def test_smoke_validates_real_reconciliation_without_waiting_for_unneeded_snapshot(monkeypatch, capsys):
    position, body = _case(monkeypatch)
    def must_not_wait():
        raise AssertionError('reconciliation needs no completed AI snapshot')
    monkeypatch.setattr(smoke, 'wait_for_ai_snapshot_ready', must_not_wait)
    monkeypatch.setattr(smoke, 'request_ai_verdict', lambda: (409, deepcopy(body), 9.))
    smoke.verify_ai_verdict()
    output = capsys.readouterr().out
    assert 'AI_VERDICT_RECONCILIATION_CONTRACT PASS' in output
    assert 'llm_availability_not_proven=True' in output
    assert position['execution_reconciliation']['commands'][0]['execution_status'] == 'pending_execution'


@pytest.mark.parametrize('broken', ['empty_commands', 'wrong_trade', 'changed_command', 'opinion'])
def test_pending_execution_cannot_make_invalid_smoke_response_pass(monkeypatch, broken):
    position, body = _case(monkeypatch)
    if broken == 'empty_commands':
        body['reconciliation']['commands'] = []
    elif broken == 'wrong_trade':
        body['reconciliation']['trade_id'] = 999
    elif broken == 'changed_command':
        body['reconciliation']['commands'][0]['decision_id'] = 'other-close'
    else:
        body['verdict'] = 'not a valid factual-state response'
    monkeypatch.setattr(smoke, 'wait_for_ai_snapshot_ready', lambda: {})
    monkeypatch.setattr(smoke, 'request_ai_verdict', lambda: (409, body, 9.))
    with pytest.raises(AssertionError):
        smoke.verify_ai_verdict()


def test_smoke_with_clear_ledger_preserves_snapshot_admission(monkeypatch):
    position, body = _case(monkeypatch)
    position['execution_reconciliation'] = None
    class SnapshotStillPending(Exception):
        pass
    def must_wait():
        raise SnapshotStillPending()
    monkeypatch.setattr(smoke, 'wait_for_ai_snapshot_ready', must_wait)
    def must_not_request():
        raise AssertionError('must wait for actual review inputs')
    monkeypatch.setattr(smoke, 'request_ai_verdict', must_not_request)
    with pytest.raises(SnapshotStillPending):
        smoke.verify_ai_verdict()
