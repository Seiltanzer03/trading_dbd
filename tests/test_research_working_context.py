from copy import deepcopy
import importlib
import importlib.util
from types import SimpleNamespace

import pytest

from test_unified_edge_ensemble import snapshot as management_snapshot

T0 = 1791900300.


def module():
    assert importlib.util.find_spec('seiltanzer.research_working_context'), 'preliminary working context missing'
    return importlib.import_module('seiltanzer.research_working_context')


def inputs():
    value = management_snapshot()
    value['captured_ts'] = T0
    value['strategy'].update(direction='long')
    value['trade_geometry'] = {'entry': 100., 'original_stop': 99., 'current': 101.}
    value['policy_manager']['inputs'] = {'horizon_minutes': 60}
    value['policy_manager']['mathematical_edge'] = {'available': False}
    bars = []
    for i in range(80):
        opened = 100 + .02*i
        bars.append([T0-4800+60*i, opened, opened+.04, opened-.02, opened+.02, 10.])
    feed = SimpleNamespace(instrument_code='NAS100', demo=False, intraday_is_offset=False,
        intraday_ohlcv=bars, intraday_source_authority={
        'provider': 'Yahoo', 'source_symbol': '^NDX', 'target_instrument': 'NAS100',
        'source_verified': True, 'observed_ts': T0-60, 'available_at': T0})
    return SimpleNamespace(market=feed), value


def test_frozen_price_proxy_has_manual_rules_and_no_missing_input_authority():
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    context = value['research_working_context']
    assert context['available'] is True
    assert context['alternatives']['order_book']['measurement_kind'] == 'BAR_PRESSURE_PROXY_NOT_ORDER_BOOK'
    assert context['alternatives']['order_book']['broker_order_book_observed'] is False
    assert context['alternatives']['consensus']['market_consensus_observed'] is False
    carry = context['alternatives']['broker_swap']
    assert carry['available'] is True and carry['broker_cost_verified'] is False
    assert carry['applied_to_net_economics'] is False
    assert any(row['cost_r_assumption'] > 0 for row in carry['scenarios'])
    component = module().research_working_component(value)
    assert component['component_id'] == 'mathematical_edge'
    assert component['quality'] == .25
    assert component['historical_profit_proven'] is False
    assert component['evidence_family_ids'] == ['price_path']


@pytest.mark.parametrize('change', ['symbol', 'future', 'stale', 'demo', 'gap'])
def test_unadmitted_or_discontinuous_bars_cannot_supply_preliminary_vote(change):
    engine, value = inputs()
    if change == 'symbol':engine.market.intraday_source_authority['source_symbol'] = '^SPX'
    if change == 'future':engine.market.intraday_source_authority['available_at'] = T0+1
    if change == 'stale':engine.market.intraday_source_authority['observed_ts'] = T0-901
    if change == 'demo':engine.market.demo = True
    if change == 'gap':engine.market.intraday_ohlcv.pop(-20)
    module().attach_research_working_context(engine, value)
    assert module().research_working_component(value) is None


def test_missing_consensus_uses_release_change_without_backdating_expectation():
    engine, value = inputs()
    value['edge_family_sources'] = {'event': {'source_id': 'official-release',
        'source_verified': True, 'instrument': 'NAS100', 'observed_ts': T0-20,
        'received_ts': T0-10, 'quality': 1., 'published_at': T0-20,
        'release_id': 'release-1', 'period': '2026-09', 'unit': 'percent',
        'actual': 3., 'previous_value': 2.}}
    module().attach_research_working_context(engine, value)
    benchmark = value['research_working_context']['alternatives']['consensus']
    assert benchmark['available'] is True
    assert benchmark['actual_minus_previous_reference'] == 1.
    assert benchmark['market_consensus_observed'] is False
    assert benchmark['prepublication_expectation_reconstructed'] is False
    value['edge_family_sources']['event']['previous_unit'] = 'basis_points'
    module().attach_research_working_context(engine, value)
    assert value['research_working_context']['alternatives']['consensus']['available'] is False


def test_existing_official_native_release_supplies_previous_reading_reference():
    engine, value = inputs()
    value['macro_context_v1'] = {'numeric_macro': {'releases': {'NFP': {
        'status': 'VALID', 'official_source_verified': True, 'release_id': 'official-nfp',
        'published_at': T0-20, 'available_at': T0-10, 'period': '2026-09',
        'payload': {'payroll_change_k': 120., 'previous_payroll_change_k': 100.}}}}}
    module().attach_research_working_context(engine, value)
    proxy = value['research_working_context']['alternatives']['consensus']
    assert proxy['available'] is True
    assert proxy['actual_minus_previous_reference'] == 20.
    assert proxy['unit'] == 'thousands_of_jobs'
    assert proxy['source_id'] == 'official-nfp'
    value['macro_context_v1']['numeric_macro']['releases']['NFP'].pop('published_at')
    module().attach_research_working_context(engine, value)
    proxy = value['research_working_context']['alternatives']['consensus']
    assert proxy['available'] is True
    assert proxy['source_clock_kind'] == 'OFFICIAL_FIRST_AVAILABILITY_NOT_EVENT_PUBLICATION'
    assert proxy['observed_ts'] == T0-10


def test_extreme_numeric_receipt_is_rejected_without_snapshot_crash():
    engine, value = inputs()
    engine.market.intraday_source_authority['available_at'] = 10**1000
    module().attach_research_working_context(engine, value)
    assert module().research_working_component(value) is None


def test_context_cannot_vote_with_observations_after_original_snapshot_cutoff():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    value['policy_manager']['input_audit'] = {'snapshot_ts': T0-600}
    assert module().research_working_component(value) is None
    assert module().research_context_audit(value)['available'] is False
    assert 'flow.bar_pressure_proxy' not in build_edge_family_evidence(value)['families']['order_flow']['features']


def test_context_retains_original_clock_after_later_quote_reprice():
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    value['captured_ts'] = T0+60
    value['policy_manager']['input_audit'] = {'snapshot_ts': T0+30}
    assert module().research_working_component(value) is not None
    assert module().research_context_audit(value)['market_observation_ts'] == T0


@pytest.mark.parametrize('alternative,field', [
    ('consensus', 'max_age_sec'), ('consensus', 'source_clock_kind'),
    ('consensus', 'measurement_kind'), ('broker_swap', 'measurement_kind')])
def test_incomplete_available_alternative_is_rejected_before_family_projection(alternative, field):
    from seiltanzer.mathematical_edge import fingerprint
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, value = inputs()
    value['edge_family_sources'] = {'event': {'source_id': 'official-release',
        'source_verified': True, 'instrument': 'NAS100', 'observed_ts': T0-20,
        'received_ts': T0-10, 'published_at': T0-20, 'release_id': 'release-1',
        'period': '2026-09', 'unit': 'percent', 'actual': 3., 'previous_value': 2.}}
    module().attach_research_working_context(engine, value)
    context = value['research_working_context']
    assert context['alternatives'][alternative]['available'] is True
    context['alternatives'][alternative].pop(field)
    context['context_sha256'] = fingerprint({k:v for k,v in context.items() if k!='context_sha256'})
    assert module().research_context_audit(value)['available'] is False
    assert module().preliminary_family_features(value) == {}
    assert build_edge_family_evidence(value)['families']['order_flow']['forecast_available'] is False


def test_bounded_long_flat_price_history_is_not_treated_as_simulated():
    engine, value = inputs()
    engine.market.intraday_ohlcv = [[T0-2500*60+i*60, 100+i*.001,
        100+i*.001+.04, 100+i*.001-.02, 100+i*.001+.02, 10.] for i in range(2500)]
    module().attach_research_working_context(engine, value)
    assert value['research_working_context']['available'] is True


def test_preliminary_fallback_changes_preferences_without_risk_or_economic_mutation():
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    original = deepcopy(value)
    audit = build_unified_ensemble(value)
    math = next(row for row in audit['components'] if row['component_id']=='mathematical_edge')
    assert math['available'] is True and math['effective_weight'] > 0
    assert math['reason'] == 'PRELIMINARY_SOURCE_BOUND_RESEARCH_RULES'
    assert audit['automatic_execution_allowed'] is False
    assert next(row for row in audit['candidates'] if row['policy']=='EXIT')['eligible'] is False
    assert value == original
    value['policy_manager']['mathematical_edge'] = {'available': True, 'direction_score': -.7}
    trained = build_unified_ensemble(value)
    math = next(row for row in trained['components'] if row['component_id']=='mathematical_edge')
    assert math['reason'] != 'PRELIMINARY_SOURCE_BOUND_RESEARCH_RULES'


def test_labelled_alternatives_can_supply_manual_family_opinions_but_not_training():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    from seiltanzer.edge_family_working import working_family_preferences
    from seiltanzer.edge_family_training import _feature_reason
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    families = build_edge_family_evidence(value)['families']
    feature_names = {'order_flow': 'flow.bar_pressure_proxy',
                     'value_carry': 'carry.assumed_15pct_3day_cost_r'}
    llm = {'status': 'ok', 'captured_ts': T0, 'policy_scores': {},
        'family_assessments': {f:{'feature_names':[name], 'policy_scores':{'HOLD':0.,'CLOSE_25':.5},
                                 'reason_ru':'Предварительная оценка явно обозначенного приближения.'}
                              for f,name in feature_names.items()}}
    opinions = working_family_preferences(families, llm)
    assert opinions['accepted_count'] == 2
    for family, name in feature_names.items():
        meta = families[family]['feature_provenance'][name]
        assert meta['context_only'] is True
        assert _feature_reason(meta, 'NAS100', family, T0) is not None
        assert opinions['families'][family]['working_preliminary_weight_factor'] == .25
    assert sum(opinions['attribution_weights']['CLOSE_25'].values()) == pytest.approx(.25)
    assert families['order_flow']['forecast_available'] is False
    assert families['order_flow']['readiness'] == 'PRELIMINARY_CONTEXT_ONLY'
    assert families['value_carry'].get('broker_carry_available') is not True


def test_independent_prompt_and_final_audit_keep_approximation_roles():
    from seiltanzer.llm_decision_shadow import _shadow_projection
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    projection = _shadow_projection(value)
    context = projection.get('research_working_context')
    assert context and context['alternatives']['broker_swap']['broker_cost_verified'] is False
    assert all('affinity' not in row for row in context['rules'])
    audit = compact_unified_ensemble(build_unified_ensemble(value))
    assert audit.get('research_working_context')
    scenarios = audit['research_working_context']['alternatives']['broker_swap'].get('scenarios')
    assert isinstance(scenarios, list) and len(scenarios) == 6
    assert any(row['cost_r_assumption'] > 0 for row in scenarios)
    rendered = '\n'.join(render_unified_ensemble_lines(audit)).lower()
    assert 'стакан' in rendered and 'своп' in rendered and 'consensus' in rendered


def test_frozen_input_pipeline_attaches_preliminary_context(tmp_path, monkeypatch):
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    import seiltanzer.app as app
    engine, value = inputs()
    engine.settings = SimpleNamespace(data_dir=tmp_path)
    monkeypatch.setattr(app, '_attach_family_source_bundle', lambda engine, snapshot: None)
    freeze_expert_inputs(engine, value)
    assert value.get('research_working_context', {}).get('available') is True


def test_archive_keeps_real_feature_while_excluding_assumed_carry_from_training():
    import hashlib
    from test_edge_family_archive import episode, canonical, assemble, export
    from seiltanzer.edge_family_market import features_from_archive
    engine, value = inputs()
    value['trade_id'] = 7
    value['edge_family_sources'] = {'value_carry': {'kind': 'valuation',
        'source_id': 'actual-valuation', 'source_verified': True, 'instrument': 'NAS100',
        'observed_ts': T0-20, 'received_ts': T0-10, 'quality': 1.,
        'features': {'value.observed_factor': 1.25}}}
    module().attach_research_working_context(engine, value)
    packet = episode(captured=T0)
    packet['snapshot_json'] = canonical(value).decode()
    packet['snapshot_sha256'] = hashlib.sha256(packet['snapshot_json'].encode()).hexdigest()
    history = features_from_archive(assemble([export(packet, exported=T0+100)]))
    assert history['NAS100']['value_carry'][0]['features'] == {'value.observed_factor': 1.25}


def test_preliminary_substitute_does_not_discount_an_existing_real_family_source():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, value = inputs()
    value['edge_family_sources'] = {'value_carry': {'kind': 'valuation',
        'source_id': 'actual-valuation', 'source_verified': True, 'instrument': 'NAS100',
        'observed_ts': T0-20, 'received_ts': T0-10, 'quality': 1.,
        'features': {'value.observed_factor': 1.25}}}
    module().attach_research_working_context(engine, value)
    row = build_edge_family_evidence(value)['families']['value_carry']
    assert row['quality'] == 1.
    assert row['features'] == {'value.observed_factor': 1.25}


@pytest.mark.parametrize('field,bad', [('rules', 1), ('alternatives', {'order_book': []}),
    ('alternatives', {'order_book': {'available': True, 'broker_order_book_observed': True}})])
def test_hash_valid_malformed_optional_context_cannot_crash_or_claim_authority(field, bad):
    from seiltanzer.mathematical_edge import fingerprint
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from seiltanzer.unified_edge_audit import render_unified_ensemble_lines
    engine, value = inputs()
    module().attach_research_working_context(engine, value)
    context = value['research_working_context']
    context[field] = bad
    context['context_sha256'] = fingerprint({k:v for k,v in context.items() if k!='context_sha256'})
    assert module().research_working_component(value) is None
    audit = build_unified_ensemble(value)
    assert audit.get('research_working_context', {}).get('available') is not True
    assert render_unified_ensemble_lines(audit)
