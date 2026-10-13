import threading

import pytest
from fastapi.testclient import TestClient

from seiltanzer.app import create_app
from seiltanzer.config import Settings
from seiltanzer import macro_data_factory_routes as routes
from seiltanzer.macro_fomc_deterministic_bootstrap import FOMCStatementSpec


PATH = '/api/research/macro/fomc-deterministic/latest'


def _app(tmp_path, monkeypatch):
    # Only background acquisition is disabled; HTTP and SQLite readers are real.
    for runtime in (routes.NumericMacroRuntime, routes.FOMCOfficialRuntime,
                    routes.BLSHistoricalBootstrapRuntime,
                    routes.ISMHistoricalBootstrapRuntime,
                    routes.FOMCDeterministicBootstrapRuntime,
                    routes.TreasuryLiveRuntime):
        monkeypatch.setattr(runtime, 'start', lambda self: None)
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    routes.install_macro_data_factory_routes(app)
    return app


def test_latest_http_reports_busy_before_shared_writer_releases(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    acquired, release, completed = (threading.Event() for _ in range(3))
    responses = []

    def hold_writer():
        with app.state.engine.short_horizon._lock:
            acquired.set()
            release.wait(3)

    def read_http():
        try:
            responses.append(TestClient(app).get(PATH))
        finally:
            completed.set()

    writer = threading.Thread(target=hold_writer)
    reader = threading.Thread(target=read_http)
    writer.start()
    assert acquired.wait(1)
    reader.start()
    try:
        assert completed.wait(1), 'FOMC HTTP reader waited for the shared writer'
        assert responses[0].status_code == 200
        assert responses[0].json() == {
            'status': 'UNAVAILABLE', 'reason': 'FOMC_RELEASE_STORE_BUSY'}
    finally:
        release.set()
        writer.join(3)
        reader.join(3)
        app.state.engine.close()


@pytest.mark.parametrize('offset', [-1, 0, 100])
def test_latest_http_preserves_original_causal_record_when_unlocked(
        tmp_path, monkeypatch, offset):
    app = _app(tmp_path, monkeypatch)
    store = app.state.macro_fomc_deterministic_store
    html = '''<div>For release at 2:00 p.m. EDT</div>
    <p>The Federal Open Market Committee decided to maintain the target range
    for the federal funds rate at 4-1/4 to 4-1/2 percent.</p>'''
    spec = FOMCStatementSpec('20260429',
        'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm')
    try:
        receipt = store.ingest(spec, html=html, fetched_at=2_000_000_000.)
        cutoff = receipt['published_at'] + offset
        expected = store.latest_admissible(cutoff)
        response = TestClient(app).get(PATH, params={'captured_ts': cutoff})
        assert response.status_code == 200
        assert response.json() == expected
        if offset >= 0:
            assert expected['available_at'] == receipt['published_at']
            assert expected['fetched_at'] == 2_000_000_000.
            assert expected['production_authority'] is False
            assert expected['historical_reconstruction'] is True
        else:
            assert expected['reason'] == 'NO_FOMC_STATEMENT_BEFORE_T0'
    finally:
        app.state.engine.close()
