"""Actual immutable native inputs, not reconstructed options or quote authority."""
from copy import deepcopy
import hashlib
import importlib
import json

import pytest

T0 = 1791504000.
DIGEST = 'a' * 64


def native():
    assert importlib.util.find_spec('seiltanzer.edge_family_native_history') is not None, 'native historical family extraction missing'
    return importlib.import_module('seiltanzer.edge_family_native_history')


def observation(**changes):
    args = dict(code='NAS100', metrics={'proxy':'QQQ', 'demo':False,
        'implied_move':{'sigma_annual':.24}}, source_id='yfinance QQQ options',
        observed_ts=T0-300, received_ts=T0-100, quality=.5, source_sha256=DIGEST)
    args.update(changes)
    return native().option_observation(**args)


def row():
    frozen = {'option_distribution':{'proxy':'QQQ', 'demo':False,
        'implied_move':{'sigma_annual':.24}}, 'g1s_evidence_v3':{
        'option_static':{'available':True, 'proxy':'QQQ', 'iv':.24,
            'quality':{'source_ts':T0-300, 'source_quality':.5}}}}
    raw = json.dumps(frozen)
    return {'instrument':'NAS100', 'captured_ts':T0-200,
        'original_created_ts':T0-100, 'frozen_features_json':raw,
        'features_sha256':hashlib.sha256(raw.encode()).hexdigest(),
        'source_verified':True, 'option_source':'yfinance QQQ options',
        'option_quality':.5}


def test_option_iv_preserves_actual_units_proxy_and_capture_clocks():
    out = observation()
    assert out['features'] == {'option.iv':.24}
    assert out['captured_ts'] == T0-100
    meta = out['feature_provenance']['option.iv']
    assert meta['observed_ts'] == T0-300
    assert meta['received_ts'] == T0-100
    assert meta['source_sha256'] == DIGEST
    assert meta['measurement_instrument'] == 'QQQ'
    assert meta['target_quote_authority'] is False
    assert meta['raw_option_probability_is_physical'] is False


@pytest.mark.parametrize('changes', [
    {'metrics':{'proxy':'SPY','demo':False,'implied_move':{'sigma_annual':.24}}},
    {'metrics':{'proxy':'QQQ','demo':True,'implied_move':{'sigma_annual':.24}}},
    {'metrics':{'proxy':'QQQ','implied_move':{'sigma_annual':.24}}},
    {'metrics':{'proxy':'QQQ','demo':False,'synthetic_data_used':True,'implied_move':{'sigma_annual':.24}}},
    {'metrics':{'proxy':'QQQ','demo':False,'synthetic':True,'implied_move':{'sigma_annual':.24}}},
    {'observed_ts':T0}, {'quality':None}, {'source_sha256':'broken'},
    {'metrics':{'proxy':'QQQ','demo':False,'implied_move':{'sigma_annual':0}}},
])
def test_invalid_native_option_is_rejected_without_neutral_forecast(changes):
    assert observation(**changes) is None


def test_native_history_uses_original_record_availability_not_old_price_or_export_time():
    out = native().native_history([row()], captured_ts=T0)
    obs = out['NAS100']['option'][0]
    assert obs['captured_ts'] == T0-100
    assert obs['feature_provenance']['option.iv']['observed_ts'] == T0-300
    from seiltanzer.edge_family_market import _history_rows
    assert not _history_rows('NAS100','option',[obs],T0-150)
    assert _history_rows('NAS100','option',[obs],T0)


@pytest.mark.parametrize('mutation', ['hash','verification','future','demo'])
def test_native_history_rejects_unverified_future_or_modified_records(mutation):
    r = row()
    if mutation == 'hash': r['features_sha256'] = 'b'*64
    elif mutation == 'verification': r['source_verified'] = False
    elif mutation == 'future': r['original_created_ts'] = T0+1
    else:
        f = json.loads(r['frozen_features_json']); f['option_distribution']['demo'] = True
        r['frozen_features_json'] = json.dumps(f)
        r['features_sha256'] = hashlib.sha256(r['frozen_features_json'].encode()).hexdigest()
    assert native().native_history([r],captured_ts=T0) == {}


def test_archive_merge_preserves_conflicts_for_trainer_and_removes_identical_duplicates():
    a = {'NAS100':{'option':[observation()]}}
    b = deepcopy(a); b['NAS100']['option'][0]['features']['option.iv'] = .3
    out = native().merge_histories(a,a,b)
    assert len(out['NAS100']['option']) == 2
    from seiltanzer.edge_family_market import _history_rows
    assert _history_rows('NAS100','option',out['NAS100']['option'],T0) == {}


def test_native_sql_export_checks_original_bytes_and_reads_only_bounded_eligible_rows(tmp_path):
    import sqlite3, io, contextlib, gzip, base64
    name = 'scripts.export_native_family_history'
    assert importlib.util.find_spec(name) is not None, 'indexed native history export missing'
    module = importlib.import_module(name)
    db = tmp_path/'native.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE g1s_observations(source_observation_id TEXT,instrument TEXT,horizon_minutes INT,captured_ts REAL,frozen_features_json TEXT,features_sha256 TEXT,training_eligible INT,option_source TEXT,option_quality REAL)')
    c.execute('CREATE TABLE passive_market_observations(observation_id TEXT PRIMARY KEY,instrument TEXT,captured_ts REAL,created_ts REAL,features_json TEXT,evidence_eligible INT)')
    r = row()
    for identity, original, eligible in [('real',r['frozen_features_json'],1),('changed','{}',1),('excluded',r['frozen_features_json'],0)]:
        c.execute('INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?,?)',(identity,'NAS100',15,r['captured_ts'],r['frozen_features_json'],r['features_sha256'],eligible,r['option_source'],.5))
        c.execute('INSERT INTO passive_market_observations VALUES(?,?,?,?,?,?)',(identity,'NAS100',r['captured_ts'],r['original_created_ts'],original,1))
    c.commit(); before = db.read_bytes(); c.close()
    script = module.REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db',str(db))
    output = io.StringIO()
    with contextlib.redirect_stdout(output): exec(script,{})
    report = json.loads(gzip.decompress(base64.b64decode(output.getvalue())))
    assert report['read_only'] is True
    assert len(report['rows']) == 1
    assert report['rows'][0]['original_created_ts'] == r['original_created_ts']
    assert report['rows'][0]['features_sha256'] == r['features_sha256']
    assert 'source_observation_id' not in report['rows'][0]
    assert db.read_bytes() == before


def test_native_history_merges_with_archive_without_relabeling_failed_restore(tmp_path):
    from test_edge_family_private_archive import MemoryStore
    from scripts.export_family_market_history import export_history
    import inspect
    assert 'native' in inspect.signature(export_history).parameters, 'native history is not connected to training import'
    matrix = native().native_history([row()],captured_ts=T0)
    path = tmp_path/'private.json'
    audit = export_history(MemoryStore(),path,native=matrix)
    assert json.loads(path.read_text()) == matrix
    assert audit['reason'] == 'PRIVATE_ARCHIVE_INITIAL_EMPTY'
    assert audit['native_feature_observation_count'] == 1
    assert audit['feature_observation_count'] == 1
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('case',['different_refresh','v2','missing_value','wrong_cached_proxy'])
def test_native_iv_and_timestamp_belong_to_same_recorded_snapshot(case):
    r=row(); frozen=json.loads(r['frozen_features_json'])
    block=frozen['g1s_evidence_v3']['option_static']
    if case=='different_refresh':frozen['option_distribution']['implied_move']['sigma_annual']=.9
    elif case=='v2':
        frozen.pop('g1s_evidence_v3')
        frozen['g1s_evidence_v2']={'option_context':{'available':True,'source_ts':T0-500,
            'proxy':'QQQ','implied_vol_annual':.19}}
    elif case=='missing_value':block.pop('iv')
    else:block['proxy']='SPY'
    r['frozen_features_json']=json.dumps(frozen)
    r['features_sha256']=hashlib.sha256(r['frozen_features_json'].encode()).hexdigest()
    out=native().native_history([r],captured_ts=T0)
    if case in ('missing_value','wrong_cached_proxy'):
        assert out=={}
    else:
        obs=out['NAS100']['option'][0]
        assert obs['features']['option.iv']==(.19 if case=='v2' else .24)
        assert obs['feature_provenance']['option.iv']['observed_ts']==(T0-500 if case=='v2' else T0-300)
