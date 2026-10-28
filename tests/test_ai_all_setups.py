"""Exercise configured strategy geometry and filters, never observed PNL."""
import json

import pytest

from seiltanzer.ai_verdict import build_snapshot, render_policy_report
from seiltanzer.config import Settings
from seiltanzer.engine import Engine


# Independent fixtures retain FX precision, the Nikkei identity and setup11's
# distinct2.7R target. These are test trades in temporary DBs, not live quotes.
CASES = [
    (1,'NAS100','long',21500.,215.,2.5,[]),
    (2,'NAS100','long',21500.,215.,2.5,[]),
    (3,'NAS100','long',21500.,215.,2.5,[]),
    (4,'SP500','long',6100.,61.,2.5,[]),
    (5,'SP500','long',6100.,61.,2.5,['vix']),
    (6,'US30','long',44500.,445.,2.5,['vix']),
    (7,'GER40','long',24300.,243.,2.5,['dv1x']),
    (8,'GER40','long',24300.,243.,2.5,[]),
    (9,'UK100','long',8900.,89.,2.5,[]),
    (10,'JPY100','long',62500.,625.,2.5,[]),
    (11,'XAU','long',4006.,30.,2.7,['vix','gvz']),
    (12,'XAU','long',4006.,30.,2.5,[]),
    (13,'XAG','long',38.,.38,2.5,[]),
    (14,'EURUSD','long',1.17,.0017,2.5,[]),
    (15,'EURUSD','short',1.17,.0017,2.5,[]),
    (16,'USDCAD','short',1.37,.0037,2.5,[]),
]


@pytest.mark.parametrize('setup,code,direction,entry,risk,rr,filters', CASES)
def test_complete_decision_builder_retains_each_setup_geometry_and_filters(
        tmp_path, setup, code, direction, entry, risk, rr, filters):
    engine = Engine(Settings(demo=True, data_dir=str(tmp_path)))
    try:
        engine.market.set_instrument(code)
        engine.market.refresh_price()
        engine.market.refresh_vols()
        engine.market.refresh_correlation()
        sign = 1 if direction == 'long' else -1
        stop, take = entry-sign*risk, entry+sign*risk*rr
        trade = engine.journal.open_trade(setup, code, direction, entry, stop, take)
        engine.position.open_trade(trade)
        engine.on_trade_opened(trade)
        snapshot = build_snapshot(engine)
        assert snapshot['strategy']['setup'] == setup
        assert snapshot['strategy']['instrument'] == code
        assert snapshot['strategy']['direction'] == direction
        geometry = snapshot['trade_geometry']
        assert geometry['entry'] == entry
        assert geometry['original_stop'] == stop
        assert geometry['final_take'] == take
        assert abs(take-entry)/abs(entry-stop) == pytest.approx(rr)
        manager = snapshot['policy_manager']
        assert set(manager['policies']) == {'HOLD','CLOSE_10','CLOSE_25','CLOSE_50','EXIT'}
        required_context = snapshot['strategy'].get('required_filter_context', [])
        required = [row['key'] for row in required_context]
        assert required == filters
        for row in required_context:
            assert row['complete_setup_confirmation'] is False
            assert row['decision_weight'] is False
            assert row['status_feed'] == 'demo'
        if required_context:
            from seiltanzer.ai_runtime_report_v20 import _independent_provider_facts
            from seiltanzer.ai_provider_decision_packet import build_decision_packet
            facts = _independent_provider_facts(snapshot, snapshot)
            packet, _ = build_decision_packet(facts)
            assert packet['strategy']['required_filter_context'] == required_context
        assert snapshot['metric_coverage']['summary']['total_groups'] == 12
        assert snapshot['position_state']['active_stop_price'] == stop
        assert len(json.dumps(snapshot, ensure_ascii=False, separators=(',',':')).encode()) < 60000
        report = render_policy_report(snapshot)
        assert isinstance(report, str) and 'CVaR' in report
        for row in required_context:
            assert row['label'] in report
    finally:
        engine.close()


def test_required_filter_receipt_cannot_borrow_a_later_mutable_feed_timestamp(tmp_path):
    from copy import deepcopy
    from seiltanzer.ai_verdict_base import _strategy
    engine = Engine(Settings(demo=False, data_dir=str(tmp_path)))
    try:
        engine.market.set_instrument('XAU')
        trade = engine.journal.open_trade(11, 'XAU', 'long', 4006., 3976., 4087.)
        engine.market.vols['vix'] = {'value':21., 'ts':100., 'status':'delayed',
                                   'source':'original VIX receipt', 'timeframe':'15m'}
        engine.market.vols['gvz'] = {'value':17., 'ts':100., 'status':'delayed'}
        tick = {'filters':engine._filters_payload(trade), 'feeds':{'vols':engine.market.vols}}
        engine.market.vols['vix'] = {'value':19., 'ts':200., 'status':'delayed',
                                   'source':'later VIX receipt', 'timeframe':'15m'}
        context = _strategy(engine, trade, tick=deepcopy(tick))['required_filter_context']
        vix = next(row for row in context if row['key'] == 'vix')
        assert (vix['value'], vix['state'], vix['source_ts'], vix['source']) == (
            21., 'pass', 100., 'original VIX receipt')
    finally:
        engine.close()
