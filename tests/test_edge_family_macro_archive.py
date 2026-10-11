"""Public archive knowledge keeps its real publication and retrieval clocks."""
from dataclasses import FrozenInstanceError
import importlib
import math
from datetime import datetime, timezone

import pytest

from test_macro_historical_offhost_bundle import _bundle, NOW, SHA, RUN_ID
from seiltanzer import macro_historical_offhost_bundle as bundle_contract
from seiltanzer.mathematical_edge import fingerprint


def archive(bundle=None):
    assert importlib.util.find_spec('seiltanzer.edge_family_macro_archive') is not None, 'official macro training archive missing'
    module=importlib.import_module('seiltanzer.edge_family_macro_archive')
    return module.verified_macro_archive(_bundle() if bundle is None else bundle,
        expected_sha=SHA, captured_ts=NOW, acceptance_run_id=RUN_ID)


def test_original_publication_controls_offline_state_without_backdating_retrieval():
    bundle=_bundle();record=bundle['bls_records'][0]
    published=record['spec']['published_at']
    value=archive(bundle)
    assert value.state('macro.cpi_core_yoy_pct',published-1) is None
    state=value.state('macro.cpi_core_yoy_pct',published)
    assert state['value']==record['payload']['core_yoy_pct']
    assert state['available_at']==published
    assert state['fetched_at']==record['fetched_at']>published
    assert state['source_sha256']==record['source_sha256']
    assert state['evidence_kind']=='OFFICIAL_PUBLICATION_ARCHIVE'
    assert 'received_ts' not in state
    assert state['prospective_observation'] is False


def test_source_payload_change_even_with_rehashed_container_is_rejected():
    bundle=_bundle();record=bundle['bls_records'][0]
    record['payload']['core_yoy_pct']=99.
    record['record_sha256']=bundle_contract._sha256(bundle_contract._without(record,'record_sha256'))
    bundle['bundle_sha256']=bundle_contract._sha256(bundle_contract._without(bundle,'bundle_sha256'))
    with pytest.raises(ValueError,match='PAYLOAD_MISMATCH'):
        archive(bundle)


def test_archive_cannot_supply_unknown_or_stale_feature_or_future_state():
    value=archive();published=_bundle()['bls_records'][0]['spec']['published_at']
    assert value.state('macro.invented_surprise',published) is None
    assert value.state('macro.cpi_core_yoy_pct',NOW+1) is None
    assert value.state('macro.cpi_core_yoy_pct',published+46*86400) is None


def test_identical_release_states_are_detached_from_mutable_bundle():
    bundle=_bundle();value=archive(bundle);published=bundle['bls_records'][0]['spec']['published_at']
    before=value.state('macro.cpi_core_yoy_pct',published)
    bundle['bls_records'][0]['payload']['core_yoy_pct']=99.
    before['value']=100.
    assert value.state('macro.cpi_core_yoy_pct',published)['value'] not in (99.,100.)


def test_untrusted_raw_history_flags_cannot_replace_verified_official_archive():
    bundle=_bundle();bundle['expected_sha']='d'*40
    bundle['bundle_sha256']=bundle_contract._sha256(bundle_contract._without(bundle,'bundle_sha256'))
    with pytest.raises(ValueError,match='EXACT_SHA_MISMATCH'):
        archive(bundle)


def test_verified_cutoff_cannot_be_mutated_after_validation():
    value=archive()
    with pytest.raises(FrozenInstanceError):
        value.captured_ts=NOW+86400


def test_archived_ism_pmi_uses_same_canonical_name_as_current_facts():
    value=archive();record=_bundle()['ism_records'][1]
    state=value.state('macro.ism_services_pmi',record['payload']['published_at'])
    assert state['value']==record['payload']['pmi']
    assert state['fetched_at']==record['fetched_at']


def test_archive_dataset_uses_only_known_publications_and_nonoverlapping_future_labels():
    module=importlib.import_module('seiltanzer.edge_family_macro_archive')
    assert hasattr(module,'archive_market_dataset'), 'archive-to-market dataset missing'
    published=_bundle()['bls_records'][0]['spec']['published_at']
    bars=[{'bar_end_ts':published-1800+i*300,'close':100.*math.exp(.0001*i)} for i in range(30)]
    out=module.archive_market_dataset('NAS100','macro.cpi_core_yoy_pct',bars,NOW,15,archive())
    assert len(out['times'])>0
    assert min(out['times'])>=published
    assert all(b-a==900 for a,b in zip(out['times'],out['ends']))
    assert all(a>=b for a,b in zip(out['times'][1:],out['ends'][:-1]))
    assert len(set(out['groups']))==1
    assert out['feature_names']==['macro.cpi_core_yoy_pct']


def historical_source(levels=(2.,4.,2.,4.)):
    from test_macro_historical_offhost_bundle import CPI
    from seiltanzer.macro_bls_historical_bootstrap import parse_bls_archive_spec,parse_cpi_archive
    bundle=_bundle();records=[];links=[]
    for (month,day,period),level in zip([('May',13,'April'),('June',11,'May'),
                                      ('July',15,'June'),('August',12,'July')],levels):
        date=datetime.strptime(f'{month} {day}, 2025','%B %d, %Y')
        url='https://www.bls.gov/news.release/archives/cpi_'+date.strftime('%m%d%Y')+'.htm'
        html=CPI.replace('August 12, 2025',f'{month} {day}, 2025').replace('JULY 2025',f'{period.upper()} 2025')
        # Test-only archived documents pass the same canonical source parser.
        html=html.replace('<td>3.1</td>',f'<td>{level}</td>')
        spec=parse_bls_archive_spec(html,family='CPI',source_url=url)
        records.append(bundle_contract._record({'spec':{'family':'CPI','period':spec.period,
            'published_at':spec.published_at,'source_url':url},'fetched_at':NOW-10,
            'html':html,'source_sha256':bundle_contract._sha256(html),
            'payload':parse_cpi_archive(html,expected_period=spec.period)}))
        links.append(f'<a href="{url}">CPI release</a>')
    bundle['bls_records']=records+[bundle['bls_records'][1]]
    manifest=bundle['bls_schedules']['CPI'];manifest['content']='\n'.join(links)+' ' * 200
    manifest['source_sha256']=bundle_contract._sha256(manifest['content'])
    manifest['record_sha256']=bundle_contract._sha256(bundle_contract._without(manifest,'record_sha256'))
    bundle['bundle_sha256']=bundle_contract._sha256(bundle_contract._without(bundle,'bundle_sha256'))
    value=archive(bundle);bars=[];price=100.;i=0
    for day in range(120):
        start=NOW-120*86400+day*86400
        if datetime.fromtimestamp(start,timezone.utc).weekday()>4:continue
        for slot in range(72):
            end=math.floor((start+slot*300)/300)*300
            state=value.state('macro.cpi_core_yoy_pct',end)
            sign=1 if state and state['value']>3 else -1
            if (i//3)%5==0:sign=-sign
            ret=sign*.0007 if i%7 else .00001
            close=price*math.exp(ret)
            bars.append({'bar_end_ts':end,'open':price,'high':max(price,close)*1.00001,
                         'low':min(price,close)*.99999,'close':close})
            price=close;i+=1
    return value,{'instrument':'NAS100','provider':'Yahoo Finance via yfinance','ticker':'^NDX',
        'interval':'5m','bars':bars,'source_sha256':fingerprint(bars)}


def test_offline_archive_fits_real_heads_without_treating_late_fetch_as_live_receipt():
    from seiltanzer.edge_family_market import train_market_families
    value,source=historical_source()
    result=train_market_families([source],NOW,horizons=(15,),macro_archive=value)['NAS100']['macro']
    assert result['status']=='TRAINED'
    assert result['features_are_captured_receipts'] is False
    assert result['official_macro_archive']['retrieval_clock_backdated'] is False
    head=result['heads']['direction']
    assert head['feature_names']==['macro.cpi_core_yoy_pct']
    assert all(math.isfinite(x) for x in head['head']['beta'])
    assert head['fit_end_ts']<head['test_start_ts']<=result['training_cutoff']
    assert head['selection_uses_final_test'] is False
    assert result['automatic_execution'] is False
    assert result['net_economic_proof'] is False


def test_appended_unfinished_bar_does_not_change_archive_fit_or_selection():
    from copy import deepcopy
    from seiltanzer.edge_family_market import train_market_families,_model_reason
    value,source=historical_source()
    first=train_market_families([source],NOW,horizons=(15,),macro_archive=value)['NAS100']['macro']
    future=deepcopy(source)
    future['bars'].append({**source['bars'][-1],'bar_end_ts':NOW+300})
    future['source_sha256']=fingerprint(future['bars'])
    second=train_market_families([future],NOW,horizons=(15,),macro_archive=value)['NAS100']['macro']
    assert first==second
    assert _model_reason(first,'NAS100','macro',NOW+1) is None


def test_same_public_release_is_purged_across_train_and_scoring():
    from seiltanzer.edge_family_macro_archive import archive_market_dataset
    from seiltanzer.edge_family_market import _purged
    import numpy as np
    value,source=historical_source()
    data=archive_market_dataset('NAS100','macro.cpi_core_yoy_pct',source['bars'],NOW,15,value)
    valid=np.arange(len(data['times'])//2,len(data['times'])//2+20)
    train=_purged(data,np.arange(valid[0]),valid)
    forbidden={group for i in valid for group in data['groups'][i]}
    assert all(not forbidden.intersection(data['groups'][i]) for i in train)


def test_serialized_archive_flag_does_not_bypass_recorded_receipt_validation():
    from seiltanzer.edge_family_market import train_market_families
    _,source=historical_source()
    with pytest.raises(ValueError,match='NOT_VALIDATED'):
        train_market_families([source],NOW,horizons=(15,),macro_archive={'validated':True})


def test_unvarying_archived_state_cannot_be_promoted_by_roundoff_against_baseline():
    from seiltanzer.edge_family_market import train_market_families
    value,source=historical_source(levels=(3.1,3.1,3.1,3.1))
    model=train_market_families([source],NOW,horizons=(15,),macro_archive=value)['NAS100']['macro']
    assert model['status']!='TRAINED'
    assert model['macro_feature_search']['macro.cpi_core_yoy_pct']['reason']=='NO_HEAD_WITH_CAUSAL_VALIDATION'


def test_report_passes_validated_macro_archive_to_the_existing_family_pipeline():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'scripts'/'run_mathematical_edge.py'
    spec=importlib.util.spec_from_file_location('macro_archive_report_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    value,source=historical_source()
    report=module.build_report([source],{},NOW,horizons=(15,),macro_archive=value)
    assert report['family_market_models']['NAS100']['macro']['status']=='TRAINED'
    assert report['historical_macro_source']['bundle_sha256']==value.bundle_sha256
    assert report['historical_macro_source']['prospective_observation'] is False


@pytest.mark.parametrize('malformed',[False,True])
def test_invalid_archive_does_not_stop_unaffected_families_in_runner(tmp_path,malformed):
    import json,subprocess,sys
    _,source=historical_source()
    inputs=tmp_path/'sources.json';inputs.write_text(json.dumps({'sources':[source]}))
    bundle={}
    if malformed:
        bundle=_bundle();bundle['bls_schedules']['CPI']=[]
        bundle['bundle_sha256']=bundle_contract._sha256(bundle_contract._without(bundle,'bundle_sha256'))
    bad=tmp_path/'bad-archive.json';bad.write_text(json.dumps(bundle))
    out=tmp_path/'report.json'
    result=subprocess.run([sys.executable,'-m','scripts.run_mathematical_edge',
        '--sources',str(inputs),'--captured-ts',str(NOW),'--horizons','15',
        '--historical-macro-bundle',str(bad),'--macro-source-sha',SHA,
        '--output',str(out)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    report=json.loads(out.read_text())
    assert report['historical_macro_source']['available'] is False
    assert report['historical_macro_source']['reason']==('OFFICIAL_MACRO_ARCHIVE_STRUCTURE_INVALID'
        if malformed else 'HISTORICAL_OFFHOST_CONTRACT_MISMATCH')
    assert report['family_market_models']['NAS100']['session']['status']=='TRAINED'


def test_offline_copy_can_be_revalidated_without_extending_live_delivery_ttl():
    from seiltanzer.edge_family_macro_archive import verified_macro_archive
    bundle=_bundle();later=NOW+3600
    with pytest.raises(ValueError,match='STALE'):
        bundle_contract.validate_bundle(bundle,expected_sha=SHA,now=later)
    value=verified_macro_archive(bundle,expected_sha=SHA,captured_ts=later)
    published=bundle['bls_records'][0]['spec']['published_at']
    assert value.state('macro.cpi_core_yoy_pct',published)['fetched_at']==NOW-10
    assert value.state('macro.cpi_core_yoy_pct',NOW+1) is None
    assert value.summary()['source_window_end']==NOW
    assert value.summary()['source_code_sha']==SHA


@pytest.mark.parametrize('present',[True,False])
def test_existing_archive_transport_reads_bytes_without_source_writes(tmp_path,present):
    import base64,gzip,io,json,contextlib
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'scripts'/'export_official_macro_archive.py'
    assert script.exists(),'existing official archive exporter missing'
    spec=importlib.util.spec_from_file_location('official_macro_export_test',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'official.json'
    if present:source.write_text(json.dumps(_bundle()))
    before=source.read_bytes() if present else None
    stream=io.StringIO()
    program=module.REMOTE_EXPORT.replace('/opt/seiltanzer/data/research/official_macro_historical_offhost_latest.json',str(source))
    with contextlib.redirect_stdout(stream):exec(program,{})
    raw=gzip.decompress(base64.b64decode(stream.getvalue()))
    result=json.loads(raw)
    if present:
        assert raw==before
        assert result['expected_sha']==SHA
        assert source.read_bytes()==before
    else:
        assert result['archive_export_error']=='OFFICIAL_MACRO_ARCHIVE_NOT_FOUND'
        assert not source.exists()


@pytest.mark.parametrize('mode',[None,0,'false'])
def test_live_validator_does_not_accept_ambiguous_freshness_mode(mode):
    with pytest.raises(ValueError,match='TRANSPORT_MODE_INVALID'):
        bundle_contract.validate_bundle(_bundle(),expected_sha=SHA,now=NOW,require_fresh_transport=mode)


@pytest.mark.parametrize('change',['future_window','prospective','source_sha'])
def test_runtime_rejects_rehashed_model_with_false_archive_provenance(change):
    from seiltanzer.edge_family_market import train_market_families,_model_reason
    value,source=historical_source()
    model=train_market_families([source],NOW,horizons=(15,),macro_archive=value)['NAS100']['macro']
    proof=model['official_macro_archive']
    if change=='future_window':proof['source_window_end']=NOW+86400
    elif change=='prospective':proof['prospective_observation']=True
    else:proof['source_code_sha']='wrong'
    model['model_sha256']=fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
    assert _model_reason(model,'NAS100','macro',NOW+1)=='MARKET_MACRO_ARCHIVE_PROVENANCE_INVALID'


def test_alfred_day_granularity_is_not_called_an_exact_release_time():
    from test_macro_historical_offhost_bundle import _alfred_bundle
    bundle=_alfred_bundle();value=archive(bundle)
    available=bundle['bls_records'][0]['spec']['published_at']
    assert value.state('macro.cpi_core_yoy_pct',available-1) is None
    state=value.state('macro.cpi_core_yoy_pct',available)
    assert state['published_at'] is None
    assert state['availability_rule']=='CONSERVATIVE_ALFRED_NEXT_DAY'
    assert state['available_at']==available


def test_macro_winner_is_frozen_before_final_test_label_availability(monkeypatch):
    import numpy as np
    from seiltanzer import edge_family_macro_archive as m, edge_family_market as market
    names=m.FEATURES[:2]
    times=NOW-1_000_000+np.arange(1000)*900
    changed=False
    def dataset(code,name,bars,captured,horizon,archive):
        n=820 if name==names[0] else 1000 if name==names[1] else 0
        direction=(np.arange(n)%2).astype(float)
        x=direction.copy()
        if name==names[1]:x[np.arange(n)%4==0]=1-x[np.arange(n)%4==0]
        movement=np.ones(n)
        if changed and name==names[0]:movement[800:]=0
        return {'x':x.reshape((-1,1)), 'y':np.column_stack((direction,movement,direction*.001)),
            'times':times[:n], 'ends':times[:n]+900, 'groups':[(str(i),) for i in range(n)],
            'feature_names':[name]}
    monkeypatch.setattr(m,'archive_market_dataset',dataset)
    monkeypatch.setattr(market,'market_dataset',lambda *a,**k:{'times':times})
    first=m.fit_archive_macro('NAS100',[],NOW,(15,),archive(),{})
    assert first['heads']['direction']['feature_names']==[names[0]]
    changed=True
    second=m.fit_archive_macro('NAS100',[],NOW,(15,),archive(),{})
    assert second['heads']['direction'].get('feature_names')==[names[0]]
    assert 'head' not in second['heads']['direction']
    assert second['heads']['direction']['reason']=='PURGED_FINAL_TEST_UNAVAILABLE'


def test_structurally_malformed_archive_is_a_validation_error():
    bundle=_bundle();bundle['bls_schedules']['CPI']=[]
    bundle['bundle_sha256']=bundle_contract._sha256(bundle_contract._without(bundle,'bundle_sha256'))
    with pytest.raises(ValueError,match='STRUCTURE_INVALID'):
        archive(bundle)


@pytest.mark.parametrize('failure',['connect','channel'])
def test_optional_macro_transport_failure_writes_unavailable_input(tmp_path,monkeypatch,failure):
    import json,sys
    from pathlib import Path
    from scripts import production_ede_offload
    path=Path(__file__).resolve().parents[1]/'scripts'/'export_official_macro_archive.py'
    spec=importlib.util.spec_from_file_location('optional_macro_transport_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Client:
        closed=False
        def exec_command(self,*a,**k):raise RuntimeError('channel failed')
        def close(self):self.closed=True
    client=Client()
    def connect(*a,**k):
        if failure=='connect':raise RuntimeError('transport unavailable')
        return client
    monkeypatch.setattr(production_ede_offload,'_connect',connect)
    output=tmp_path/'macro.json'
    monkeypatch.setattr(sys,'argv',['export','--password','test-only','--output',str(output)])
    module.main()
    value=json.loads(output.read_text())
    assert value=={'archive_export_error':'OFFICIAL_MACRO_ARCHIVE_READ_FAILED','available':False,'read_only':True}
    if failure=='channel':assert client.closed
