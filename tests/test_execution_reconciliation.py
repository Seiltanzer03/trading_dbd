"""Real ledgers and API must preserve uncertain prior execution, never issue a new order."""
import time
import pytest
from seiltanzer.position_state import PositionLedger
from test_decision_publication import _trade, _snapshot, _extended
from test_ai_management_api import client


def issue(ledger,trade,policy='EXIT',review='old',captured=2000.):
    snapshot=_snapshot(ledger,trade,policy if policy in ('EXIT','CLOSE_10') else 'HOLD',captured)
    if policy=='TIGHTEN_STOP':
        return ledger.register_shadow_action(snapshot,review,trade,_extended(105.))['action_id']
    return ledger.register_decision(snapshot,review,trade)['decision_id']


@pytest.mark.parametrize('policy',['EXIT','CLOSE_10','TIGHTEN_STOP'])
def test_saved_pending_execution_is_a_reconciliation_requirement(tmp_path,policy):
    ledger=PositionLedger(str(tmp_path/'db'));trade=_trade()
    identity=issue(ledger,trade,policy)
    before=ledger.state(trade)
    context=ledger.execution_reconciliation(trade)
    assert context['required'] is True and context['broker_state_verified'] is False
    assert context['commands'][0]['decision_id']==identity
    assert ledger.state(trade)==before
    assert ledger.execution_reconciliation(trade,ignore_review_id='old') is None
    # Ignoring one's own review cannot hide another review's pending command.
    assert ledger.execution_reconciliation(trade,ignore_review_id='new')['required'] is True
    ledger.close()


def test_superseding_a_stop_does_not_establish_broker_non_execution(tmp_path):
    ledger=PositionLedger(str(tmp_path/'db'));trade=_trade()
    identity=issue(ledger,trade,'TIGHTEN_STOP')
    ledger.supersede_other_pending_actions(1,'old-hold')
    assert ledger.execution_reconciliation(trade)['commands'][0]['decision_id']==identity
    ledger.acknowledge_shadow_action(action_id=identity,trade=trade,executed=False,
                                    execution_price=None,execution_r=None)
    assert ledger.execution_reconciliation(trade) is None
    ledger.close()


@pytest.mark.parametrize('executed',[False,True])
def test_explicit_pending_exit_receipt_resolves_uncertainty(tmp_path,executed):
    ledger=PositionLedger(str(tmp_path/'db'));trade=_trade();identity=issue(ledger,trade)
    ledger.acknowledge(decision_id=identity,trade=trade,executed=executed,
                       execution_price=104.,execution_r=.4)
    assert ledger.execution_reconciliation(trade) is None
    assert ledger.state(trade)['remaining_position_fraction']==(0. if executed else 1.)
    ledger.close()


@pytest.mark.parametrize('policy',['EXIT','CLOSE_10'])
def test_api_requires_reconciliation_before_snapshot_and_preserves_pending(client,monkeypatch,policy):
    engine=client.app.state.engine;trade=engine.journal.active_trade()
    identity=issue(engine.position,trade,policy,captured=time.time())
    monkeypatch.setattr('seiltanzer.app.build_snapshot',lambda _: (_ for _ in ()).throw(AssertionError('must not build a new review')))
    before=engine.journal._conn.execute('SELECT COUNT(*) FROM ai_verdicts').fetchone()[0]
    response=client.post('/api/ai/verdict');body=response.json()
    assert response.status_code==409
    assert body['error']['code']=='broker_reconciliation_required'
    assert body['reconciliation']['commands'][0]['decision_id']==identity
    assert body['reconciliation']['trade_id']==trade['id']
    assert engine.journal._conn.execute('SELECT COUNT(*) FROM ai_verdicts').fetchone()[0]==before
    assert engine.position._conn.execute('SELECT status FROM management_decisions WHERE decision_id=?',(identity,)).fetchone()[0]=='pending_execution'
    assert engine.position._conn.execute('SELECT COUNT(*) FROM management_decisions').fetchone()[0]==1


@pytest.mark.parametrize('pending_review',['other','current'])
def test_publication_rechecks_late_pending_but_ignores_its_own_command(tmp_path,monkeypatch,pending_review):
    from types import SimpleNamespace
    from threading import RLock
    from seiltanzer.app import _assert_unified_review_current
    from seiltanzer.position_state import StaleDecisionError
    ledger=PositionLedger(str(tmp_path/'db'));trade=_trade()
    snapshot=_snapshot(ledger,trade,'HOLD',3000.);snapshot['review_id']='current'
    engine=SimpleNamespace(position=ledger,journal=SimpleNamespace(_lock=RLock(),active_trade=lambda:trade))
    monkeypatch.setattr('seiltanzer.ai_snapshot_market_refresh.assert_live_review_compatible',lambda *a,**k:None)
    issue(ledger,trade,review=pending_review)
    if pending_review=='other':
        with pytest.raises(StaleDecisionError,match='broker_reconciliation_required'):
            _assert_unified_review_current(engine,snapshot,trade,{'candidates':[]})
    else:
        _assert_unified_review_current(engine,snapshot,trade,{'candidates':[]})
    ledger.close()


def test_factual_non_execution_does_not_require_a_market_quote(client,monkeypatch):
    engine=client.app.state.engine;trade=engine.journal.active_trade()
    identity=issue(engine.position,trade,captured=time.time())
    monkeypatch.setattr(engine,'tick_payload',lambda: (_ for _ in ()).throw(RuntimeError('quote unavailable')))
    response=client.post('/api/ai/decision/ack',json={'trade_id':trade['id'],'decision_id':identity,'executed':False})
    assert response.status_code==200
    assert response.json()['position_state']['remaining_position_fraction']==1.
    assert engine.position.execution_reconciliation(trade) is None


def test_background_delivery_retains_specific_reconciliation_context():
    import asyncio,json
    from fastapi.responses import JSONResponse
    from seiltanzer.ai_review_jobs import ReviewJobs,bind_delivery_guard
    from seiltanzer.position_state import BrokerReconciliationRequired
    context={'required':True,'trade_id':141,'commands':[{'decision_id':'old-exit'}]}
    async def run():
        jobs=ReviewJobs()
        def guard():raise BrokerReconciliationRequired(context)
        async def work():
            bind_delivery_guard(guard)
            return JSONResponse({'ok':True,'verdict':'must not deliver'})
        identity=json.loads(jobs.start(work).body)['review_job']['job_id']
        await asyncio.sleep(.01)
        response=await jobs.poll(identity);body=json.loads(response.body)
        assert response.status_code==409
        assert body['error']['code']=='broker_reconciliation_required'
        assert body['reconciliation']==context and 'verdict' not in body
    asyncio.run(run())


def test_release_smoke_verifies_reconciliation_against_actual_position_context(client):
    from scripts import production_functional_smoke as smoke
    engine=client.app.state.engine;trade=engine.journal.active_trade()
    issue(engine.position,trade,captured=time.time())
    body=client.post('/api/ai/verdict').json();position=client.get('/api/position').json()
    smoke.verify_execution_reconciliation_contract(body,position)
    import copy
    broken=copy.deepcopy(body);broken['reconciliation']['commands']=[]
    with pytest.raises(AssertionError):smoke.verify_execution_reconciliation_contract(broken,position)


def test_legacy_exit_replaced_by_close_requires_both_factual_outcomes(tmp_path):
    from seiltanzer.position_state import StaleDecisionError
    ledger=PositionLedger(str(tmp_path/'db'));trade=_trade()
    old=issue(ledger,trade,'EXIT',captured=2000.)
    new=issue(ledger,trade,'CLOSE_10',review='new',captured=2100.)
    ledger.acknowledge(decision_id=new,trade=trade,executed=False,execution_price=None,execution_r=None)
    context=ledger.execution_reconciliation(trade)
    assert context and [c['decision_id'] for c in context['commands']]==[old]
    with pytest.raises(StaleDecisionError):
        ledger.acknowledge(decision_id=old,trade=trade,executed=True,execution_price=104.,execution_r=.4)
    ledger.acknowledge(decision_id=old,trade=trade,executed=False,execution_price=None,execution_r=None)
    assert ledger.execution_reconciliation(trade) is None
    assert ledger.state(trade)['remaining_position_fraction']==1.
    ledger.close()


@pytest.mark.parametrize('policy',['EXIT','TIGHTEN_STOP'])
def test_stale_command_can_be_declined_after_independent_real_partial_fill(tmp_path,policy):
    ledger=PositionLedger(str(tmp_path/'db'));trade=_trade();identity=issue(ledger,trade,policy)
    ledger.record_manual_fill(trade,request_id='observed-fill',close_fraction_current=.1,
                              execution_price=104.,execution_r=.4)
    before=ledger.state(trade)
    if policy=='EXIT':
        result=ledger.acknowledge(decision_id=identity,trade=trade,executed=False,
                                  execution_price=None,execution_r=None)
    else:
        result=ledger.acknowledge_shadow_action(action_id=identity,trade=trade,executed=False,
                                                execution_price=None,execution_r=None)
    assert result['execution_status']=='recommended_not_executed'
    assert ledger.execution_reconciliation(trade) is None
    assert ledger.state(trade)==before
    assert before['remaining_position_fraction']==pytest.approx(.9)
    ledger.close()


def test_old_delivery_reconciliation_cannot_invalidate_replacement_job():
    import asyncio,json,threading
    from fastapi.responses import JSONResponse
    from seiltanzer.ai_review_jobs import ReviewJobs,bind_delivery_guard
    from seiltanzer.position_state import BrokerReconciliationRequired
    entered=threading.Event();release=threading.Event()
    async def run():
        jobs=ReviewJobs()
        def guard():
            entered.set();assert release.wait(2)
            raise BrokerReconciliationRequired({'required':True,'trade_id':999})
        async def old():
            bind_delivery_guard(guard);return JSONResponse({'ok':True,'verdict':'old'})
        identity=json.loads(jobs.start(old).body)['review_job']['job_id']
        await asyncio.sleep(.01)
        polling=asyncio.create_task(jobs.poll(identity))
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.001)
        assert entered.is_set()
        async def current():return JSONResponse({'ok':True,'verdict':'current'})
        replacement=json.loads(jobs.start(current).body)['review_job']['job_id']
        release.set();await polling;await asyncio.sleep(.01)
        response=await jobs.poll(replacement)
        assert response.status_code==200 and json.loads(response.body)['verdict']=='current'
    try:asyncio.run(run())
    finally:release.set()
