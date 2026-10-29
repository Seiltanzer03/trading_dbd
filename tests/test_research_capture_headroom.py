"""Optional research writes must not consume the operational recovery reserve."""
from types import SimpleNamespace

from seiltanzer import research_capture_headroom as owner


def space(monkeypatch, value):
    monkeypatch.setattr(owner.shutil, 'disk_usage', lambda _: SimpleNamespace(free=value))


def test_preserves_existing_reserve_and_batch_margin(tmp_path, monkeypatch):
    from scripts.history_production_plan import MIN_FREE_BYTES
    assert owner.OPERATIONAL_RESERVE_BYTES == MIN_FREE_BYTES
    space(monkeypatch, MIN_FREE_BYTES + owner.BATCH_MARGIN_BYTES - 1)
    result = owner.capture_admission(tmp_path/'db')
    assert result['admitted'] is False
    assert result['reason'] == 'RESEARCH_CAPTURE_PAUSED_LOW_STORAGE'
    assert result['minimum_free_bytes'] == MIN_FREE_BYTES + owner.BATCH_MARGIN_BYTES
    space(monkeypatch, result['minimum_free_bytes'])
    assert owner.capture_admission(tmp_path/'db')['admitted'] is True


def test_hysteresis_prevents_repeated_pause_resume(tmp_path, monkeypatch):
    low = owner.OPERATIONAL_RESERVE_BYTES + owner.BATCH_MARGIN_BYTES
    space(monkeypatch, low + 1)
    assert owner.capture_admission(tmp_path/'db', previously_paused=True)['admitted'] is False
    space(monkeypatch, low + owner.RESUME_MARGIN_BYTES)
    assert owner.capture_admission(tmp_path/'db', previously_paused=True)['admitted'] is True


def test_unknown_capacity_pauses_only_optional_capture(tmp_path, monkeypatch):
    def fail(_): raise OSError('unavailable')
    monkeypatch.setattr(owner.shutil, 'disk_usage', fail)
    result = owner.capture_admission(tmp_path/'db')
    assert result['admitted'] is False
    assert result['reason'] == 'RESEARCH_CAPTURE_CAPACITY_UNAVAILABLE'
    assert result['free_bytes'] is None


def test_explicit_in_memory_has_no_disk_budget(monkeypatch):
    def fail(_): raise AssertionError('in-memory DB must not probe disk')
    monkeypatch.setattr(owner.shutil, 'disk_usage', fail)
    assert owner.capture_admission(':memory:')['admitted'] is True


def test_large_batch_preserves_full_batch_reserve(tmp_path, monkeypatch):
    batch = owner.BATCH_MARGIN_BYTES * 2
    space(monkeypatch, owner.OPERATIONAL_RESERVE_BYTES + batch - 1)
    assert owner.capture_admission(tmp_path/'db', batch_bytes=batch)['admitted'] is False


def test_measurement_follows_actual_database_filesystem(tmp_path, monkeypatch):
    target = tmp_path/'volume'/'trades.db'
    target.parent.mkdir(); target.touch()
    link = tmp_path/'trades.db'; link.symlink_to(target)
    measured = []
    monkeypatch.setattr(owner.shutil, 'disk_usage',
        lambda directory: measured.append(directory) or SimpleNamespace(free=4*1024**3))
    assert owner.capture_admission(link)['admitted'] is True
    assert measured == [target.parent]


def make_passive(tmp_path):
    from seiltanzer.config import Settings
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.passive_learning import PassiveLearningEngine
    cache = DiskCache(str(tmp_path/'cache.db'))
    return PassiveLearningEngine(str(tmp_path/'trades.db'),
        Settings(demo=True,data_dir=str(tmp_path)),cache), cache


def capture(passive):
    from test_g1_operational_integrity import _capture
    return _capture(passive,2_000_000_000.)


def test_actual_passive_capture_pauses_and_resumes_with_price_paths_intact(tmp_path, monkeypatch):
    passive, cache = make_passive(tmp_path)
    try:
        space(monkeypatch,0)
        assert capture(passive) == []
        assert passive._conn.execute('SELECT count(*) FROM passive_market_observations').fetchone()[0] == 0
        status = passive.status()
        assert status['storage_capture']['admitted'] is False
        assert status['collector_status'] == 'paused_storage'
        passive.record_market_point('NAS100',2_000_000_000.,101.)
        passive.record_market_bar('NAS100',2_000_000_000.-60,2_000_000_000.,100.,102.,99.,101.)
        assert passive._conn.execute('SELECT count(*) FROM passive_market_path').fetchone()[0] == 1
        assert passive._conn.execute('SELECT count(*) FROM passive_market_bars').fetchone()[0] == 1
        space(monkeypatch,4*1024**3)
        assert capture(passive)
        assert passive.status()['storage_capture']['admitted'] is True
        assert passive.status()['collector_status'] != 'paused_storage'
        space(monkeypatch,0)
        before = passive._conn.execute('SELECT count(*) FROM passive_market_observations').fetchone()[0]
        assert capture(passive) == []
        passive.resolve_due(now=2_000_000_000.+7*86400)
        assert passive._conn.execute('SELECT count(*) FROM passive_market_observations').fetchone()[0] == before
        assert passive._conn.execute("SELECT count(*) FROM passive_market_observations WHERE resolution_status='insufficient_future_data'").fetchone()[0] > 0
    finally:
        passive.close();cache.close()


def test_actual_g1s_pause_preserves_source_cursor_and_resumes(tmp_path, monkeypatch):
    from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
    passive, cache = make_passive(tmp_path)
    try:
        space(monkeypatch,4*1024**3)
        assert capture(passive)
        runtime = ShortHorizonRuntime(SimpleNamespace(passive=passive))
        before = runtime._watermark()
        space(monkeypatch,0)
        assert runtime.materialize_new() == 0
        assert runtime._watermark() == before
        assert runtime.materializer_status()['storage_capture']['admitted'] is False
        assert passive._conn.execute('SELECT count(*) FROM g1s_observations').fetchone()[0] == 0
        space(monkeypatch,4*1024**3)
        assert runtime.materialize_new() > 0
        assert runtime._watermark() > before
        assert runtime.materializer_status()['storage_capture']['admitted'] is True
    finally:
        passive.close();cache.close()


def test_actual_cached_passive_status_exposes_storage_pause_without_sql_on_read():
    from test_g1_operational_status_passthrough import _Passive, _app
    from seiltanzer.g1_operational_status_passthrough import install_operational_status_passthrough
    passive = _Passive()
    passive._storage_capture_admission = dict(admitted=False,free_bytes=0,
        minimum_free_bytes=owner.OPERATIONAL_RESERVE_BYTES+owner.BATCH_MARGIN_BYTES,
        reason='RESEARCH_CAPTURE_PAUSED_LOW_STORAGE')
    app = _app(passive)
    install_operational_status_passthrough(app)
    passive._conn.close()
    body = passive.status()  # A closed connection proves the reader is cached.
    assert body['storage_capture'] == passive._storage_capture_admission
    assert body['collector_status'] == 'paused_storage'
    assert body['collector_health']['operational_status'] == 'PAUSED_STORAGE'


def test_actual_g1s_cached_status_exposes_latest_admission_without_sql(tmp_path):
    from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
    from seiltanzer.g1_short_horizon_status_nonblocking import install_g1_short_horizon_status_nonblocking
    passive, cache = make_passive(tmp_path)
    try:
        runtime = ShortHorizonRuntime(SimpleNamespace(passive=passive))
        install_g1_short_horizon_status_nonblocking(runtime)
        runtime._storage_capture_admission = dict(admitted=False,reason='RESEARCH_CAPTURE_PAUSED_LOW_STORAGE')
        passive._conn.close()
        assert runtime.status()['storage_capture']['admitted'] is False
        runtime._g1s_status_snapshot_json = ''
        assert runtime.status()['storage_capture']['admitted'] is False
    finally:
        passive.close();cache.close()
