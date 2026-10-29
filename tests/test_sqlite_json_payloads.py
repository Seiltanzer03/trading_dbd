"""Exact, bounded storage compression; original JSON bytes remain the evidence."""
import base64
import hashlib
import random
import sqlite3
import string
import zlib

import pytest

from seiltanzer import sqlite_json_payloads as codec


def raw_json():
    return ' {\n  "r": 1.25, "unicode": "золото", "audit": "'+'repeated audit; '*400+'"\n}\n'


def connection():
    c = sqlite3.connect(':memory:')
    c.executescript('''
        CREATE TABLE passive_market_observations(observation_id TEXT PRIMARY KEY,features_json TEXT,note TEXT);
        CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY,frozen_features_json TEXT);
    ''')
    return c


def test_exact_text_hash_and_whitespace_roundtrip():
    raw = raw_json(); packed = codec.pack_text(raw)
    assert packed.startswith(codec.PREFIX)
    assert len(packed) < len(raw.encode())
    assert codec.unpack_text(packed) == raw
    assert hashlib.sha256(codec.unpack_text(packed).encode()).digest() == hashlib.sha256(raw.encode()).digest()
    assert codec.pack_text(packed) == packed


def test_plain_small_nontext_and_unhelpful_compression_remain_unchanged():
    rng = random.Random(17)
    value = ''.join(rng.choices(string.printable,k=12000))
    for raw in ('{}','',None,17,b'\x00\xff',value):
        assert codec.pack_text(raw) == raw
        assert codec.unpack_text(raw) == raw


@pytest.mark.parametrize('hazard',['version','length','hash','base64','trailing','truncated'])
def test_corrupt_payload_never_becomes_empty_or_default_json(hazard):
    packed = codec.pack_text(raw_json())
    prefix,length,sha,data = packed.split(':',3)
    if hazard == 'version': prefix = '~stj2'
    elif hazard == 'length': length = str(int(length)+1)
    elif hazard == 'hash': sha = '0'*64
    elif hazard == 'base64': data = '!'+data[1:]
    elif hazard == 'trailing': data = base64.b64encode(base64.b64decode(data)+b'junk').decode()
    else: data = base64.b64encode(base64.b64decode(data)[:-2]).decode()
    with pytest.raises(ValueError): codec.unpack_text(':'.join((prefix,length,sha,data)))


def test_decompression_bomb_and_excessive_declared_length_are_refused():
    huge = b'x'*(12*1024**2)
    packed = base64.b64encode(zlib.compress(huge,9)).decode()
    for length in (100000,codec.MAX_RAW_BYTES+1):
        value = codec.PREFIX+str(length)+':'+hashlib.sha256(huge).hexdigest()+':'+packed
        with pytest.raises(ValueError): codec.unpack_text(value)


@pytest.mark.parametrize('rows',[False,True])
def test_reader_preserves_tuple_or_sqlite_row_and_only_target_column_names(rows):
    c = connection()
    if rows:c.row_factory = sqlite3.Row
    codec.install_marker(c)
    raw = raw_json(); packed = codec.pack_text(raw)
    c.execute('INSERT INTO passive_market_observations VALUES(?,?,?)',('a',packed,packed))
    assert codec.install_reader(c) is True
    row = c.execute('SELECT features_json,note FROM passive_market_observations').fetchone()
    assert isinstance(row,sqlite3.Row if rows else tuple)
    assert row[0] == raw and row[1] == packed
    assert c.execute("SELECT json_extract(storage_json_text(features_json),'$.r') FROM passive_market_observations").fetchone()[0] == 1.25
    assert c.execute('SELECT storage_json_text(features_json) AS renamed FROM passive_market_observations').fetchone()[0] == raw
    assert codec.install_reader(c) is True
    assert c.execute('SELECT features_json FROM passive_market_observations').fetchone()[0] == raw
    c.close()


def test_unmarked_database_does_not_enable_storage_or_reinterpret_reserved_text():
    c = connection(); c.row_factory = sqlite3.Row
    assert codec.install_reader(c) is False
    assert c.row_factory is sqlite3.Row
    packed = codec.pack_text(raw_json())
    assert c.execute('SELECT storage_json_text(?)',(packed,)).fetchone()[0] == packed
    assert codec.manifest_fields(c) == {}
    c.close()


def test_existing_custom_row_factory_is_preserved():
    c = connection();codec.install_marker(c)
    raw = raw_json()
    c.execute('INSERT INTO passive_market_observations VALUES(?,?,?)',('a',codec.pack_text(raw),'note'))
    c.row_factory = lambda cursor,row:dict(zip((d[0] for d in cursor.description),row))
    assert codec.install_reader(c) is True
    assert c.execute('SELECT features_json,note FROM passive_market_observations').fetchone() == {'features_json':raw,'note':'note'}
    c.close()


def test_marker_is_immutable_and_pin_must_be_complete():
    c = connection(); codec.install_marker(c)
    fields = codec.manifest_fields(c)
    assert fields.keys() == {codec.MANIFEST_FIELD}
    assert len(fields[codec.MANIFEST_FIELD]) == 64
    for sql in [f'DELETE FROM {codec.TABLE}',f'UPDATE {codec.TABLE} SET id=1',
                f'INSERT INTO {codec.TABLE} SELECT * FROM {codec.TABLE}']:
        with pytest.raises(sqlite3.DatabaseError): c.execute(sql)
    with pytest.raises(ValueError):codec.install_marker(c)
    c.close()


def test_marker_requires_exact_schema_guards_columns_and_json():
    c = connection();codec.install_marker(c)
    name = next(iter(codec.marker_guards()))
    c.execute('DROP TRIGGER "'+name+'"')
    with pytest.raises(ValueError):codec.manifest_fields(c)
    c.close()


def test_failed_marker_install_leaves_no_partial_storage_contract():
    c = connection();c.execute('DROP TABLE g1s_observations')
    with pytest.raises(ValueError):codec.install_marker(c)
    assert c.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(codec.TABLE,)).fetchone() is None
    c.close()


def test_manifest_requires_payload_pin_and_rejects_pin_without_marker():
    from scripts.history_storage_loader import history_state
    c = connection();codec.install_marker(c)
    fields = history_state.history_manifest_fields(c)
    assert fields == codec.manifest_fields(c)
    with pytest.raises(ValueError):history_state.validate_manifest_history(c,{})
    assert history_state.validate_manifest_history(c,fields) == fields
    codec.drop_marker(c)
    with pytest.raises(ValueError):history_state.validate_manifest_history(c,fields)
    c.close()


def test_startup_rejects_old_reader_even_without_cold_history_marker(tmp_path):
    from scripts import history_service_fence as fence
    db = tmp_path/'source.db'; c = connection()
    codec.install_marker(c)
    with sqlite3.connect(db) as output:c.backup(output)
    c.close()
    app = tmp_path/'app';(app/'seiltanzer').mkdir(parents=True)
    result = fence.check_start(db,app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'JSON_PAYLOAD_READER_CAPABILITY_REQUIRED'


def test_startup_with_codec_capability_accepts_valid_marker_and_rejects_corruption(tmp_path):
    from pathlib import Path
    from scripts import history_service_fence as fence
    db = tmp_path/'source.db';c = connection();codec.install_marker(c)
    with sqlite3.connect(db) as output:c.backup(output)
    c.close()
    app = tmp_path/'app';folder=app/'seiltanzer';folder.mkdir(parents=True)
    (folder/'history_runtime_views.py').write_text("JSON_PAYLOAD_READER_CONTRACT = 'sqlite-json-zlib-base64-v1'\n")
    (folder/'sqlite_json_payloads.py').write_text(Path(codec.__file__).read_text())
    assert fence.check_start(db,app)['start_allowed'] is True
    with sqlite3.connect(db) as conn:
        conn.execute('DROP TRIGGER "trg_'+codec.TABLE+'_update"')
        conn.execute('UPDATE "'+codec.TABLE+'" SET payload_sha256=?',('0'*64,))
        conn.execute(codec.marker_guards()['trg_'+codec.TABLE+'_update'])
    result=fence.check_start(db,app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'JSON_PAYLOAD_STORAGE_INVALID'


def test_actual_passive_and_g1s_keep_raw_hashes_while_storage_is_compressed(tmp_path):
    from types import SimpleNamespace
    from test_research_capture_headroom import make_passive
    from test_g1_operational_integrity import _features, _forecast
    from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
    passive, cache = make_passive(tmp_path)
    ShortHorizonRuntime(SimpleNamespace(passive=passive))
    codec.install_marker(passive._conn);passive._conn.commit()
    passive.close();cache.close()
    passive, cache = make_passive(tmp_path)
    try:
        runtime = ShortHorizonRuntime(SimpleNamespace(passive=passive))
        ts = 2_000_000_000.
        features = _features(ts);features['exact_audit'] = 'полный исходный аудит; '*2000
        ids = passive.capture_observation(instrument='NAS100',captured_ts=ts,
            market_price=101.,features=features,forecast=_forecast(ts),provenance={},trigger_reason='test')
        assert ids
        decoded = passive._conn.execute('SELECT features_json FROM passive_market_observations ORDER BY rowid LIMIT 1').fetchone()[0]
        assert 'полный исходный аудит' in decoded
        with sqlite3.connect(tmp_path/'trades.db') as raw:
            stored = raw.execute('SELECT features_json FROM passive_market_observations LIMIT 1').fetchone()[0]
        assert stored.startswith(codec.PREFIX)
        assert codec.unpack_text(stored) == decoded
        assert runtime.materialize_new() > 0
        row = passive._conn.execute('SELECT frozen_features_json,features_sha256 FROM g1s_observations LIMIT 1').fetchone()
        assert row[0] == decoded
        assert hashlib.sha256(decoded.encode()).hexdigest() == row[1]
        with sqlite3.connect(tmp_path/'trades.db') as raw:
            assert raw.execute('SELECT frozen_features_json FROM g1s_observations LIMIT 1').fetchone()[0].startswith(codec.PREFIX)
    finally:
        passive.close();cache.close()
