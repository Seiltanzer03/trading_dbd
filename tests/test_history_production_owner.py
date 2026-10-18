"""Actual SQLite precommit reserve failure must restore the exact original."""
import pytest

from scripts import sqlite_history_transaction as core
from test_sqlite_history_transaction import setup_transaction


@pytest.mark.parametrize('failure',['false','exception'])
def test_actual_precommit_resource_refusal_restores_original(setup_transaction,failure):
    root,plan,transport,control,original,hot = setup_transaction
    phases = []
    def admission():
        phases.append(core._read(root/'cutover.journal.json')['phase'])
        assert (root/'trades.db').read_bytes() == hot
        if failure == 'exception':
            raise ValueError('measured reserve unavailable')
        return False
    with core._owned_lock(root):
        result = core._execute_validated_transfer(root,core._plan_bytes(plan),
            transport=transport,control=control,precommit=admission)
    assert phases == ['HOT_VERIFIED']
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original
    assert control.starts == 1
    assert transport.calls == ['hot','original']


def test_precommit_fence_loss_never_restores_old_over_new_writer(setup_transaction):
    root,plan,transport,control,_,hot = setup_transaction
    def admission():
        control.closed = False
        raise core.FencingError('writer exclusion lost')
    with core._owned_lock(root),pytest.raises(core.FencingError):
        core._execute_validated_transfer(root,core._plan_bytes(plan),
            transport=transport,control=control,precommit=admission)
    assert core._read(root/'cutover.journal.json')['phase'] == 'FENCE_LOST_GUARDED'
    assert (root/'trades.db').read_bytes() == hot
    assert transport.calls == ['hot']
    assert control.starts == 0
