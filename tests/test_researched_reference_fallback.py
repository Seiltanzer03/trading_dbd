"""Research calculations consume actual frozen inputs, never borrowed outcomes."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest


def fixture(code='XAU'):
    from test_family_reference_market import reference_source, T0
    from test_research_working_context import inputs
    from seiltanzer.reference_market_series import SYMBOLS
    engine, row = inputs()
    row['captured_ts'] = T0
    row['strategy']['instrument'] = code
    engine.market.instrument_code = code
    reference = reference_source()
    reference.update(instrument=code, ticker=SYMBOLS[code], resolved_symbol=SYMBOLS[code])
    engine.market.family_reference_series = reference
    # Existing spot-offset native candles cannot be relabelled as original data.
    engine.market.intraday_source_authority['derived'] = True
    return engine, row


@pytest.mark.parametrize('code', ['NAS100','SP500','US30','GER40','UK100','JPY100','XAU','XAG','EURUSD','USDCAD'])
def test_each_noncrypto_reference_supplies_real_research_inputs(code):
    from seiltanzer.research_working_context import attach_research_working_context, research_context_audit
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, row = fixture(code)
    before = deepcopy(engine.market.family_reference_series)
    attach_research_working_context(engine, row)
    context = research_context_audit(row)
    assert context['available'] is True, 'configured reference is ignored by research fallback'
    assert context['source_provenance']['provider'] == 'TradingView chart'
    assert context['alternatives']['order_book']['broker_order_book_observed'] is False
    assert context['alternatives']['broker_swap']['broker_cost_verified'] is False
    families = build_edge_family_evidence(row)['families']
    assert 'flow.bar_pressure_proxy' in families['order_flow']['features']
    assert families['order_flow']['forecast_available'] is False
    assert engine.market.family_reference_series == before


def test_rogers_satchell_estimate_is_measured_per_bar_not_implied_or_annualized():
    from seiltanzer.research_working_context import attach_research_working_context, preliminary_family_features
    from seiltanzer.mathematical_edge import fingerprint
    engine, row = fixture()
    reference = engine.market.family_reference_series
    for bar in reference['bars']:
        bar.update(open=100., high=101.0050167084168, low=99.0049833749168, close=100.)
    reference['source_sha256'] = fingerprint(reference['bars'])
    attach_research_working_context(engine, row)
    estimator = row['research_working_context'].get('price_estimators', {}).get('rogers_satchell')
    assert estimator, 'no measured range-volatility alternative'
    # log(H/O)=+.01, log(L/O)=-.01, C=O: variance=.0002.
    assert estimator['value'] == pytest.approx(.01414213562373095, abs=1e-12)
    assert estimator['sample_n'] == 12
    assert estimator['interval_sec'] == 300
    assert estimator['unit'] == 'log_return_std_per_5m'
    assert estimator['measurement_kind'] == 'REALIZED_RANGE_VOLATILITY_NOT_IMPLIED'
    value, proof = preliminary_family_features(row)['option']['option.realized_range_volatility_5m']
    assert value == pytest.approx(.01414213562373095, abs=1e-12)
    assert proof['manual_preliminary'] is True and proof['dependency_group'] == 'price_path'


def test_original_reference_freeze_can_follow_older_ui_tick_but_not_builder_completion(monkeypatch):
    from test_family_reference_market import T0
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    from seiltanzer.research_working_context import attach_research_working_context, research_context_audit
    engine, row = fixture()
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time', lambda: T0+10)
    frozen = capture_market_inputs(engine)
    row['captured_ts'] = T0+100
    row['policy_manager']['input_audit'] = {'snapshot_ts': T0-1}
    attach_research_working_context(frozen, row)
    assert research_context_audit(row)['available'] is True
    assert row['research_working_context']['market_observation_ts'] == T0+10
    row['captured_ts'] = T0+200
    assert research_context_audit(row)['market_observation_ts'] == T0+10
    assert row['policy_manager']['input_audit']['snapshot_ts'] == T0-1


@pytest.mark.parametrize('case', ['symbol','hash','late','stale','gap','demo'])
def test_reference_research_stays_fail_closed(case):
    from test_family_reference_market import T0
    from seiltanzer.research_working_context import attach_research_working_context, research_working_component
    from seiltanzer.mathematical_edge import fingerprint
    engine, row = fixture()
    r = engine.market.family_reference_series
    if case == 'symbol':r['ticker'] = 'OANDA:NAS100USD'
    elif case == 'hash':r['bars'][-1]['close'] += .001
    elif case == 'late':r['received_ts'] = T0+1
    elif case == 'stale':row['captured_ts'] = T0+901
    elif case == 'gap':r['bars'].pop(-4);r['source_sha256'] = fingerprint(r['bars'])
    elif case == 'demo':engine.market.demo = True
    attach_research_working_context(engine, row)
    assert research_working_component(row) is None


def test_prompt_and_compact_audit_keep_measured_volatility_and_source_roles():
    from seiltanzer.research_working_context import attach_research_working_context
    from seiltanzer.llm_decision_shadow import _shadow_projection
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines
    engine, row = fixture()
    attach_research_working_context(engine, row)
    projected = _shadow_projection(row)
    assert projected['research_working_context']['price_estimators']['rogers_satchell']['sample_n'] == 12
    audit = compact_unified_ensemble(build_unified_ensemble(row))
    assert audit['research_working_context']['price_estimators']['rogers_satchell']['sample_n'] == 12
    text = '\n'.join(render_unified_ensemble_lines(audit))
    assert 'Rogers–Satchell' in text and 'не IV' in text
    json.dumps(audit, allow_nan=False)


def test_reference_research_fits_the_real_frozen_input_budget(tmp_path, monkeypatch):
    from test_family_reference_market import T0
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs, freeze_expert_inputs
    from seiltanzer.research_working_context import research_context_audit
    engine, row = fixture()
    engine.settings = SimpleNamespace(data_dir=tmp_path)
    row['policy_manager']['retained_existing_facts'] = 'q'*49000
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time', lambda:T0)
    frozen = capture_market_inputs(engine)
    freeze_expert_inputs(engine, row, market_inputs=frozen)
    assert len(json.dumps(row, ensure_ascii=False, separators=(',',':')).encode()) <= 60000
    assert research_context_audit(row)['available'] is True
    assert research_context_audit(row)['price_estimators']['rogers_satchell']['sample_n'] == 12


def test_finite_extreme_prices_cannot_overflow_the_range_estimator():
    import math
    from seiltanzer.research_working_context import attach_research_working_context
    from seiltanzer.mathematical_edge import fingerprint
    engine, row = fixture()
    reference = engine.market.family_reference_series
    for bar in reference['bars']:
        bar.update(open=1e-200, high=1e300, low=1e-300, close=1e200)
    reference['source_sha256'] = fingerprint(reference['bars'])
    attach_research_working_context(engine, row)
    value = row['research_working_context']['price_estimators']['rogers_satchell']['value']
    assert math.isfinite(value) and value >= 0
