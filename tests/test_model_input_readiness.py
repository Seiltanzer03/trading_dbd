import pytest

from seiltanzer.ai_snapshot_materializer import AISnapshotMaterializer,SnapshotNotReady
from test_ai_snapshot_materializer import FakeEngine,snapshot


def receipt(count=0,trained=True,provider='TradingView chart'):
    return {'contract_version':'family-runtime-forecast-receipt-v1','instrument':'GER40',
        'available_forecasts_at_capture':count,'families':{'session':{
            'trained_model_valid':trained,'target_provider':provider,
            'target_symbol':'FPMARKETS:GER40','forecast_available_at_capture':False,
            'reason':'REFERENCE_LATEST_COMPLETED_BAR_UNAVAILABLE'}}}


@pytest.mark.parametrize('had_ready_snapshot',[False,True])
def test_missing_current_reference_cannot_be_published_as_ready_zero_forecast_review(monkeypatch,had_ready_snapshot):
    from seiltanzer import edge_family_market
    current=receipt(count=1 if had_ready_snapshot else 0)
    monkeypatch.setattr(edge_family_market,'family_runtime_status',lambda _:current)
    mat=AISnapshotMaterializer(FakeEngine(),lambda _:snapshot())
    if had_ready_snapshot:
        mat._build_once()
        assert mat.status()['ready'] is True
        current=receipt()
    mat._build_once()
    assert mat.status()['ready'] is False
    assert mat.status()['invalidated_reason']=='CURRENT_REFERENCE_MODEL_INPUTS_PENDING'
    assert mat.status()['family_market_forecasts']['available_forecasts_at_capture']==0
    with pytest.raises(SnapshotNotReady):mat.cached_build_snapshot(mat.engine)
    current=receipt(count=1)
    mat._build_once()
    assert mat.status()['ready'] is True
    assert mat.status()['last_error'] is None


@pytest.mark.parametrize('current',[receipt(count=1),receipt(trained=False),receipt(provider='Yahoo Finance via yfinance'),{}])
def test_untrained_families_and_unrelated_missing_cash_inputs_do_not_block_ready_review(monkeypatch,current):
    from seiltanzer import edge_family_market
    monkeypatch.setattr(edge_family_market,'family_runtime_status',lambda _:current)
    mat=AISnapshotMaterializer(FakeEngine(),lambda _:snapshot())
    mat._build_once()
    assert mat.status()['ready'] is True


def test_math_report_distinguishes_preliminary_pool_from_missing_trained_forecast():
    from seiltanzer.mathematical_edge import render_math_edge
    text=render_math_edge({'reason':'COMPLETED_CAUSAL_5M_PRICE_FEATURES_UNAVAILABLE'},
        unified_component={'available':True,'effective_weight':.02,
            'reason':'PRELIMINARY_SOURCE_BOUND_RESEARCH_RULES'})
    assert 'Обученный математический блок: вес 0' in text
    assert '2.0%' in text
    assert 'предварительных правил' in text
    assert 'не обученный прогноз' in text


def test_a_busy_market_copy_cannot_run_the_heavy_builder_or_publish_ready():
    import threading
    engine=FakeEngine();engine.market._intraday_lock=threading.Lock()
    builds=[]
    mat=AISnapshotMaterializer(engine,lambda _:(builds.append(1) or snapshot()))
    engine.market._intraday_lock.acquire()
    try:
        mat._build_once()
        assert builds==[]
        assert mat.status()['ready'] is False
        assert mat.status()['invalidated_reason']=='MARKET_INPUT_FREEZE_BUSY'
    finally:engine.market._intraday_lock.release()
    mat._build_once()
    assert mat.status()['ready'] is True
