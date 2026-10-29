"""Only physical feature text changes; full decoded typed tables stay equal."""
from contextlib import closing
import hashlib
import sqlite3

import pytest
from scripts import sqlite_history_partition as archive
from scripts import sqlite_json_rebuild as builder
from scripts.history_storage_loader import history_state


def source(tmp_path):
    path=tmp_path/'plain.db'
    text=' {"r": 1.25, "note": "'+'точно; '*4000+'"} '
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('''PRAGMA application_id=721; PRAGMA user_version=9;
          CREATE TABLE passive_market_observations(observation_id TEXT UNIQUE,features_json TEXT,n REAL,b BLOB);
          CREATE TABLE g1s_observations(observation_id TEXT,frozen_features_json TEXT);
          CREATE TABLE audit(id INTEGER PRIMARY KEY AUTOINCREMENT,note TEXT);
          CREATE INDEX idx_g1s_id ON g1s_observations(observation_id);
          CREATE VIEW capture_ids AS SELECT observation_id FROM g1s_observations;
          CREATE TRIGGER audit_capture AFTER INSERT ON g1s_observations BEGIN INSERT INTO audit(note) VALUES('captured'); END;
        ''')
        conn.execute('INSERT INTO passive_market_observations(rowid,observation_id,features_json,n,b) VALUES(91,?,?,?,?)',('id',text,-0.125,b'\x00\xff'))
        conn.execute('INSERT INTO g1s_observations(rowid,observation_id,frozen_features_json) VALUES(204,?,?)',('g1',text))
        conn.execute('INSERT INTO audit(id,note) VALUES(9,NULL)');conn.execute('DELETE FROM audit WHERE id=9')
        conn.commit()
    return path,text


def proof(path):
    return dict(immutable_sqlite_read=True,immutable_snapshot_verified=True,
        verified_local_file=archive._identity(path),database_size_bytes=path.stat().st_size,
        database_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_compress_and_expand_keep_exact_rows_rowids_schema_headers_and_original(tmp_path):
    original,text=source(tmp_path);before=original.read_bytes()
    coded=tmp_path/'coded.db'
    packed=builder.rebuild_snapshot(original,coded,source_receipt=proof(original),mode='compress')
    assert packed['all_typed_table_roots_verified'] and packed['reclaimed_bytes']>0
    assert history_state.PAYLOAD_PIN_FIELD in packed
    with closing(sqlite3.connect(coded)) as conn:
        assert conn.execute('SELECT features_json FROM passive_market_observations').fetchone()[0].startswith('~stj1:')
    plain=tmp_path/'expanded.db'
    expanded=builder.rebuild_snapshot(coded,plain,source_receipt=proof(coded),mode='expand')
    assert history_state.PAYLOAD_PIN_FIELD not in expanded
    assert expanded['analytical_snapshot_only'] is True
    with closing(sqlite3.connect(original)) as a, closing(sqlite3.connect(plain)) as b:
        assert a.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()==b.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        for table in ('passive_market_observations','g1s_observations','audit','sqlite_sequence'):
            assert a.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid').fetchall()==b.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid').fetchall()
        assert b.execute('SELECT features_json FROM passive_market_observations').fetchone()[0]==text
        assert b.execute('PRAGMA application_id').fetchone()==(721,)
        assert b.execute('PRAGMA user_version').fetchone()==(9,)
        assert b.execute('PRAGMA page_size').fetchone()==a.execute('PRAGMA page_size').fetchone()
    assert original.read_bytes()==before


@pytest.mark.parametrize('hazard',['hash','sidecar','collision','deadline','nonshrinking'])
def test_invalid_source_or_failed_publication_cannot_leave_output(tmp_path,hazard):
    original,_=source(tmp_path);receipt=proof(original);out=tmp_path/'output.db';kwargs={}
    if hazard=='hash':receipt['database_sha256']='0'*64
    if hazard=='sidecar':__import__('pathlib').Path(str(original)+'-wal').write_bytes(b'unsafe')
    if hazard=='collision':out.write_bytes(b'unrelated')
    if hazard=='deadline':kwargs['deadline']=0
    if hazard=='nonshrinking':
        with sqlite3.connect(original) as conn:
            conn.execute("UPDATE passive_market_observations SET features_json='{}'")
            conn.execute("UPDATE g1s_observations SET frozen_features_json='{}'")
            conn.commit();conn.execute('VACUUM')
        receipt=proof(original)
    with pytest.raises((ValueError,FileExistsError)):
        builder.rebuild_snapshot(original,out,source_receipt=receipt,mode='compress',**kwargs)
    assert out.read_bytes()==b'unrelated' if hazard=='collision' else not out.exists()


@pytest.mark.parametrize('hazard',['owned_fault','replaced_success','replaced_fault'])
def test_publication_readback_owns_the_output_and_cleanup_preserves_replacements(tmp_path,monkeypatch,hazard):
    work=tmp_path/'owned.tmp';work.write_bytes(b'owned original image')
    output=tmp_path/'published.db';publish=archive._publish
    def injected(work,destination):
        publish(work,destination)
        if hazard.startswith('replaced'):
            destination.unlink();destination.write_bytes(b'unrelated racing image')
        if hazard.endswith('fault'):raise OSError('injected post-publication failure')
    monkeypatch.setattr(archive,'_publish',injected)
    with pytest.raises((ValueError,OSError)):
        builder._publish_owned(work,output)
    if hazard.startswith('replaced'):assert output.read_bytes()==b'unrelated racing image'
    else:assert not output.exists()


@pytest.mark.parametrize('failure',[False,True])
def test_rebuild_cleanup_never_deletes_recreated_worker_path(tmp_path,monkeypatch,failure):
    original,_=source(tmp_path);output=tmp_path/'coded.db';publish=archive._publish
    replacements=[]
    def injected(work,destination):
        publish(work,destination)
        work.write_bytes(b'unrelated worker-path replacement');replacements.append(work)
        if failure:raise OSError('injected post-publication failure')
    monkeypatch.setattr(archive,'_publish',injected)
    if failure:
        with pytest.raises(OSError):
            builder.rebuild_snapshot(original,output,source_receipt=proof(original),mode='compress')
        assert not output.exists()
    else:builder.rebuild_snapshot(original,output,source_receipt=proof(original),mode='compress')
    assert len(replacements)==1
    assert replacements[0].read_bytes()==b'unrelated worker-path replacement'
