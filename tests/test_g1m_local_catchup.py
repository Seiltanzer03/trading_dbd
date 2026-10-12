from seiltanzer.g1_management_local_runtime import ManagementLocalRuntime
from test_g1m_local_feedback import _make_runtime, _insert_source


def test_materialize_small_batches_reaches_later_management_observations(tmp_path):
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    for i in range(3):
        _insert_source(engine, management, trade_id=i + 1, captured_ts=local.activation_ts + i + 1)
    assert [local.materialize_windows(limit=1) for _ in range(4)] == [4, 4, 4, 0]
    c = engine.passive._conn
    assert c.execute('SELECT COUNT(DISTINCT observation_id) FROM g1m_local_windows').fetchone()[0] == 3
    assert c.execute('SELECT COUNT(*) FROM g1m_local_windows').fetchone()[0] == 12


def test_incomplete_first_observation_is_repaired_before_next(tmp_path):
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    for i in range(2):
        _insert_source(engine, management, trade_id=i + 1, captured_ts=local.activation_ts + i + 1)
    # Stop a genuine INSERT sequence after its first committed immutable window.
    c = engine.passive._conn
    c.executescript("""
      CREATE TRIGGER fixture_interrupt BEFORE INSERT ON g1m_local_windows
      WHEN NEW.horizon_minutes=30
      BEGIN SELECT RAISE(ABORT,'fixture interrupt'); END;
    """)
    import pytest
    with pytest.raises(Exception, match='fixture interrupt'):
        local.materialize_windows(limit=1)
    c.execute('DROP TRIGGER fixture_interrupt')
    before = tuple(c.execute('SELECT * FROM g1m_local_windows').fetchone())
    assert local.materialize_windows(limit=1) == 3
    assert tuple(c.execute('SELECT * FROM g1m_local_windows ORDER BY captured_ts,horizon_minutes').fetchone()) == before
    assert local.materialize_windows(limit=1) == 4


def test_missing_older_path_does_not_starve_later_resolvable_window(tmp_path):
    from seiltanzer.passive_learning import _advance_trading_time
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    base = _advance_trading_time('NAS100', local.activation_ts + 1, 1) + 60
    _insert_source(engine, management, trade_id=1, captured_ts=base)
    review, _ = _insert_source(engine, management, trade_id=2, captured_ts=base + 61)
    local.materialize_windows()
    c = engine.passive._conn
    target = c.execute('SELECT target_ts FROM g1m_local_windows WHERE trade_id=2 AND horizon_minutes=15').fetchone()[0]
    c.executemany('INSERT INTO decision_path_points VALUES(?,?,?,?)',
        [(review, base + 61, 102., .2), (review, target, 108., .8)])
    c.commit()
    assert local.resolve_due(now=target + 1, limit=1) == 0
    assert local.resolve_due(now=target + 1, limit=1) == 1
    assert c.execute('SELECT COUNT(*) FROM g1m_local_outcomes').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM g1m_local_windows w JOIN g1m_local_outcomes o USING(window_id) WHERE w.trade_id=1').fetchone()[0] == 0
    assert local.resolve_due(now=target + 1, limit=1) == 0


def test_unavailable_first_trading_clock_is_retried_without_starving_later_source(tmp_path, monkeypatch):
    from seiltanzer import g1_fast_learning_integrity_refinement as integrity
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    base = local.activation_ts + 1
    for i in range(2):
        _insert_source(engine, management, trade_id=i + 1, captured_ts=base + i)
    clock = integrity._pl._advance_trading_time
    def unavailable_clock(code, captured, horizon):
        if captured == base:
            raise ValueError('fixture clock unavailable')
        return clock(code, captured, horizon)
    monkeypatch.setattr(integrity._pl, '_advance_trading_time', unavailable_clock)
    assert local.materialize_windows(limit=1) == 0
    assert local.materialize_windows(limit=1) == 4
    monkeypatch.undo()
    assert local.materialize_windows(limit=1) == 4
