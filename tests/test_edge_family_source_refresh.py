from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.edge_family_source_runtime import load_family_source_context

SHA = 'a' * 40
T0 = time.time() + 30


@pytest.fixture(autouse=True)
def current_test_clock(monkeypatch):
    # Collection precedes this file's execution by several minutes in full CI.
    # Source cutoffs must follow the files actually created by each test.
    monkeypatch.setitem(globals(),'T0',time.time()+30)


def packet(source='current-related', *, captured=None):
    if captured is None:captured=T0-2
    record = dict(source_id=source, source_verified=True, instrument='NAS100',
                  observed_ts=captured-5, received_ts=captured-1, quality=.8,
                  linked_returns=[dict(leader='ETHUSD', start_ts=captured-305,
                                       end_ts=captured-5, start_price=100, end_price=101)])
    return dict(contract_version='edge-family-source-bundle-v1',
                edge_policy='g1s-manual-trader-high-risk-edge-policy-v1',
                production_authority=False, models_produced=0,
                exact_publication_times_inferred=False,
                publication_contract_version='active-edge-exact-sha-publication-v1',
                published_for_sha=SHA, publication_run_id='1', captured_ts=captured,
                instruments={c:dict(edge_family_sources={'intermarket':[deepcopy(record)]}
                                    if c=='NAS100' else {}, readiness={})
                             for c in ALL_INSTRUMENTS},
                raw_sources={'public:candles':dict(fetch_status='FETCHED', received_ts=captured-1)},
                errors=[])


def write(tmp_path, body, *, runtime=False):
    path=tmp_path/'research'/('edge_family_sources_runtime.json' if runtime else
                             'edge_family_sources_latest.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(body))
    os.utime(path,(T0-1,T0-1))
    return path


def admitted(tmp_path, cutoff=None):
    if cutoff is None:cutoff=T0
    return load_family_source_context(SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)),
        {'captured_ts':cutoff,'strategy':{'instrument':'NAS100'}}, SHA)


def source_ids(result):
    return [r['source_id'] for r in result['edge_family_sources'].get('intermarket',[])]


def test_runtime_publication_restores_facts_when_deploy_bundle_is_absent(tmp_path):
    write(tmp_path,packet(),runtime=True)
    result=admitted(tmp_path)
    assert source_ids(result)==['current-related']
    assert result['edge_family_source_bundle_audit']['models_loaded']==0


def test_live_partial_refresh_keeps_other_admitted_baseline_facts(tmp_path):
    write(tmp_path,packet('baseline-related'))
    write(tmp_path,packet('live-related'),runtime=True)
    assert source_ids(admitted(tmp_path))==['baseline-related','live-related']


def test_same_source_identity_is_replaced_without_double_counting(tmp_path):
    old=packet();old['instruments']['NAS100']['edge_family_sources']['intermarket'][0]['quality']=.6
    write(tmp_path,old)
    write(tmp_path,packet(),runtime=True)
    result=admitted(tmp_path)
    assert source_ids(result)==['current-related']
    assert result['edge_family_sources']['intermarket'][0]['quality']==.8


@pytest.mark.parametrize('damage',['sha','future','json','oversized'])
def test_bad_live_document_cannot_disable_valid_baseline(tmp_path,damage):
    write(tmp_path,packet('baseline-related'))
    live=packet('live-related')
    if damage=='sha':live['published_for_sha']='b'*40
    if damage=='future':live['captured_ts']=T0+100
    path=write(tmp_path,live,runtime=True)
    if damage=='json':path.write_text('{broken')
    if damage=='oversized':path.write_bytes(b'x'*1_000_001)
    assert source_ids(admitted(tmp_path))==['baseline-related']


def test_combined_byte_limit_keeps_valid_baseline_instead_of_erasing_it(tmp_path,monkeypatch):
    import seiltanzer.edge_family_source_runtime as m
    write(tmp_path,packet('baseline-related'))
    write(tmp_path,packet('live-related'),runtime=True)
    monkeypatch.setattr(m,'MAX_SELECTED_BYTES',450)
    result=admitted(tmp_path)
    assert source_ids(result)==['baseline-related']


def owner(tmp_path,monkeypatch,collect=None,clock=None,sha=None):
    from seiltanzer.edge_family_source_refresh import FamilySourceRefresher
    import seiltanzer.edge_family_sources as sources
    if collect is None:collect=lambda **kwargs:packet()
    monkeypatch.setattr(sources,'build_bundle',collect)
    return FamilySourceRefresher(tmp_path,expected_sha=SHA,
        clock=clock or (lambda:T0),sha_reader=sha or (lambda:SHA))


def test_refresh_publishes_actual_clocks_and_can_be_read_by_existing_admission(tmp_path,monkeypatch):
    refresher=owner(tmp_path,monkeypatch)
    refresher.refresh()
    path=tmp_path/'research/edge_family_sources_runtime.json'
    assert path.is_file()
    body=json.loads(path.read_bytes())
    assert body['captured_ts']==T0-2
    assert body['published_for_sha']==SHA
    assert body['production_authority'] is False and body['models_produced']==0
    os.utime(path,(T0,T0))
    assert source_ids(admitted(tmp_path,cutoff=T0+1))==['current-related']
    assert refresher.status()['state']=='PUBLISHED'


def test_fresh_exact_sha_offhost_data_prevents_duplicate_acquisition(tmp_path,monkeypatch):
    write(tmp_path,packet())
    def unexpected(**kwargs):raise AssertionError('fresh facts were fetched again')
    refresher=owner(tmp_path,monkeypatch,unexpected)
    refresher.refresh()
    assert refresher.status()['state']=='FRESH_PUBLICATION'
    assert not (tmp_path/'research/edge_family_sources_runtime.json').exists()


def test_failed_acquisition_keeps_original_bytes_and_obeys_retry_interval(tmp_path,monkeypatch):
    original=write(tmp_path,packet(captured=T0-600),runtime=True)
    before=original.read_bytes();clock=[T0];calls=[]
    def fail(**kwargs):calls.append(1);raise TimeoutError('provider unavailable')
    refresher=owner(tmp_path,monkeypatch,fail,clock=lambda:clock[0])
    refresher.refresh();refresher.refresh();clock[0]+=299;refresher.refresh()
    assert original.read_bytes()==before and len(calls)==1
    assert refresher.status()['state']=='FAILED'
    clock[0]+=1;refresher.refresh()
    assert len(calls)==2 and original.read_bytes()==before


@pytest.mark.parametrize('damage',['generation','oversized','all_denied','schema'])
def test_invalid_refresh_cannot_replace_previous_runtime_publication(tmp_path,monkeypatch,damage):
    original=write(tmp_path,packet(captured=T0-600),runtime=True)
    before=original.read_bytes();live=packet()
    if damage=='oversized':live['padding']='x'*1_000_000
    if damage=='all_denied':live['raw_sources']={'denied':{'fetch_status':'UNAVAILABLE'}}
    if damage=='schema':live['production_authority']=True
    shas=iter([SHA,'b'*40]) if damage=='generation' else None
    refresher=owner(tmp_path,monkeypatch,lambda **kwargs:live,
                    sha=(lambda:next(shas)) if shas else None)
    refresher.refresh()
    assert original.read_bytes()==before
    assert refresher.status()['state']=='FAILED'


def test_atomic_write_failure_preserves_previous_publication(tmp_path,monkeypatch):
    import seiltanzer.edge_family_source_refresh as m
    original=write(tmp_path,packet(captured=T0-600),runtime=True);before=original.read_bytes()
    refresher=owner(tmp_path,monkeypatch)
    monkeypatch.setattr(m.os,'replace',lambda *args:(_ for _ in ()).throw(OSError('disk')))
    refresher.refresh()
    assert original.read_bytes()==before
    assert sorted(p.name for p in original.parent.iterdir())==['edge_family_sources_runtime.json']


def test_concurrent_refreshes_have_only_one_acquisition_and_publication(tmp_path,monkeypatch):
    entered,release=threading.Event(),threading.Event();calls=[]
    def collect(**kwargs):
        calls.append(1);entered.set();assert release.wait(3);return packet()
    refresher=owner(tmp_path,monkeypatch,collect)
    thread=threading.Thread(target=refresher.refresh);thread.start()
    try:
        assert entered.wait(2)
        refresher.refresh()
        assert calls==[1] and not (tmp_path/'research/edge_family_sources_runtime.json').exists()
    finally:release.set();thread.join(3)
    assert not thread.is_alive()
    assert refresher.status()['state']=='PUBLISHED'


def test_runtime_publication_changes_existing_ai_invalidation_marker(tmp_path):
    from seiltanzer.ai_frozen_expert_inputs import publication_marker
    engine=SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    before=publication_marker(engine)
    write(tmp_path,packet(),runtime=True)
    assert publication_marker(engine)!=before


def test_repeated_real_collection_keeps_latest_admissible_source_window(tmp_path,monkeypatch):
    from seiltanzer.edge_family_source_refresh import FamilySourceRefresher
    from seiltanzer.edge_family_sources import build_bundle,SourceBudget
    import seiltanzer.edge_family_sources as sources
    clock=[T0]
    def fetch(url):
        if '/candles?' not in url:raise TimeoutError('fixture source unavailable')
        end=int(clock[0]//60)*60
        return json.dumps([[end-60*i,90,200,99,100+i,5] for i in range(12)]).encode()
    def collect(**kwargs):
        kwargs['fetch']=fetch;kwargs['budget']=SourceBudget(min_interval=0)
        return build_bundle(**kwargs)
    monkeypatch.setattr(sources,'build_bundle',collect)
    refresher=FamilySourceRefresher(tmp_path,expected_sha=SHA,clock=lambda:clock[0],sha_reader=lambda:SHA)
    previous_id=None
    for _ in range(4):
        refresher.refresh()
        assert refresher.status()['state']=='PUBLISHED'
        result=admitted(tmp_path,cutoff=clock[0]+1)
        records=result['edge_family_sources'].get('intermarket',[])
        assert len(records)==1, result['edge_family_source_bundle_audit']
        assert records[0]['observed_ts']>clock[0]-120
        assert records[0]['source_id']!=previous_id
        previous_id=records[0]['source_id']
        # An off-host publication of the same lineage must not make the next
        # runtime window overflow the selected8KB budget or retain old returns.
        baseline=json.loads((tmp_path/'research/edge_family_sources_runtime.json').read_bytes())
        write(tmp_path,baseline)
        clock[0]+=300


def test_older_runtime_window_cannot_displace_newer_offhost_observation(tmp_path):
    prefix='synced-crypto:'
    old=packet(prefix+'a'*64,captured=T0-600)
    fresh=packet(prefix+'b'*64,captured=T0-2)
    write(tmp_path,fresh);write(tmp_path,old,runtime=True)
    result=admitted(tmp_path)
    assert source_ids(result)==[prefix+'b'*64]
    assert result['edge_family_sources']['intermarket'][0]['observed_ts']==T0-7


@pytest.mark.parametrize('body',[b'{broken',b'[]'])
def test_http_success_without_usable_facts_cannot_erase_last_good_publication(tmp_path,monkeypatch,body):
    from seiltanzer.edge_family_source_refresh import FamilySourceRefresher
    from seiltanzer.edge_family_sources import build_bundle,SourceBudget
    import seiltanzer.edge_family_sources as sources
    original=write(tmp_path,packet(captured=T0-600),runtime=True)
    before=original.read_bytes()
    assert source_ids(admitted(tmp_path))==['current-related']
    def fetch(url):
        if '/candles?' in url:return body
        raise TimeoutError('fixture source unavailable')
    def collect(**kwargs):
        kwargs['fetch']=fetch;kwargs['budget']=SourceBudget(min_interval=0)
        return build_bundle(**kwargs)
    monkeypatch.setattr(sources,'build_bundle',collect)
    refresher=FamilySourceRefresher(tmp_path,expected_sha=SHA,clock=lambda:T0,sha_reader=lambda:SHA)
    refresher.refresh()
    assert refresher.status()['state']=='FAILED'
    assert original.read_bytes()==before
    assert source_ids(admitted(tmp_path))==['current-related']
