from copy import deepcopy
from types import SimpleNamespace
import threading
import pytest

from test_family_reference_market import reference_source, T0


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr('seiltanzer.data.feeds.time.time',lambda:T0)


def feed():
    from seiltanzer.data.feeds import MarketData
    f=object.__new__(MarketData)
    f.instrument_code='NAS100';f.demo=False;f._intraday_lock=threading.RLock()
    f.family_reference_series=None
    return f


def test_reference_refresh_publishes_original_series_without_replacing_execution_quote(monkeypatch):
    from seiltanzer import reference_market_series as m
    f=feed();f.price={'value':17000.,'source':'existing execution quote'};before=deepcopy(f.price)
    source=reference_source();calls=[]
    def fetch(code,**kwargs):calls.append((code,kwargs));return source
    monkeypatch.setattr(m,'fetch_reference_series',fetch)
    f.refresh_family_reference('NAS100')
    assert f.family_reference_series==source
    assert f.price==before
    assert calls==[('NAS100',{'limit':120,'timeout':5.})]


def test_instrument_switch_during_fetch_cannot_publish_the_previous_reference(monkeypatch):
    from seiltanzer import reference_market_series as m
    f=feed()
    def fetch(*a,**k):f.instrument_code='SP500';return reference_source()
    monkeypatch.setattr(m,'fetch_reference_series',fetch)
    f.refresh_family_reference('NAS100')
    assert f.family_reference_series is None


def test_failed_optional_refresh_preserves_original_receipt_and_does_not_refetch(monkeypatch):
    from seiltanzer import reference_market_series as m
    f=feed();f.family_reference_series=reference_source();before=deepcopy(f.family_reference_series)
    def fetch(*a,**k):raise TimeoutError('timeout')
    monkeypatch.setattr(m,'fetch_reference_series',fetch)
    f.refresh_family_reference('NAS100')
    assert f.family_reference_series==before
    assert f.family_reference_error=='TimeoutError'


def test_frozen_reference_is_copied_with_original_clock_and_instrument():
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    f=feed();f.family_reference_series=reference_source()
    f.intraday_ohlcv=[];f.intraday_source_authority={};f.chain={'metrics':{'iv':.2}}
    frozen=capture_market_inputs(SimpleNamespace(market=f)).market
    f.family_reference_series['bars'][-1]['close']+=1
    assert frozen.family_reference_series['bars'][-1]['close']!=f.family_reference_series['bars'][-1]['close']
    assert frozen.family_reference_series['received_ts']==f.family_reference_series['received_ts']


def test_invalid_optional_reference_freeze_cannot_erase_other_inputs():
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    f=feed();f.intraday_ohlcv=[[1.,2.,3.,1.,2.]];f.intraday_source_authority={'provider':'Yahoo'}
    f.chain={'metrics':{'iv':.2}};f.family_reference_series={'oversized':[0]*4097}
    frozen=capture_market_inputs(SimpleNamespace(market=f)).market
    assert frozen.intraday_ohlcv==f.intraday_ohlcv and frozen.chain==f.chain
    assert frozen.family_reference_series is None
    assert frozen.reference_freeze_reason=='REFERENCE_INPUT_FREEZE_BOUND'


def test_reference_bar_progress_invalidates_background_inputs_even_when_cash_series_is_closed():
    from seiltanzer.ai_frozen_expert_inputs import completed_bar_marker
    from seiltanzer.mathematical_edge import fingerprint
    f=feed();f.intraday_source_authority={};f.family_reference_series=reference_source()
    engine=SimpleNamespace(market=f)
    before=completed_bar_marker(engine)
    assert before is not None
    f.family_reference_series['received_ts']+=30
    assert completed_bar_marker(engine)==before
    last={**f.family_reference_series['bars'][-1],'bar_end_ts':T0+300}
    f.family_reference_series['bars'].append(last)
    f.family_reference_series['observed_ts']=T0+300
    f.family_reference_series['source_sha256']=fingerprint(f.family_reference_series['bars'])
    assert completed_bar_marker(engine)!=before

@pytest.mark.parametrize('legacy_ahead',[False,True])
def test_reference_and_legacy_refresh_gap_does_not_rebuild_without_bar_progress(legacy_ahead):
    from seiltanzer.ai_frozen_expert_inputs import completed_bar_marker,completed_bar_advanced
    f=feed();f.family_reference_series=reference_source()
    original={'provider':'Yahoo','source_symbol':'^NDX','target_instrument':'NAS100',
              'source_verified':True,'observed_ts':T0+(300 if legacy_ahead else 0)}
    f.intraday_source_authority=dict(original);engine=SimpleNamespace(market=f)
    baseline=completed_bar_marker(engine)
    f.intraday_source_authority={}
    assert not completed_bar_advanced(baseline,completed_bar_marker(engine))
    f.intraday_source_authority=dict(original)
    assert not completed_bar_advanced(baseline,completed_bar_marker(engine))
    f.intraday_source_authority['observed_ts']+=300
    assert completed_bar_advanced(baseline,completed_bar_marker(engine))


def test_reference_bar_catching_up_to_legacy_is_actual_new_target_input():
    from seiltanzer.ai_frozen_expert_inputs import completed_bar_marker,completed_bar_advanced
    f=feed();f.family_reference_series=reference_source()
    f.intraday_source_authority={'target_instrument':'NAS100','observed_ts':T0+300}
    engine=SimpleNamespace(market=f);before=completed_bar_marker(engine)
    f.family_reference_series['observed_ts']=T0+300
    f.family_reference_series['bars'][-1]['bar_end_ts']=T0+300
    assert completed_bar_advanced(before,completed_bar_marker(engine))


def test_successful_market_capture_records_actual_freeze_clock():
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    f=feed();f.intraday_ohlcv=[];f.intraday_source_authority={}
    f.family_reference_series=reference_source()
    frozen=capture_market_inputs(SimpleNamespace(market=f)).market
    assert frozen.frozen_inputs_captured_ts==T0
