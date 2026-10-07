"""Artificial HTTP/native-store fixtures; no live forecasting claim."""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from seiltanzer.edge_family_sources import SourceBudget
from seiltanzer.macro_fomc_deterministic_bootstrap import FOMCStatementSpec
from seiltanzer.macro_fomc_deterministic_store_refinement import StrictFOMCDeterministicReleaseStore

SHA = '1' * 40
NOW = 1780000000.
DATES = ('20260318', '20260429')


def module():
    name = 'seiltanzer.fomc_prospective_capture'
    assert importlib.util.find_spec(name) is not None, 'bounded prospective collector missing'
    return importlib.import_module(name)


def page(word='maintain'):
    return ('<div>For release at 2:00 p.m. EDT</div><p>The Committee decided to '
            + word + ' the target range for the federal funds rate at 4.25 to 4.50 percent.'
            + '</p><p>For media inquiries, call the Board.</p>').encode()


def url(date):
    return 'https://www.federalreserve.gov/newsevents/pressreleases/monetary' + date + 'a.htm'


def collect(*, fail_latest=False, revised=False, advancing=False):
    calls = []
    def fetch(address, **kwargs):
        calls.append(address)
        if 'press-fomc' in address:
            return ''.join('<a href="' + url(d) + '">Statement</a>' for d in DATES).encode()
        if fail_latest and address == url(DATES[-1]):
            raise ValueError('NEWEST_INVALID')
        return page('reduce' if revised and address == url(DATES[-1]) else 'maintain')
    capture = module().build_capture(expected_sha=SHA, fetch=fetch,
        clock=lambda: NOW + len(calls) if advancing else NOW,
        budget=SourceBudget(min_interval=0, seconds=60))
    return capture, calls


def test_capture_has_actual_receipts_exact_previous_and_no_materialization_clock():
    capture, calls = collect(advancing=True)
    assert len(calls) == capture['collection_limits']['requests'] == 3
    assert capture['captured_ts'] == NOW + 3
    assert capture['records'][0]['fetched_at'] == NOW + 2
    assert capture['records'][1]['fetched_at'] == NOW + 3
    assert capture['records'][1]['previous_source_url'] == url(DATES[0])
    assert all('created_ts' not in row for row in capture['records'])
    assert module().validate_capture(capture, expected_sha=SHA, now=NOW+3) == capture


def test_invalid_newest_does_not_return_an_older_pair():
    with pytest.raises(ValueError, match='NEWEST_INVALID'):
        collect(fail_latest=True)


@pytest.mark.parametrize('mutation', ['hash', 'future_receipt', 'wrong_sha', 'wrong_previous', 'wrong_index'])
def test_tampered_capture_is_rejected_even_if_envelope_rehashed(mutation):
    capture, _ = collect()
    changed = deepcopy(capture)
    if mutation == 'hash':
        changed['records'][-1]['html'] += 'changed'
    elif mutation == 'future_receipt':
        changed['records'][-1]['fetched_at'] = NOW + 1
    elif mutation == 'wrong_sha':
        changed['code_sha'] = '2' * 40
    elif mutation == 'wrong_previous':
        changed['records'][-1]['previous_source_url'] = url('20260319')
    else:
        row = changed['schedules'][0]
        row['html'] = row['html'].replace(DATES[-1], '20260430')
        row['body_sha256'] = hashlib.sha256(row['html'].encode()).hexdigest()
    changed['capture_sha256'] = module().capture_digest(changed)
    with pytest.raises(ValueError):
        module().validate_capture(changed, expected_sha=SHA, now=NOW)


def test_native_import_preserves_first_receipt_and_retains_changed_body(monkeypatch):
    runtime = SimpleNamespace(_conn=sqlite3.connect(':memory:'), _lock=threading.RLock())
    store = StrictFOMCDeterministicReleaseStore(runtime)
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: NOW+10)
    capture, _ = collect()
    assert module().ingest_capture(store, capture, expected_sha=SHA, now=NOW+10)['stored'] == 2
    first = runtime._conn.execute('SELECT fetched_at,created_ts,body_sha256 FROM macro_fomc_deterministic_releases ORDER BY date_code').fetchall()
    assert first[0][0] == NOW and first[0][1] == NOW+10
    assert module().ingest_capture(store, capture, expected_sha=SHA, now=NOW+11)['skipped'] == 2
    revised, _ = collect(revised=True)
    revised['captured_ts'] = NOW+20
    revised['records'][-1]['fetched_at'] = NOW+20
    revised['capture_sha256'] = module().capture_digest(revised)
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: NOW+21)
    assert module().ingest_capture(store, revised, expected_sha=SHA, now=NOW+21)['stored'] == 1
    assert runtime._conn.execute('SELECT COUNT(*) FROM macro_fomc_deterministic_releases').fetchone()[0] == 3
    assert runtime._conn.execute('SELECT fetched_at,created_ts,body_sha256 FROM macro_fomc_deterministic_releases ORDER BY created_ts LIMIT 2').fetchall() == first


def test_stale_capture_cannot_materialize_as_fresh():
    capture, _ = collect()
    with pytest.raises(ValueError, match='STALE'):
        module().validate_capture(capture, expected_sha=SHA, now=NOW+3601)


def test_background_reader_binds_publication_and_blocks_old_native_pair_on_failure(monkeypatch, tmp_path):
    assert hasattr(module(), 'ProspectiveFOMCRuntime'), 'local background delivery missing'
    runtime = SimpleNamespace(_conn=sqlite3.connect(':memory:'), _lock=threading.RLock())
    store = StrictFOMCDeterministicReleaseStore(runtime)
    path = tmp_path / 'fomc.json'
    worker = module().ProspectiveFOMCRuntime(store, path, code_sha=lambda: SHA, clock=lambda: NOW+10)
    capture, _ = collect()
    capture.update(publication_contract_version='active-edge-exact-sha-publication-v1', published_for_sha=SHA)
    path.write_text(json.dumps(capture))
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: NOW+10)
    assert worker.refresh()['status'] == 'OK'
    assert module().prospective_admission_reason(store, NOW+11) is None
    assert module().prospective_admission_reason(store, NOW+9)
    assert module().prospective_admission_reason(store, NOW+3601)
    capture['published_for_sha'] = '2' * 40
    path.write_text(json.dumps(capture))
    assert worker.refresh()['status'] == 'UNAVAILABLE'
    assert worker.status()['admissible_for_new_snapshot'] is False
    assert worker.status()['admission_reason'] == 'FOMC_PROSPECTIVE_CAPTURE_REJECTED'
    assert module().prospective_admission_reason(store, NOW+11)
    assert runtime._conn.execute('SELECT COUNT(*) FROM macro_fomc_deterministic_releases').fetchone()[0] == 2


def test_worker_status_reports_current_admission_without_io(monkeypatch, tmp_path):
    runtime = SimpleNamespace(_conn=sqlite3.connect(':memory:'), _lock=threading.RLock())
    store = StrictFOMCDeterministicReleaseStore(runtime)
    now = [NOW + 10]
    path = tmp_path / 'fomc.json'
    worker = module().ProspectiveFOMCRuntime(store, path, code_sha=lambda: SHA, clock=lambda: now[0])
    assert worker.status()['admissible_for_new_snapshot'] is False
    capture, _ = collect()
    capture.update(publication_contract_version='active-edge-exact-sha-publication-v1', published_for_sha=SHA)
    path.write_text(json.dumps(capture))
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: now[0])
    assert worker.refresh()['status'] == 'OK'
    path.unlink()
    runtime._conn.close()
    current = worker.status()
    assert current['published_for_sha'] == SHA
    assert current['capture_sha256'] == capture['capture_sha256']
    assert current['captured_ts'] == NOW
    assert current['materialized_at'] == NOW + 10
    assert current['stored'] == 2 and current['skipped'] == 0
    assert current['admissible_for_new_snapshot'] is True
    assert current['admission_reason'] is None
    current['status'] = 'ALTERED'
    now[0] = NOW + 3601
    stale = worker.status()
    assert stale['status'] == 'OK'
    assert stale['admissible_for_new_snapshot'] is False
    assert stale['admission_reason'] == 'FOMC_PROSPECTIVE_CAPTURE_STALE_OR_AFTER_SNAPSHOT'


def test_capture_cadence_is_overdue_before_admission_ttl_expires(monkeypatch, tmp_path):
    runtime = SimpleNamespace(_conn=sqlite3.connect(':memory:'), _lock=threading.RLock())
    store = StrictFOMCDeterministicReleaseStore(runtime)
    now = [NOW + 10]
    path = tmp_path / 'fomc.json'
    worker = module().ProspectiveFOMCRuntime(store, path, code_sha=lambda: SHA, clock=lambda: now[0])
    initial = worker.status()
    assert initial['delivery_cadence']['state'] == 'NOT_OBSERVED'
    capture, _ = collect()
    capture.update(publication_contract_version='active-edge-exact-sha-publication-v1', published_for_sha=SHA)
    path.write_text(json.dumps(capture))
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: now[0])
    worker.refresh()
    runtime._conn.close()
    path.unlink()
    current = worker.status()['delivery_cadence']
    assert current['state'] == 'WITHIN_TARGET'
    assert current['capture_age_sec'] == 10
    assert current['target_interval_sec'] == 600
    assert current['scheduler_guaranteed'] is False
    now[0] = NOW + 601
    overdue = worker.status()
    assert overdue['delivery_cadence']['state'] == 'OVERDUE'
    assert overdue['delivery_cadence']['capture_age_sec'] == 601
    assert overdue['admissible_for_new_snapshot'] is True
    assert worker.refresh()['status'] == 'UNAVAILABLE'
    assert worker.status()['delivery_cadence']['state'] == 'OVERDUE'
    now[0] = NOW + 3601
    assert worker.status()['admissible_for_new_snapshot'] is False
