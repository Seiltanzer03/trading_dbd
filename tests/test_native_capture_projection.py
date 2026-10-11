"""Existing large captures must not erase usable history at the transport bound."""
import base64
import contextlib
import gzip
import hashlib
import importlib
import io
import json
import sqlite3

import pytest

from test_edge_family_native_history import row, T0
from seiltanzer.edge_family_native_history import native_history


def projection(r):
    module = importlib.import_module('seiltanzer.native_family_capture_projection')
    out = module.project_capture(r['frozen_features_json'], r['features_sha256'])
    assert out is not None
    return {**r, **out}


def test_projection_preserves_features_original_hash_clocks_and_rejection_markers():
    r = row()
    f = json.loads(r['frozen_features_json']); f['unrelated_array'] = 'x'*40000
    r['frozen_features_json'] = json.dumps(f)
    r['features_sha256'] = hashlib.sha256(r['frozen_features_json'].encode()).hexdigest()
    out = projection(r)
    assert len(out['frozen_features_json']) < 2000
    assert out['features_sha256'] == r['features_sha256']
    assert native_history([out],captured_ts=T0) == native_history([r],captured_ts=T0)
    assert native_history([out],captured_ts=T0)['NAS100']['option'][0]['feature_provenance']['option.iv']['source_sha256'] == r['features_sha256']


def test_projection_rejects_original_source_hash_mismatch():
    module=importlib.import_module('seiltanzer.native_family_capture_projection')
    r=row()
    assert module.project_capture(r['frozen_features_json'],'b'*64) is None


def test_projection_at_consumer_byte_ceiling_preserves_valid_original():
    r=row(); f=json.loads(r['frozen_features_json'])
    f['edge_family_sources']={'unrelated':'x'}
    raw=json.dumps(f,separators=(',',':'))
    f['edge_family_sources']['unrelated']='x'*(256000-len(raw.encode())+1)
    r['frozen_features_json']=json.dumps(f,separators=(',',':'))
    assert len(r['frozen_features_json'].encode())==256000
    r['features_sha256']=hashlib.sha256(r['frozen_features_json'].encode()).hexdigest()
    expected=native_history([r],captured_ts=T0)
    assert len(expected['NAS100']['option'])==1
    out=projection(r)
    assert len(out['frozen_features_json'].encode())<=256000
    assert native_history([out],captured_ts=T0)==expected


def test_projection_preserves_official_macro_inputs_alongside_option_history():
    r=row();f=json.loads(r['frozen_features_json'])
    f['macro_context_v1']={'numeric_macro':{'releases':{'CPI':{
        'status':'VALID','release_id':'CPI-existing','official_source_verified':True,
        'available_at':T0-86400,'fetched_at':T0-86400,'payload':{'core_yoy_pct':3.}}},
        'candidate_vector':{'macro.cpi_core_yoy_pct':3.}}}
    r['frozen_features_json']=json.dumps(f)
    r['features_sha256']=hashlib.sha256(r['frozen_features_json'].encode()).hexdigest()
    original=native_history([r],captured_ts=T0)
    assert original['NAS100']['macro'][0]['features']['macro.cpi_core_yoy_pct']==3.
    assert native_history([projection(r)],captured_ts=T0)==original


@pytest.mark.parametrize('mutation',['bytes','original_hash','contract','verification'])
def test_invalid_private_projection_is_not_admitted(mutation):
    out=projection(row())
    if mutation=='bytes': out['frozen_features_json']+=' '
    elif mutation=='original_hash':out['features_sha256']='b'*64
    elif mutation=='contract':out['projection_contract']='unknown'
    else:out['source_verified']=False
    assert native_history([out],captured_ts=T0)=={}


@pytest.mark.parametrize('where',['root','producer','option_block_nested'])
def test_projection_cannot_remove_synthetic_markers(where):
    r=row(); f=json.loads(r['frozen_features_json'])
    if where=='root': f['synthetic_data_used']=True
    elif where=='producer':f['option_distribution']['provenance']={'synthetic':True}
    else:f['g1s_evidence_v3']['option_static']['unused_nested']={'synthetic_data_used':True}
    r['frozen_features_json']=json.dumps(f)
    r['features_sha256']=hashlib.sha256(r['frozen_features_json'].encode()).hexdigest()
    module=importlib.import_module('seiltanzer.native_family_capture_projection')
    out=module.project_capture(r['frozen_features_json'],r['features_sha256'])
    assert out is None or native_history([{**r,**out}],captured_ts=T0)=={}


def test_bounded_sql_transport_preserves_more_than_180_existing_large_option_captures(tmp_path):
    from scripts.export_native_family_history import REMOTE_EXPORT
    db=tmp_path/'native.db'; c=sqlite3.connect(db)
    c.execute('CREATE TABLE g1s_observations(source_observation_id TEXT,instrument TEXT,horizon_minutes INT,captured_ts REAL,frozen_features_json TEXT,features_sha256 TEXT,training_eligible INT,option_source TEXT,option_quality REAL)')
    c.execute('CREATE INDEX capture_idx ON g1s_observations(instrument,horizon_minutes,captured_ts)')
    c.execute('CREATE TABLE passive_market_observations(observation_id TEXT PRIMARY KEY,instrument TEXT,captured_ts REAL,created_ts REAL,features_json TEXT,evidence_eligible INT)')
    originals=[]
    for i in range(260):
        r=row(); ts=T0-260*900+i*900
        r.update(captured_ts=ts,original_created_ts=ts+6)
        f=json.loads(r['frozen_features_json']); f['unrelated_array']='x'*40000
        f['g1s_evidence_v3']['option_static']['quality']['source_ts']=ts-60
        f['g1s_evidence_v3']['option_static']['iv']=.15+(i%17)*.001
        raw=json.dumps(f); sha=hashlib.sha256(raw.encode()).hexdigest()
        r.update(frozen_features_json=raw,features_sha256=sha); originals.append(r)
        identity=str(i)
        c.execute('INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?,?)',(identity,'NAS100',15,ts,raw,sha,1,r['option_source'],.5))
        c.execute('INSERT INTO passive_market_observations VALUES(?,?,?,?,?,?)',(identity,'NAS100',ts,ts+6,raw,1))
    c.commit();c.close(); before=hashlib.sha256(db.read_bytes()).hexdigest()
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream):exec(REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db',str(db)),{})
    report=json.loads(gzip.decompress(base64.b64decode(stream.getvalue())))
    assert report['reason']=='NATIVE_OBSERVATIONS_EXPORTED'
    assert len(report['rows'])==260
    assert report['limited_instruments']==[]
    projected=native_history(report['rows'],captured_ts=T0)
    expected=native_history(list(reversed(originals)),captured_ts=T0)
    assert len(projected['NAS100']['option'])==260
    assert projected==expected
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
