"""Actual research preparation chooses a complete, verified reader database."""
import hashlib
import importlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from test_sqlite_history_current_union import _hot, _current_proof
from test_verify_immutable_snapshot import create_snapshot


def test_plain_verified_snapshot_requires_no_cloud_access_or_second_copy(tmp_path):
    database, manifest = create_snapshot(tmp_path)
    class NoCloud:
        def get_object(self, **kwargs):
            raise AssertionError('Plain snapshot should not read cloud history')
    prep = importlib.import_module('scripts.prepare_research_history')
    result = prep.prepare_research_snapshot(database, manifest, tmp_path/'unused.sqlite3', client=NoCloud())
    assert result['database'] == str(database.resolve())
    assert result['manifest'] == str(manifest.resolve())
    assert result['history_union_prepared'] is False
    assert not (tmp_path/'unused.sqlite3').exists()


def test_research_readers_receive_complete_history_and_current_hot_outcomes(tmp_path):
    _, hot, report, _, client = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute("UPDATE passive_market_observations SET resolution_status='resolved' WHERE rowid=110")
    proof = _current_proof(hot, report)
    manifest = tmp_path/'hot.manifest.json'; manifest.write_text(json.dumps(proof['storage_manifest']))
    prep = importlib.import_module('scripts.prepare_research_history')
    result = prep.prepare_research_snapshot(hot, manifest, tmp_path/'research.sqlite3', client=client)
    assert result['history_union_prepared'] is True
    with closing(sqlite3.connect(result['database'])) as conn:
        assert conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone() == (14,)
        assert conn.execute('SELECT resolution_status FROM passive_market_observations WHERE rowid=110').fetchone() == ('resolved',)
        assert conn.execute("SELECT 1 FROM sqlite_schema WHERE name='hot_history_storage_state'").fetchone() is None
    output_manifest = json.loads(__import__('pathlib').Path(result['manifest']).read_text())
    assert output_manifest['source'] == 'VERIFIED_CURRENT_HOT_COLD_UNION'
    assert output_manifest['raw_hot_database_sha256'] == proof['database_sha256']
    assert output_manifest['production_authority'] is False
    assert 'cold_history_catalog_sha256' not in output_manifest
    assert hashlib.sha256(hot.read_bytes()).hexdigest() == proof['database_sha256']


def test_bad_manifest_history_pin_fails_before_catalog_fetch(tmp_path):
    _, hot, result, _, _ = _hot(tmp_path)
    manifest = tmp_path/'bad.json'
    manifest.write_text(json.dumps(dict(result['hot_restore_proof']['storage_manifest'], cold_history_catalog_sha256='0'*64)))
    class NoCloud:
        def get_object(self, **kwargs):
            raise AssertionError('Invalid pin reached cloud')
    prep = importlib.import_module('scripts.prepare_research_history')
    with pytest.raises(ValueError, match='history pin'):
        prep.prepare_research_snapshot(hot, manifest, tmp_path/'forbidden.sqlite3', client=NoCloud())
    assert not (tmp_path/'forbidden.sqlite3').exists()


def test_research_output_manifest_collision_is_not_overwritten(tmp_path):
    _, hot, result, _, client = _hot(tmp_path)
    manifest = tmp_path/'hot.json'; manifest.write_text(json.dumps(result['hot_restore_proof']['storage_manifest']))
    destination = tmp_path/'research.sqlite3'
    collision = tmp_path/'research.sqlite3.manifest.json'; collision.write_text('keep unrelated file')
    prep = importlib.import_module('scripts.prepare_research_history')
    with pytest.raises(FileExistsError):
        prep.prepare_research_snapshot(hot, manifest, destination, client=client)
    assert collision.read_text() == 'keep unrelated file'
    assert not destination.exists()


def test_coded_plain_history_expands_exact_text_without_cloud_or_changing_backup(tmp_path):
    from test_sqlite_json_rebuild import source, proof
    from scripts.sqlite_json_rebuild import rebuild_snapshot
    from scripts.history_storage_loader import history_state
    original,text=source(tmp_path);coded=tmp_path/'coded.db'
    result=rebuild_snapshot(original,coded,source_receipt=proof(original),mode='compress')
    manifest=tmp_path/'coded.json';manifest.write_text(json.dumps(result))
    before=coded.read_bytes()
    class NoCloud:
        def get_object(self,**kwargs):raise AssertionError('No cold state')
    prep=importlib.import_module('scripts.prepare_research_history')
    prepared=prep.prepare_research_snapshot(coded,manifest,tmp_path/'research.db',client=NoCloud())
    assert prepared['json_payloads_expanded'] is True
    with closing(sqlite3.connect(prepared['database'])) as conn:
        assert conn.execute('SELECT features_json FROM passive_market_observations').fetchone()==(text,)
        assert history_state.PAYLOAD_PIN_FIELD not in history_state.history_manifest_fields(conn)
    data=json.loads(Path(prepared['manifest']).read_text())
    assert data['raw_physical_database_sha256']==result['database_sha256']
    assert data['analytical_snapshot_only'] is True
    assert coded.read_bytes()==before


@pytest.mark.parametrize('runtime_state',[False,True])
def test_coded_current_hot_expands_before_cold_union_keeps_current_outcomes_and_pins(tmp_path,runtime_state):
    from scripts.sqlite_json_rebuild import rebuild_snapshot
    from test_sqlite_json_rebuild import proof
    from test_sqlite_history_cutover import _build_cutover_fixture
    from scripts.sqlite_history_cutover import build_hot_cutover_snapshot
    src,receipt,raw,pin,client=_build_cutover_fixture(tmp_path,include_post_catalog=True,include_feature_json=True,include_runtime_metadata=runtime_state)
    hot=tmp_path/'hot.db'
    build_hot_cutover_snapshot(src,hot,source_receipt=receipt,catalog_bytes=raw,expected_catalog_sha256=pin)
    with closing(sqlite3.connect(hot)) as conn:
        text=' {"note": "'+'new current exact feature '*2000+'"} '
        conn.execute('UPDATE passive_market_observations SET features_json=?,resolution_status=? WHERE rowid=140',(text,'resolved'))
        conn.commit()
    coded=tmp_path/'coded-hot.db'
    result=rebuild_snapshot(hot,coded,source_receipt=proof(hot),mode='compress')
    manifest=tmp_path/'coded-hot.json';manifest.write_text(json.dumps(result))
    prep=importlib.import_module('scripts.prepare_research_history')
    prepared=prep.prepare_research_snapshot(coded,manifest,tmp_path/'research.db',client=client)
    assert prepared['history_union_prepared'] and prepared['json_payloads_expanded']
    with closing(sqlite3.connect(prepared['database'])) as conn:
        assert conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone()==((1014 if runtime_state else 14),)
        assert conn.execute('SELECT features_json,resolution_status FROM passive_market_observations WHERE rowid=140').fetchone()==(text,'resolved')
    out=json.loads(Path(prepared['manifest']).read_text())
    assert out['raw_hot_database_sha256']==result['database_sha256']
    assert out['raw_physical_database_sha256']==result['database_sha256']
    assert 'cold_history_catalog_sha256' not in out
