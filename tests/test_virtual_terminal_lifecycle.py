"""Missing market outcomes must never keep impossible virtual labels pending."""
import json
import sqlite3

import pytest

from test_passive_learning import passive,fixture_forecast


def capture(engine,ts=1_700_000_000.):
    engine.capture_observation(instrument='NAS100',captured_ts=ts,market_price=100,
        features={'source_observation_ts':ts},forecast=fixture_forecast(ts),
        provenance={},trigger_reason='test')
    row=engine._conn.execute('SELECT p.observation_id,p.target_ts FROM passive_market_observations p JOIN virtual_position_observations v ON v.passive_observation_id=p.observation_id ORDER BY p.target_ts LIMIT 1').fetchone()
    return row['observation_id'],row['target_ts']


def test_terminal_failure_propagates_without_an_outcome_or_backdated_clock(passive):
    parent,target=capture(passive);now=target+301
    before=[tuple(r) for r in passive._conn.execute('SELECT virtual_id,features_json FROM virtual_position_observations WHERE passive_observation_id=?',(parent,))]
    passive.resolve_due(now=now)
    rows=passive._conn.execute('SELECT resolution_status,resolved_ts,outcome_json FROM virtual_position_observations WHERE passive_observation_id=?',(parent,)).fetchall()
    assert rows and all(tuple(r)==('insufficient_future_data',now,None) for r in rows)
    assert passive.virtual_management_report()['raw_n']==0
    assert before==[tuple(r) for r in passive._conn.execute('SELECT virtual_id,features_json FROM virtual_position_observations WHERE passive_observation_id=?',(parent,))]
    passive.resolve_due(now=now+1)
    assert all(r[0]==now for r in passive._conn.execute('SELECT resolved_ts FROM virtual_position_observations WHERE passive_observation_id=?',(parent,)))


def test_parent_child_failure_transition_is_atomic(passive):
    parent,target=capture(passive)
    with passive._conn:
        passive._conn.execute("CREATE TRIGGER reject_virtual_failure BEFORE UPDATE ON virtual_position_observations WHEN NEW.resolution_status='insufficient_future_data' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='test failure'):passive.resolve_due(now=target+301)
    assert passive._conn.execute('SELECT resolution_status FROM passive_market_observations WHERE observation_id=?',(parent,)).fetchone()[0]=='pending'


@pytest.mark.parametrize('other_parent_status',['pending','resolved'])
def test_bounded_legacy_scan_persists_progress_and_leaves_valid_states_untouched(passive,other_parent_status):
    parent,target=capture(passive);now=target+1000
    with passive._conn:
        passive._conn.execute("UPDATE passive_market_observations SET resolution_status='insufficient_future_data',resolved_ts=? WHERE observation_id=?",(target+301,parent))
        # A pre-existing non-null outcome is never rewritten as missing data.
        first=passive._conn.execute('SELECT virtual_id FROM virtual_position_observations WHERE passive_observation_id=? ORDER BY rowid LIMIT 1',(parent,)).fetchone()[0]
        passive._conn.execute('UPDATE virtual_position_observations SET outcome_json=? WHERE virtual_id=?',('{"preserve":true}',first))
        passive._conn.execute('UPDATE passive_market_observations SET resolution_status=? WHERE observation_id IN (SELECT passive_observation_id FROM virtual_position_observations WHERE passive_observation_id<>?)',(other_parent_status,parent))
    seen=changed=0
    while True:
        result=passive.reconcile_terminal_virtuals(now=now,limit=3)
        assert result['scanned_rows']<=3
        seen+=result['scanned_rows'];changed+=result['marked_unavailable']
        if result['scan_complete']:break
    assert seen==16 and changed==7
    rows=passive._conn.execute('SELECT resolution_status,resolved_ts,outcome_json FROM virtual_position_observations WHERE passive_observation_id=? AND virtual_id<>?',(parent,first)).fetchall()
    assert all(tuple(r)==('insufficient_future_data',now,None) for r in rows)
    assert passive._conn.execute('SELECT outcome_json FROM virtual_position_observations WHERE virtual_id=?',(first,)).fetchone()[0]=='{"preserve":true}'
    other=passive._conn.execute('SELECT COUNT(*) FROM virtual_position_observations WHERE passive_observation_id<>? AND resolution_status=?',(parent,'pending')).fetchone()[0]
    assert other==8
    assert passive.reconcile_terminal_virtuals(now=now+1,limit=3)['scanned_rows']==0


def test_legacy_scan_refuses_low_headroom_without_cursor_writes(passive,monkeypatch):
    from types import SimpleNamespace
    capture(passive)
    monkeypatch.setattr('seiltanzer.passive_learning.shutil.disk_usage',lambda _:SimpleNamespace(free=1024**3-1))
    result=passive.reconcile_terminal_virtuals(now=1_700_050_000.,limit=100)
    assert result['reason']=='WAL_HEADROOM_UNAVAILABLE'
    assert passive._conn.execute("SELECT COUNT(*) FROM passive_collector_state WHERE key='virtual_terminal_reconciliation_v1'").fetchone()[0]==0


def test_legacy_scan_is_called_by_existing_bounded_resolution_worker(passive,monkeypatch):
    seen=[]
    monkeypatch.setattr(passive,'reconcile_terminal_virtuals',lambda **kw:seen.append(kw))
    assert passive.resolve_due(now=1_700_000_000.,limit=20)=={}
    assert seen==[dict(now=1_700_000_000.,limit=20)]


def test_reconciliation_cursor_survives_engine_reopen(passive):
    from seiltanzer.passive_learning import PassiveLearningEngine
    capture(passive)
    one=passive.reconcile_terminal_virtuals(now=1_700_050_000.,limit=3)
    other=PassiveLearningEngine(str(passive.settings.trades_db),passive.settings,passive.cache)
    try:
        two=other.reconcile_terminal_virtuals(now=1_700_050_001.,limit=3)
        assert two['scanned_rows']==3 and two['next_rowid']>one['next_rowid']
    finally:other.close()


def test_cursor_failure_rolls_back_legacy_status_updates(passive):
    parent,target=capture(passive)
    with passive._conn:
        passive._conn.execute("UPDATE passive_market_observations SET resolution_status='insufficient_future_data' WHERE observation_id=?",(parent,))
        passive._conn.execute("CREATE TRIGGER reject_cursor BEFORE INSERT ON passive_collector_state BEGIN SELECT RAISE(ABORT,'cursor failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='cursor failure'):
        passive.reconcile_terminal_virtuals(now=target+1000,limit=100)
    assert passive._conn.execute("SELECT COUNT(*) FROM virtual_position_observations WHERE resolution_status='pending'").fetchone()[0]==16


@pytest.mark.parametrize('clock',[float('nan'),float('inf'),-1,True])
def test_reconciliation_never_mints_invalid_clocks(passive,clock):
    capture(passive)
    with pytest.raises(ValueError):passive.reconcile_terminal_virtuals(now=clock)
