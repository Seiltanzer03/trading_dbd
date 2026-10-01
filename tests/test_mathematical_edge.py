import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from seiltanzer import mathematical_edge as edge
from seiltanzer.mathematical_edge_archive import replay_frozen_fx_rule, phi_g, PHI_G_MEAN


def bars(n=13, end=None):
    end = end or math.floor(time.time() / 300) * 300
    return [dict(bar_end_ts=end - (n - i - 1) * 300, open=100+i*.01,
                 high=101+i*.01, low=99+i*.01, close=100+i*.01) for i in range(n)]


def runtime_fixture(tmp_path, monkeypatch):
    now = time.time()
    rows = bars(end=math.floor(now/300)*300)
    raw = [[b['bar_end_ts']-300+k*60, b['open'], b['high'], b['low'], b['close']] for b in rows for k in range(5)]
    n = len(edge.FEATURES)
    head = dict(mean=[0.]*n, scale=[1.]*n, beta=[math.log(.2/.8)] + [0.]*n, baseline=.5)
    diagnostic = dict(gain_mbit=20., positive_blocks=3, blocks=3, working_supported=True, test_n=120)
    model = dict(instrument='NAS100', horizon_minutes=15, feature_contract=edge.FEATURE_CONTRACT,
                 target_contract=dict(edge.TARGET_CONTRACT),
                 features=list(edge.FEATURES), heads={'direction':head,'movement':head},
                 diagnostics={'direction':diagnostic,'movement':diagnostic}, training_cutoff=now-86400)
    model['model_sha256'] = edge.fingerprint(model)
    from seiltanzer import runtime_git_identity
    monkeypatch.setattr(runtime_git_identity, 'runtime_git_sha', lambda: 'a'*40)
    report = dict(contract_version=edge.CONTRACT, created_ts=now-1, published_for_sha='a'*40,
                  automatic_execution=False, production_authority=False, instruments={'NAS100':model})
    dest=tmp_path/'research'/'mathematical_edge_latest.json';dest.parent.mkdir();dest.write_text(json.dumps(report))
    engine=SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), market=SimpleNamespace(instrument_code='NAS100',intraday_ohlcv=raw))
    return engine, {'ts': now, 'instrument':'NAS100'}, {'instrument':'NAS100','direction':'long'}, report, dest


def test_archive_all_167_rows_and_eight_veto_reproduce():
    result = replay_frozen_fx_rule(Path(__file__).parents[1]/'research_fixtures/mathematical_edge')
    assert result['n']==167 and result['veto_n']==8
    assert result['delta_gross_bp_per_base']==pytest.approx(.07244231955423992)
    assert not result['prospective_profit_proof']


def test_phi_g_is_recovered_separate_coordinate_and_missing_stays_missing():
    assert phi_g(*PHI_G_MEAN)==pytest.approx(0.)
    assert phi_g(None,0,0,0) is None


def test_h2_requires_exact_units_and_never_transfers_to_indices():
    assert not edge.h2_controller('NAS100',.4,-.0001,'archived-dot-gex-v1')['available']
    assert not edge.h2_controller('EURUSD',.4,-.0001,'generic-gex-slope')['available']
    result=edge.h2_controller('EURUSD',.4,-.0001,'archived-dot-gex-v1')
    assert result['action']=='VETO' and result['weight_fraction']==0
    assert edge.h2_controller('EURUSD',.6,-.0001,'archived-dot-gex-v1')['action']=='KEEP'


def test_live_historic_features_match_and_unfinished_future_minutes_excluded():
    rows=bars(end=6000)
    raw=[[b['bar_end_ts']-300+k*60,b['open'],b['high'],b['low'],b['close']] for b in rows for k in range(5)]
    raw.append([6000,999,999,999,999])
    live=edge.completed_live_bars(raw,6000)
    assert live==rows
    assert edge.price_features(live,6000)==edge.price_features(rows+[dict(rows[-1],bar_end_ts=6300,close=999)],6000)
    assert edge.price_features(rows[:4]+rows[5:],6000) is None
    assert edge.price_features(rows,7000) is None


def test_targets_do_not_overlap_or_cross_gaps():
    rows=bars(90,end=30000)
    x,y,t,e=edge.dataset(rows,15)
    assert len(x)>10 and np.all(e-t==900) and np.all(t[1:]>=e[:-1])
    _,_,t2,e2=edge.dataset(rows[:45]+rows[46:],15)
    assert not any(a<rows[45]['bar_end_ts']<b for a,b in zip(t2,e2))


def test_training_rejects_future_outcomes_and_is_stable_to_future_append():
    rows=bars(900,end=300000)
    a=edge.train_instrument('NAS100',rows,300000)
    b=edge.train_instrument('NAS100',rows+[dict(rows[-1],bar_end_ts=300300,close=1)],300000)
    assert a==b
    assert a['training_cutoff']<=300000
    assert a['test_cutoff']<a['training_cutoff']
    assert a['selection']=='earlier_three_validation_blocks_then_untouched_final_20pct'
    assert set(a['candidate_test_cutoffs'].values())=={a['test_cutoff']}
    assert a['target_contract']==edge.TARGET_CONTRACT


def test_runtime_changes_soft_preference_and_reverses_for_short(tmp_path,monkeypatch):
    engine,tick,trade,_,_=runtime_fixture(tmp_path,monkeypatch)
    long=edge.runtime_profile(engine,tick,trade)
    short=edge.runtime_profile(engine,tick,{**trade,'direction':'short'})
    assert long['available'] and 0<long['weight_fraction']<=.15
    assert long['direction_score']==pytest.approx(-.12)
    assert short['direction_score']==pytest.approx(.12)
    assert not long['hard_risk_modified'] and not long['independent_evidence_vote']


def test_flat_outcomes_cannot_become_down_votes_in_direction_head():
    y=np.array([[0,0,0.],[1,0,.0001],[0,1,-.001],[1,1,.001]])
    assert list(edge.head_indices(np.arange(4),y,0))==[2,3]
    assert list(edge.head_indices(np.arange(4),y,1))==[0,1,2,3]


@pytest.mark.parametrize('case',['hash','future_training','sha','stale','future_report','feed','nan'])
def test_runtime_invalid_data_fails_closed(tmp_path,monkeypatch,case):
    engine,tick,trade,report,dest=runtime_fixture(tmp_path,monkeypatch)
    model=report['instruments']['NAS100']
    if case=='hash':model['heads']['direction']['beta'][0]=9
    if case=='future_training':
        model['training_cutoff']=tick['ts']+60
        model['model_sha256']=edge.fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
    if case=='sha':report['published_for_sha']='b'*40
    if case=='stale':report['created_ts']=tick['ts']-8*86400
    if case=='future_report':report['created_ts']=tick['ts']+30
    if case=='feed':engine.market.instrument_code='EURUSD'
    if case=='nan':model['heads']['direction']['beta'][0]=float('nan')
    dest.write_text(json.dumps(report))
    result=edge.runtime_profile(engine,tick,trade)
    assert not result['available'] and result['weight_fraction']==0


def test_shared_weights_and_hard_floor_are_preserved():
    old=dict(available=True,weight_fraction=.4,direction_score=1,preferred_close_fraction=0,active_component_weight=.3,exploratory_component_weight=.1)
    mathematical=dict(available=True,weight_fraction=.15,direction_score=-1)
    combined=edge.combine_math_profile(old,mathematical)
    assert combined['weight_fraction']==.4
    assert sum(combined[k] for k in ('active_component_weight','exploratory_component_weight','mathematical_component_weight'))==pytest.approx(.4,abs=1e-6)
    from seiltanzer.active_edge_policy_weight import adjust_metrics_for_edge
    from seiltanzer.ai_policy_base import POLICY_FRACTIONS
    metrics={'HOLD':dict(expected_final_r=.1,cvar10_r=0),'CLOSE_50':dict(expected_final_r=.15,cvar10_r=0),'EXIT':dict(expected_final_r=.5,cvar10_r=-1)}
    adjusted,_=adjust_metrics_for_edge(metrics,combined,0,cvar_floor=-.5,policy_fractions=POLICY_FRACTIONS)
    assert adjusted['EXIT']==metrics['EXIT']
    assert all(adjusted[k]['cvar10_r']==metrics[k]['cvar10_r'] for k in metrics)


def test_movement_only_does_not_become_direction_or_close_preference(tmp_path,monkeypatch):
    engine,tick,trade,report,dest=runtime_fixture(tmp_path,monkeypatch)
    model=report['instruments']['NAS100']
    # Avoid shared fixture diagnostic object while changing only one head.
    model['diagnostics']['direction']={**model['diagnostics']['direction'], 'working_supported':False}
    model['model_sha256']=edge.fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
    dest.write_text(json.dumps(report))
    profile=edge.runtime_profile(engine,tick,trade)
    assert profile['available'] and not profile['base_policy_eligible']
    combined=edge.combine_math_profile({'available':False,'weight_fraction':0.},profile)
    assert not combined['available'] and combined['mathematical_component_weight']==0
    assert combined['mathematical_extended_component_weight']>0
    assert edge.extended_ranking_bonus('TIME_STOP',profile)>0
    assert edge.extended_ranking_bonus('TIGHTEN_STOP',profile)==0
    assert edge.extended_ranking_bonus('EXIT',profile)==0


def test_math_preserves_hold_in_indifference_and_never_buys_material_expected_loss():
    from seiltanzer import ai_policy
    from seiltanzer.active_edge_policy_weight import _PROFILE_CTX
    names=['HOLD','CLOSE_10','CLOSE_25','CLOSE_50','EXIT']
    math_profile=dict(available=True,weight_fraction=.15,direction_score=1,mathematical_component_weight=.15,preferred_close_fraction=0)
    def choice(values):
        metrics={n:dict(name=n,expected_final_r=e,cvar10_r=0.) for n,e in zip(names,values)}
        token=_PROFILE_CTX.set(math_profile)
        try:return ai_policy._raw_policy_choice(metrics,0,cvar_floor=-.5),metrics
        finally:_PROFILE_CTX.reset(token)
    (selected,rule),_=choice([.1]*5)
    assert selected=='HOLD'
    (selected,rule),metrics=choice([.1,.11,.12,.13,.15])
    assert selected!='HOLD'
    assert .15-metrics[selected]['expected_final_r']<=.03+1e-12
    assert rule['best_expected_r']==.15
    assert rule['combined_edge_soft_weight']['weighted_value_semantics']=='RANKING_SCORE_NOT_EXPECTED_RETURN'


def test_extended_edge_bonus_never_resurrects_blocked_candidates(monkeypatch):
    from seiltanzer import active_management as active
    monkeypatch.setattr(active,'build_working_action',lambda s,p:dict(policy=p['policy']))
    monkeypatch.setattr(active,'evaluate_extended_action',lambda s,a:dict(status='blocked',reason='NO_MATERIAL_ROBUST_EXPECTED_GAIN'))
    profile=dict(available=True,weight_fraction=.15,direction_score=-1)
    snapshot={'policy_manager':{'mathematical_edge':profile}}
    assert active.select_active_management(snapshot) is None
    assert all(r['mathematical_edge_ranking_bonus_r']==0 for r in snapshot['active_management_candidates'])
    assert edge.extended_ranking_bonus('TIGHTEN_STOP',profile)==.0045
    assert edge.extended_ranking_bonus('EXTEND_TAKE',profile)==0


def test_compaction_preserves_math_weight_and_ui_explanation(tmp_path,monkeypatch):
    from seiltanzer.ai_verdict import _capture_report_integrity,_restore_report_integrity_views
    engine,tick,trade,_,_=runtime_fixture(tmp_path,monkeypatch)
    profile=edge.runtime_profile(engine,tick,trade)
    combined=edge.combine_math_profile({'available':False,'weight_fraction':0},profile)
    frozen=_capture_report_integrity({'policy_manager':{'mathematical_edge':profile,'combined_edge_soft_weight':combined}})
    compact={'policy_manager':{}}
    _restore_report_integrity_views(compact,frozen)
    assert compact['policy_manager']['mathematical_edge']==profile
    assert compact['policy_manager']['combined_edge_soft_weight']['weight_fraction']==combined['weight_fraction']
    assert compact['policy_manager']['combined_edge_soft_weight']['mathematical_component_weight']==combined['mathematical_component_weight']
    text=edge.render_math_edge(profile,combined,{'raw_policy_without_mathematical_edge':'HOLD','raw_policy_with_edge':'CLOSE_25'})
    assert 'HOLD' in text and 'CLOSE_25' in text and 'proper-score' in text


def test_source_export_is_read_only_and_rejects_retroactive_offset_bars(tmp_path,capsys):
    import sqlite3,gzip,hashlib,base64
    from scripts.export_mathematical_edge_sources import REMOTE_EXPORT
    db=tmp_path/'sources.db';now=time.time(); start=math.floor((now-600)/300)*300
    historical=[dict(bar_end_ts=start-600,open=100.,high=101.,low=99.,close=100.)]
    raw=json.dumps(historical).encode()
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE g1s_historical_sources (source_id TEXT, source_sha256 TEXT, bars_gzip BLOB, ticker TEXT, provider TEXT, interval TEXT, source_semantics_json TEXT, instrument TEXT, contract_version TEXT, created_ts REAL)')
        c.execute('CREATE TABLE passive_market_bars (instrument TEXT, bar_start_ts REAL, bar_end_ts REAL, open REAL, high REAL, low REAL, close REAL, source TEXT, kind TEXT, created_ts REAL)')
        c.execute('INSERT INTO g1s_historical_sources VALUES (?,?,?,?,?,?,?,?,?,?)',('src',hashlib.sha256(raw).hexdigest(),gzip.compress(raw),'^NDX','yahoo','5m','{}','NAS100','g1s-historical-wf-real-bars-v1',now))
        for group,kind in [(-1,'partial'),(0,'direct'),(1,'derived')]:
            for k in range(5):
                ts=start+group*300+k*60
                stored_kind='direct' if kind=='partial' else kind
                created=ts+30 if kind=='partial' else now
                c.execute('INSERT INTO passive_market_bars VALUES (?,?,?,?,?,?,?,?,?,?)',('NAS100',ts,ts+60,100,101,99,100,'yahoo_1m',stored_kind,created))
    before=db.read_bytes()
    exec(REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db',str(db)),{})
    result=json.loads(gzip.decompress(base64.b64decode(capsys.readouterr().out.strip())))
    source=result['sources'][0]
    assert result['read_only'] and db.read_bytes()==before
    assert source['recent_completed_5m_n']==1 and source['excluded_derived_minute_n']==5
    assert source['excluded_partial_minute_n']==5
    assert len(source['bars'])==2 and source['not_broker_execution_bars']
