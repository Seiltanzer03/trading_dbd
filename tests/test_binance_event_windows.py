"""Calendar-selected real target windows, never outcome-selected history."""
import hashlib
import importlib
import importlib.util
import io
import zipfile

import pytest

from test_binance_public_archive import line,ts
from seiltanzer.mathematical_edge_sources import RequestBudget


def module():
    assert importlib.util.find_spec('seiltanzer.binance_event_windows') is not None, 'older exact event target windows missing'
    return importlib.import_module('seiltanzer.binance_event_windows')


class Response:
    status_code=200
    def __init__(self,content):self.content=content


class Client:
    def __init__(self,deny=False):self.calls=[];self.deny=deny
    def get(self,url,*,timeout):
        self.calls.append(url)
        if self.deny:
            response=Response(b'');response.status_code=451;return response
        filename=url.rsplit('/',1)[1].removesuffix('.CHECKSUM')
        # Two calendar windows and an unrelated observation between them.
        start=ts('2026-09-01')
        raw=''.join(line(start=start+300*i) for i in range(12*288))
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(filename[:-4]+'.csv',raw)
        body=stream.getvalue()
        return Response(f'{hashlib.sha256(body).hexdigest()}  {filename}\n'.encode() if url.endswith('.CHECKSUM') else body)


def test_collector_selects_all_official_windows_and_keeps_actual_later_file_receipt():
    start=ts('2026-09-01');cutoff=ts('2026-10-25')
    releases=[start+12*3600,start+10*86400+12*3600]
    client=Client()
    result=module().collect_event_windows('BTCUSD',cutoff,client,RequestBudget(30,0),
        release_times=releases,calendar_sha256='a'*64)
    bars=result['event_window_bars'];proof=result['event_window_archive']
    assert len(client.calls)==2
    assert proof['calendar_release_times']==releases
    assert proof['selection_policy']=='ALL_VERIFIED_OFFICIAL_RELEASE_WINDOWS_NOT_PRICE_OUTCOMES'
    assert proof['historical_first_receipt_known'] is False
    assert proof['files'][0]['received_ts']>max(releases)
    assert all(any(at+300<=bar['bar_end_ts']<=at+6*3600 for at in releases) for bar in bars)
    assert min(bar['bar_end_ts'] for bar in bars)==releases[0]+300
    assert result['event_window_archive']['bars_sha256']==module().fingerprint(bars)


def test_collector_stops_after_denial_without_retry_or_host_change():
    client=Client(deny=True)
    with pytest.raises(RuntimeError,match='451'):
        module().collect_event_windows('BTCUSD',ts('2026-10-25'),client,RequestBudget(30,0),
            release_times=[ts('2026-09-01')],calendar_sha256='a'*64)
    assert len(client.calls)==1


def test_official_archive_exposes_only_known_publication_times_not_alfred_availability():
    from test_edge_family_macro_archive import archive,NOW
    value=archive()
    assert hasattr(value,'published_release_times'), 'validated release-time accessor missing'
    times=value.published_release_times()
    assert times==sorted(set(times))
    assert all(NOW-365*86400<=point<=NOW for point in times)
    for point in times:
        assert any(row.published_at==point for rows in value._points.values() for row in rows)


def test_source_collection_adds_separate_event_targets_without_changing_recent_price_bars(monkeypatch):
    from seiltanzer import mathematical_edge_sources as sources,binance_public_archive
    from test_edge_family_macro_archive import archive,NOW
    value=archive();bars=[sources._bar(NOW//300*300-300,100.,102.,99.,101.)]
    exact=sources._make_source('BTCUSD','Binance','BTCUSDT',bars,NOW,{})
    monkeypatch.setattr(binance_public_archive,'fetch_public_archive',lambda *a,**k:exact)
    monkeypatch.setattr(sources,'fetch_coinbase',lambda *a,**k:sources._make_source('BTCUSD','Coinbase Exchange','BTC-USD',bars,NOW,{}))
    calls=[]
    def collect(*args,**kwargs):
        calls.append(kwargs)
        return {'event_window_bars':[], 'event_window_archive':{'selection_policy':'calendar only'}}
    monkeypatch.setattr(module(),'collect_event_windows',collect)
    class Context:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    rows,errors,_=sources.collect_fresh_sources(NOW,codes=['BTCUSD'],macro_archive=value,client_factory=Context)
    assert calls[0]['release_times']==value.published_release_times()
    assert rows[0]['bars']==bars and rows[0]['source_sha256']==exact['source_sha256']
    assert rows[0]['event_window_archive']['selection_policy']=='calendar only'
    assert not errors


def test_runner_validates_official_calendar_before_source_acquisition(tmp_path,monkeypatch):
    import json,sys
    from scripts import run_mathematical_edge as runner
    from seiltanzer import mathematical_edge_sources as sources
    from test_edge_family_macro_archive import _bundle,NOW
    from test_macro_historical_offhost_bundle import SHA
    path=tmp_path/'official.json';path.write_text(json.dumps(_bundle()))
    seen=[]
    def collect(*args,**kwargs):seen.append(kwargs.get('macro_archive'));return [],{},{}
    monkeypatch.setattr(sources,'collect_fresh_sources',collect)
    monkeypatch.setattr(sys,'argv',['run','--refresh-sources','--captured-ts',str(NOW),
        '--historical-macro-bundle',str(path),'--macro-source-sha',SHA,'--output',str(tmp_path/'models.json')])
    runner.main()
    assert seen and seen[0] is not None, 'calendar must be validated before collecting older event windows'
    assert seen[0].published_release_times()


def event_fixture():
    from test_edge_family_macro_archive import archive,NOW
    from seiltanzer.mathematical_edge_sources import _bar
    value=archive();release=value.published_release_times()[0]
    start=int(release//300)*300+300
    bars=[_bar(start+i*300-300,100.,102.,99.,101.) for i in range(48)]
    period=__import__('datetime').datetime.fromtimestamp(start,__import__('datetime').timezone.utc).strftime('%Y-%m')
    filename=f'BTCUSDT-5m-{period}.zip'
    proof=dict(contract_version=module().CONTRACT,provider='Binance',symbol='BTCUSDT',instrument='BTCUSD',
        interval='5m',bars_sha256=module().fingerprint(bars),calendar_bundle_sha256=value.bundle_sha256,
        calendar_cutoff_ts=NOW,calendar_release_times=value.published_release_times(),selection_policy=module().POLICY,
        historical_first_receipt_known=False,files=[dict(url=f'{module().BASE}/monthly/klines/BTCUSDT/5m/{filename}',
            filename=filename,sha256='a'*64,checksum_sha256='b'*64,received_ts=NOW+1)])
    source=dict(instrument='BTCUSD',provider='Binance',ticker='BTCUSDT',event_window_bars=bars,event_window_archive=proof)
    return source,value,NOW


def test_event_target_union_is_calendar_selected_and_does_not_modify_recent_bars():
    from copy import deepcopy
    source,value,cutoff=event_fixture()
    recent=[dict(row) for row in source['event_window_bars'][-3:]]
    before=deepcopy(recent)
    selected=module().event_target_bars(source,recent,value,cutoff)
    assert selected==source['event_window_bars']
    assert recent==before
    bad=deepcopy(source);bad['event_window_bars'][0]['close']=100.5
    with pytest.raises(ValueError,match='HASH_INVALID'):
        module().event_target_bars(bad,recent,value,cutoff)


def test_event_proof_preserves_original_calendar_cutoff_when_model_is_loaded_later():
    source,value,cutoff=event_fixture()
    proof=source['event_window_archive']
    # A year-old publication used at fitting does not become a malformed
    # calendar merely because the fitted model is read a month later.
    assert module().event_archive_valid(proof,'BTCUSD',value.bundle_sha256,cutoff+30*86400)
    proof['files'][0]['received_ts']=cutoff+31*86400
    assert not module().event_archive_valid(proof,'BTCUSD',value.bundle_sha256,cutoff+30*86400)


def test_overlap_revision_conflict_is_rejected_instead_of_splicing_target_prices():
    source,value,cutoff=event_fixture()
    recent=[{**source['event_window_bars'][0],'close':100.5}]
    with pytest.raises(ValueError,match='REVISION_CONFLICT'):
        module().event_target_bars(source,recent,value,cutoff)


def test_fitted_event_window_provenance_requires_a_real_target_digest():
    from seiltanzer.edge_family_event_archive import archive_provenance_valid,CONTRACT,NAMES
    source,value,cutoff=event_fixture()
    digest=source['event_window_archive']['bars_sha256']
    model=dict(instrument='BTCUSD',event_timing_contract=CONTRACT,consensus_available=False,surprise_computed=False,
        features_are_captured_receipts=False,official_event_archive=value.summary(),
        heads={'direction':{'feature_names':list(NAMES)}},event_window_target_archive=source['event_window_archive'],
        source={'bars_sha256':digest},event_target_bars_sha256=digest)
    assert archive_provenance_valid(model,cutoff,cutoff+2)
    model['source']['bars_sha256']=None;model['event_target_bars_sha256']=None
    assert not archive_provenance_valid(model,cutoff,cutoff+2)


@pytest.mark.parametrize('status',[403,451,429])
def test_event_enrichment_does_not_reenter_an_archive_host_that_denied_primary_collection(monkeypatch,status):
    from seiltanzer import mathematical_edge_sources as sources,binance_public_archive
    from test_edge_family_macro_archive import archive,NOW
    bars=[sources._bar(NOW//300*300-300,100.,102.,99.,101.)]
    exact=sources._make_source('BTCUSD','Binance','BTCUSDT',bars,NOW,{})
    exact.update(captured_ts=NOW-1,fetched_ts=NOW-1,receipt_observed_ts=NOW-1)
    def denied(*args,**kwargs):raise RuntimeError(f'BINANCE_ARCHIVE_HTTP_{status}')
    monkeypatch.setattr(binance_public_archive,'fetch_public_archive',denied)
    monkeypatch.setattr(sources,'fetch_coinbase',lambda *args,**kwargs:sources._make_source('BTCUSD','Coinbase Exchange','BTC-USD',bars,NOW,{}))
    called=[]
    monkeypatch.setattr(module(),'collect_event_windows',lambda *a,**k:called.append(True) or {})
    class Context:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    rows,errors,attempts=sources.collect_fresh_sources(NOW,codes=['BTCUSD'],seed_sources=[exact],
        macro_archive=archive(),client_factory=Context)
    assert not called, 'the same denied archive host must not be retried for event history'
    assert rows[0]['provider']=='Binance' and not errors
    assert attempts['BTCUSD']['event_history_error']=='BINANCE_ARCHIVE_HOST_DENIED_DURING_PRIMARY_COLLECTION'
