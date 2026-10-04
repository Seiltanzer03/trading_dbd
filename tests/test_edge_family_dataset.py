"""Synthetic CI fixtures exercise admission; never publish them as source data."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_extended_policy_evaluation import _snapshot
from test_execution_cost_context import document, snapshot as identity_snapshot, SHA, T0
from seiltanzer.execution_cost_context import validate_execution_cost_context
from scripts.run_unified_edge_comparison import observed_replay


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def snapshot():
    value = _snapshot()
    value.update(identity_snapshot())
    value['policy_manager'] = _snapshot()['policy_manager']
    value['position_state'] = {'remaining_position_fraction': .8, 'realized_r_weighted': .2}
    value['trade_identity'].update(broker_position_id='ci-position-1', trade_id=1,
        instrument='NAS100', direction='long', position_evidence={
            'version': 'broker-position-context-v1', 'source_verified': True,
            'measurement_kind': 'executing_broker_position', 'source_id': 'ci-independent-position',
            'evidence_sha256': 'c' * 64, 'document_sha256': 'd' * 64, 'deployment_sha': SHA,
            'observed_ts': T0 - 10., 'received_ts': T0 - 5., 'max_age_sec': 60.,
            'entry': 100., 'original_stop': 90., 'remaining_position_fraction': .8})
    value['edge_family_sources'] = {'intermarket': [{
        'source_verified': True, 'source_id': 'ci-completed-index-bars',
        'instrument': 'NAS100', 'observed_ts': T0 - 10., 'received_ts': T0 - 5.,
        'linked_returns': [{'leader': 'SP500', 'start_ts': T0 - 610., 'end_ts': T0 - 10.,
                            'start_price': 100., 'end_price': 102.}]}]}
    doc = document()
    costs = validate_execution_cost_context(doc, snapshot=value,
        expected_deployment_sha=SHA, document_sha256=digest(doc))
    value['execution_cost_context_audit'] = costs
    value['policy_manager']['execution_cost_model'] = deepcopy(costs)
    value['broker_rollover_schedule'] = {'source_verified': True, 'source_id': 'ci-broker-carry',
        'broker_id': 'executing-broker', 'account_id': 'account-1', 'trade_id': 1,
        'direction': 'long', 'quantity_units': 2., 'quantity_basis': 'current_remaining_position',
        'instrument': 'NAS100', 'observed_ts': T0 - 10., 'received_ts': T0 - 5.,
        'quality': 1., 'max_age_sec': 60., 'currency': 'USD', 'risk_currency_per_unit': 100.,
        'charge_basis': 'per_unit_of_remaining_position', 'coverage_start_epoch': T0,
        'coverage_end_epoch': T0 + 14400., 'same_timestamp_rule': 'fills_before_rollover',
        'included_in_base_costs': False, 'events': []}
    value['active_management_candidates'] = [{'policy': 'TIGHTEN_STOP',
        'parameters': {'stop_price': 105.}, 'status': 'eligible',
        'expected_variant_net_r': .7, 'worst_seed_cvar10_net_r': .4,
        'expected_delta_vs_hold_r': .4}]
    value['policy_manager']['policies'] = {name: {'expected_final_r_net': .5,
        'cvar10_r_net': -.5, 'outcomes_include_execution_costs': True}
        for name in ('HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT')}
    value['policy_manager']['risk_constraint']['net_cvar_floor_r'] = -1.
    value['policy_manager']['selection_rule']['eligible'] = list(value['policy_manager']['policies'])
    return value


def record(value=None, *, path=None):
    value = snapshot() if value is None else value
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return {'review_id': 'ci-review', 'trade_id': 1, 'captured_ts': T0,
        'instrument': 'NAS100', 'snapshot_json': raw,
        'snapshot_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'production_policy': 'HOLD', 'path_truncated': False,
        'path_query_horizon_end_ts': T0 + 14400.,
        'path_points': path if path is not None else [
            {'ts': T0, 'r': 1., 'price': 110.},
            {'ts': T0 + 300., 'r': .4, 'price': 104.},
            {'ts': T0 + 14400., 'r': .2, 'price': 102.}],
        'stored_replay': None}


def archive(value=None):
    episodes = [record() if value is None else value]
    return {'contract_version': 'edge-family-archive-v1', 'episodes': episodes,
            'dataset_sha256': digest(episodes)}


def build(value):
    from seiltanzer.edge_family_dataset import build_family_dataset
    return build_family_dataset(value)


def geometry(value):
    from seiltanzer.edge_family_dataset import family_geometry_sha256
    return family_geometry_sha256(value)


def test_real_replay_delta_and_causal_provenance_are_separate_immutable_and_deterministic():
    source = archive()
    original = deepcopy(source)
    result = build(source)
    assert source == original
    assert result == build(source)
    assert result['version'] == 'edge-family-dataset-v1'
    assert result['rows']
    assert result['dataset_sha256'] == digest(result['rows'])
    frozen = json.loads(source['episodes'][0]['snapshot_json'])
    hold = observed_replay(frozen, source['episodes'][0], {'policy': 'HOLD', 'parameters': {}})
    for row in result['rows']:
        replay = observed_replay(frozen, source['episodes'][0], row['candidate'])
        assert row['delta_net_r'] == pytest.approx(replay['net_r_on_remaining'] - hold['net_r_on_remaining'])
        assert row['label_end_ts'] == T0 + 14400.
        assert row['costs_verified'] is True
        assert row['cost_provenance']['trade_id'] == 1
        assert row['cost_provenance']['components']['immediate']['total_r'] == .06
        assert row['feature_windows_sec']['intermarket.SP500.return'] == 600.
        assert all(meta['received_ts'] <= T0 for meta in row['feature_provenance'].values())
        assert row['label_kind'] == 'OBSERVED_PATH_COUNTERFACTUAL_NOT_BROKER_FILL'
    assert any(row['candidate']['policy'] == 'TIGHTEN_STOP' for row in result['rows'])


def test_real_path_deltas_have_hand_derived_net_values_and_observed_rollover():
    value = snapshot()
    value['broker_rollover_schedule']['events'] = [
        {'scheduled_epoch': T0 + 300., 'charge_currency_per_unit': 2.}]
    rows = build(archive(record(value)))['rows']
    deltas = {row['candidate']['policy']: row['delta_net_r'] for row in rows}
    # HOLD gross=.2 minus .06 execution minus .02 carry = .12.
    # Tightened .5 stop resolves at 250sec, before the verified carry event.
    assert deltas == pytest.approx({'HOLD': 0., 'EXIT': .82, 'CLOSE_10': .082,
                                   'CLOSE_25': .205, 'CLOSE_50': .41, 'TIGHTEN_STOP': .32})


@pytest.mark.parametrize('change', ['broker', 'account', 'trade', 'direction', 'quantity', 'basis'])
def test_rollover_requires_executing_trade_and_remaining_units_binding(change):
    value = snapshot()
    schedule = value['broker_rollover_schedule']
    key = {'broker': 'broker_id', 'account': 'account_id', 'trade': 'trade_id',
           'direction': 'direction', 'quantity': 'quantity_units', 'basis': 'quantity_basis'}[change]
    schedule[key] = 'other'
    assert build(archive(record(value)))['rows'] == []


def test_path_prices_must_agree_with_frozen_r_units():
    retained = record()
    retained['path_points'][1]['price'] = 105.
    assert build(archive(retained))['rows'] == []


@pytest.mark.parametrize('change', ['received', 'published', 'revision', 'instrument', 'unverified'])
def test_future_or_wrong_identity_sources_do_not_become_features(change):
    value = snapshot()
    source = value['edge_family_sources']['intermarket'][0]
    if change == 'received': source['received_ts'] = T0 + .1
    elif change == 'published': source['published_at'] = T0 + .1
    elif change == 'revision': source['revision_received_ts'] = T0 + .1
    elif change == 'instrument': source['instrument'] = 'XAU'
    else: source['source_verified'] = False
    assert build(archive(record(value)))['rows'] == []


def test_context_only_option_features_and_models_cannot_rescue_missing_observations():
    value = snapshot()
    value.pop('edge_family_sources')
    value['policy_manager']['evidence'] = {'iv_surface': {'available': True,
        'snapshot_age_sec': 10., 'real_expiries': [{'days': 1., 'atm_iv_pct': 20.}]},
        'data_quality': {'chain': {'source': 'ci-options'}}}
    value['edge_family_models'] = {'intermarket': {'features': {'forged': 1.}}}
    assert build(archive(record(value)))['rows'] == []


@pytest.mark.parametrize('change', ['assumed', 'missing_audit', 'boolean_only', 'units',
    'account', 'horizon', 'component', 'total', 'model', 'source', 'document_hash',
    'cost_received', 'rollover', 'rollover_units'])
def test_cost_claims_require_matching_verified_identity_units_components_and_frozen_model(change):
    value = snapshot()
    audit = value['execution_cost_context_audit']
    if change == 'assumed': value['policy_manager']['execution_cost_model']['assumed'] = True
    elif change == 'missing_audit': value.pop('execution_cost_context_audit')
    elif change == 'boolean_only': value['execution_cost_context_audit'] = {'available': True, 'complete_costs_available': True}
    elif change == 'units': value['position_execution_units']['quantity_units'] = 3.
    elif change == 'account': value['trade_identity']['account_id'] = 'other'
    elif change == 'horizon': audit['horizon_minutes'] = 60.
    elif change == 'component': audit['components']['immediate']['components_r'].pop('manual_latency')
    elif change == 'total': audit['components']['deferred']['total_r'] = .01
    elif change == 'model': value['policy_manager']['execution_cost_model']['immediate_full_close_r'] = .01
    elif change == 'source': audit['source'] = 'other'
    elif change == 'document_hash': audit['document_sha256'] = 'wrong'
    elif change == 'cost_received': audit['received_ts'] = T0 + .1
    elif change == 'rollover': value.pop('broker_rollover_schedule')
    elif change == 'rollover_units': value['broker_rollover_schedule']['risk_currency_per_unit'] = 10.
    result = build(archive(record(value)))
    assert result['rows'] == []
    assert result['exclusions']


def test_missing_hold_continuation_excludes_even_immediate_exit_and_stored_replay_claim():
    value = record(path=[{'ts': T0, 'r': 1.}, {'ts': T0 + 300., 'r': .9}])
    value['stored_replay'] = {'resolved_ts': T0 + 301., 'resolution_kind': 'manual_close', 'replay_json': '{}'}
    assert build(archive(value))['rows'] == []
    value['path_truncated'] = True
    assert build(archive(value))['rows'] == []


def test_archive_hash_snapshot_hash_and_wrong_horizon_are_not_trusted():
    value = archive()
    value['dataset_sha256'] = 'b' * 64
    assert build(value)['rows'] == []
    value = record()
    value['snapshot_sha256'] = 'b' * 64
    assert build(archive(value))['rows'] == []
    value = record()
    value['path_query_horizon_end_ts'] = T0 + 300.
    assert build(archive(value))['rows'] == []


def test_partial_real_path_can_label_when_hold_and_candidate_resolve_within_prefix():
    value = record(path=[{'ts': T0, 'r': 1.}, {'ts': T0 + 300., 'r': -1.1}])
    value['path_truncated'] = True
    result = build(archive(value))
    assert result['rows']
    assert all(row['label_end_ts'] == T0 + 300. for row in result['rows'])


def test_geometry_uses_exact_policy_exposure_action_matrix_and_relative_time_deadline():
    value = snapshot()
    value['active_management_candidates'].append({'policy': 'TIME_STOP', 'parameters': {'deadline_ts': T0 + 600.}})
    original = geometry(value)
    shifted = deepcopy(value)
    shifted['captured_ts'] += 100.
    shifted['active_management_candidates'][-1]['parameters']['deadline_ts'] += 100.
    assert geometry(shifted) == original
    for key in ('stop_r', 'T', 'r0', 'max_r', 'be_after', 'rung_fraction'):
        changed = deepcopy(value)
        changed['policy_manager']['inputs'][key] += .01
        price = {'stop_r': 'active_risk_barrier', 'T': 'final_take', 'r0': 'current'}.get(key)
        if price: changed['trade_geometry'][price] += .1
        if key == 'r0': changed['policy_manager']['inputs']['max_r'] += .01
        assert geometry(changed) != original
    for path, changed in [(('position_state', 'remaining_position_fraction'), .7),
                           (('trade_geometry', 'entry'), 100.01)]:
        modified = deepcopy(value); modified[path[0]][path[1]] = changed
        if path[1] == 'entry':
            for price, r_key in [('current', 'r0'), ('active_risk_barrier', 'stop_r'), ('final_take', 'T')]:
                modified['policy_manager']['inputs'][r_key] = (modified['trade_geometry'][price] - changed) / (changed - 90.)
        assert geometry(modified) != original
    changed = deepcopy(value)
    changed['active_management_candidates'][0]['parameters']['stop_price'] += .01
    assert geometry(changed) != original
    changed = deepcopy(value)
    changed['active_management_candidates'][-1]['parameters']['deadline_ts'] += 1.
    assert geometry(changed) != original


@pytest.mark.parametrize('where', ['trade_geometry', 'inputs', 'parameters', 'missing_stop', 'missing_exposure'])
def test_unknown_or_incomplete_geometry_excludes_training(where):
    value = snapshot()
    if where == 'trade_geometry': value['trade_geometry']['new_execution_rule'] = 1.
    elif where == 'inputs': value['policy_manager']['inputs']['new_execution_rule'] = 1.
    elif where == 'parameters': value['active_management_candidates'][0]['parameters']['new_execution_rule'] = 1.
    elif where == 'missing_stop': value['policy_manager']['inputs'].pop('stop_r')
    else: value['position_state'].pop('remaining_position_fraction')
    assert build(archive(record(value)))['rows'] == []


def test_geometry_import_does_not_load_offhost_replay_or_numpy():
    import subprocess
    import sys
    code = "import sys, importlib.util; spec=importlib.util.spec_from_file_location('geometry_test', 'seiltanzer/edge_family_dataset.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); assert 'scripts.run_unified_edge_comparison' not in sys.modules; assert 'numpy' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_actual_archive_contract_produces_causal_rows():
    from seiltanzer.edge_family_archive import assemble_archive
    value = assemble_archive([{'read_only': True, 'exported_ts': T0 + 15000., 'reviews': [record()]}])
    assert value['episodes']
    assert build(value)['rows']


@pytest.mark.parametrize('change', ['context_only', 'wrong_source_horizon', 'synthetic_demo', 'is_demo', 'direction', 'path_instrument', 'path_horizon', 'conflict'])
def test_explicit_non_training_claims_and_identity_mismatches_fail_closed(change):
    value = snapshot()
    source = value['edge_family_sources']['intermarket'][0]
    if change == 'context_only': source['context_only'] = True
    elif change == 'wrong_source_horizon': source['horizon_minutes'] = 60.
    elif change in ('synthetic_demo', 'is_demo'): value[change] = True
    elif change == 'direction': value['strategy']['direction'] = 'unknown'
    retained = record(value)
    if change == 'path_instrument': retained['path_points'][1]['instrument'] = 'XAU'
    elif change == 'path_horizon': retained['horizon_minutes'] = 60.
    result = archive(retained)
    if change == 'conflict':
        other = deepcopy(retained); other['path_points'][1]['r'] += .1
        result['episodes'].append(other); result['dataset_sha256'] = digest(result['episodes'])
    assert build(result)['rows'] == []


def test_interpolated_horizon_label_waits_for_actual_right_bracketing_observation():
    value = record()
    value['path_points'][-1]['ts'] += 60.
    result = build(archive(value))
    assert result['rows']
    assert all(row['label_end_ts'] == T0 + 14460. for row in result['rows'])


def test_time_stop_absolute_candidate_ids_remain_explicitly_unavailable():
    value = snapshot()
    value['active_management_candidates'].append({'policy': 'TIME_STOP', 'parameters': {'deadline_ts': T0 + 600.}})
    result = build(archive(record(value)))
    assert result['rows']
    assert not any(row['candidate']['policy'] == 'TIME_STOP' for row in result['rows'])
    assert any(item['reason'] == 'TIME_STOP_RUNTIME_ACTION_ID_NOT_TIME_INVARIANT' for item in result['exclusions'])


@pytest.mark.parametrize('change', ['stop', 'take', 'current', 'short', 'trade_id', 'terminal', 'armed',
                                    'position_stop', 'position_take', 'original_stop', 'realized_fraction'])
def test_inconsistent_frozen_execution_geometry_is_not_a_label_cohort(change):
    value = snapshot()
    if change == 'stop': value['trade_geometry']['active_risk_barrier'] = 95.
    elif change == 'take': value['trade_geometry']['final_take'] = 125.
    elif change == 'current': value['trade_geometry']['current'] = 109.
    elif change == 'short': value['strategy']['direction'] = 'short'
    elif change == 'trade_id': value['position_state']['trade_id'] = 2
    elif change == 'terminal': value['position_state']['strategy_terminal_event'] = {'kind': 'take'}
    elif change == 'armed': value['position_state']['armed_conditional_actions'] = [{'policy': 'TIME_STOP'}]
    elif change == 'position_stop': value['position_state']['active_stop_price'] = 95.
    elif change == 'position_take': value['position_state']['take'] = 125.
    elif change == 'original_stop': value['position_state']['original_stop'] = 89.
    elif change == 'realized_fraction': value['position_state']['realized_position_fraction'] = .3
    assert build(archive(record(value)))['rows'] == []


@pytest.mark.parametrize('change', ['manager', 'policy', 'candidate', 'source', 'path', 'gate', 'rule', 'overlay'])
def test_malformed_nested_inputs_fail_closed_without_crashing(change):
    value = snapshot()
    if change == 'manager': value['policy_manager'] = ['invalid']
    elif change == 'policy': value['policy_manager']['policies']['HOLD'] = ['invalid']
    elif change == 'candidate': value['active_management_candidates'] = ['invalid']
    elif change == 'source': value['edge_family_sources'] = {'intermarket': ['invalid']}
    elif change == 'gate': value['policy_manager']['gate'] = ['invalid']
    elif change == 'rule': value['policy_manager']['selection_rule'] = ['invalid']
    elif change == 'overlay': value['policy_manager']['gate'] = {'degraded_authority_overlay': ['invalid']}
    retained = record(value)
    if change == 'path': retained['path_points'][1] = ['invalid']
    assert build(archive(retained))['rows'] == []


@pytest.mark.parametrize('change', ['missing', 'clock', 'source', 'hash', 'measurement', 'entry', 'fraction', 'trade', 'position'])
def test_independent_position_proof_is_required_not_cost_derived_identity(change):
    value = snapshot()
    identity = value['trade_identity']
    proof = identity['position_evidence']
    if change == 'missing': identity.pop('position_evidence')
    elif change == 'clock': proof['received_ts'] = T0 + 1.
    elif change == 'source': proof['source_verified'] = False
    elif change == 'hash': proof['document_sha256'] = 'invalid'
    elif change == 'measurement': proof['measurement_kind'] = 'public_quote'
    elif change == 'entry': proof['entry'] = 99.
    elif change == 'fraction': proof['remaining_position_fraction'] = .7
    elif change == 'trade': identity['trade_id'] = 2
    else: identity.pop('broker_position_id')
    assert build(archive(record(value)))['rows'] == []


@pytest.mark.parametrize('change', ['cost_document', 'cost_component', 'family_source', 'position'])
def test_explicit_synthetic_evidence_never_becomes_real_training_rows(change):
    value = snapshot()
    if change.startswith('cost_'):
        doc = document()
        if change == 'cost_document': doc['synthetic'] = True
        else: doc['immediate']['spread']['synthetic'] = True
        costs = validate_execution_cost_context(doc, snapshot=value,
            expected_deployment_sha=SHA, document_sha256=digest(doc))
        value['execution_cost_context_audit'] = costs
        value['policy_manager']['execution_cost_model'] = deepcopy(costs)
    elif change == 'family_source': value['edge_family_sources']['intermarket'][0]['synthetic'] = True
    else: value['trade_identity']['position_evidence']['synthetic'] = True
    assert build(archive(record(value)))['rows'] == []


@pytest.mark.parametrize('change', ['binding', 'coverage', 'evidence', 'measurement', 'component_clock', 'currency_total', 'historical_missing'])
def test_normalized_cost_audit_must_retain_full_loader_evidence(change):
    value = snapshot()
    audit = value['execution_cost_context_audit']
    if change == 'binding': audit['trade_id'] = 2
    elif change == 'coverage': audit['coverage_end_epoch'] = T0 + 1.
    elif change == 'historical_missing': audit['components']['immediate'].pop('component_provenance', None)
    else:
        proof = audit['components']['immediate'].setdefault('component_provenance', deepcopy(document()['immediate']))['spread']
        if change == 'evidence': proof['evidence_sha256'] = 'not-sha'
        elif change == 'measurement': proof['measurement_kind'] = 'public_quote'
        elif change == 'component_clock': proof['observed_ts'] = T0 - 1000.
        else: proof['cost_currency_per_unit'] = 100.
    value['policy_manager']['execution_cost_model'] = deepcopy(audit)
    assert build(archive(record(value)))['rows'] == []


@pytest.mark.parametrize('marker', ['synthetic', 'demo', 'is_demo', 'synthetic_demo', 'contract_fixture'])
def test_archive_origin_markers_exclude_all_training_rows(marker):
    value = archive()
    value[marker] = True
    result = build(value)
    assert result['rows'] == []
    assert result['exclusions'][0]['reason'] == 'SYNTHETIC_ARCHIVE_EXCLUDED'


def event_source():
    return {'source_verified': True, 'source_id': 'ci-release', 'instrument': 'NAS100',
        'observed_ts': T0 - 10., 'received_ts': T0 - 5., 'published_at': T0 - 10.,
        'release_id': 'ci-release', 'period': '2026-09', 'unit': 'percent', 'actual': 3.,
        'consensus': {'source_verified': True, 'source_id': 'ci-consensus',
            'instrument': 'NAS100', 'observed_ts': T0 - 30., 'received_ts': T0 - 20.,
            'release_id': 'ci-release', 'period': '2026-09', 'unit': 'percent', 'value': 2.}}


@pytest.mark.parametrize('declaration', [{'synthetic': True}, {'context_only': True}, {'horizon_minutes': 60.}])
def test_supporting_consensus_declarations_cannot_be_erased_by_normalization(declaration):
    value = snapshot()
    source = event_source()
    value['edge_family_sources'] = {'event': [source]}
    assert any(row['family_id'] == 'event' for row in build(archive(record(value)))['rows'])
    source['consensus'].update(declaration)
    result = build(archive(record(value)))
    assert result['rows'] == []
    assert result['exclusions']


def official_macro_source(kind):
    release = {'status': 'VALID', 'release_id': 'ci-release', 'official_source_verified': True,
               'published_at': T0 - 10., 'available_at': T0 - 5.}
    if kind == 'release':
        return {'numeric_macro': {'releases': {'CPI': release},
                                 'candidate_vector': {'macro.cpi_actual': 3.}}}, release
    release.update(available=True)
    release['semantic' if kind == 'fomc' else 'payload'] = {'rate': 3.}
    return {kind: release}, release


@pytest.mark.parametrize('root', ['macro_context_v1', 'macro_t0_context'])
@pytest.mark.parametrize('kind', ['release', 'fomc', 'fomc_deterministic'])
@pytest.mark.parametrize('declaration', [{'synthetic': True}, {'context_only': True}, {'horizon_minutes': 60.}])
def test_official_macro_original_declarations_are_enforced(root, kind, declaration):
    value = snapshot()
    value['edge_family_sources'] = {}
    value[root], source = official_macro_source(kind)
    assert any(row['family_id'] == 'macro' for row in build(archive(record(value)))['rows'])
    source.update(declaration)
    assert build(archive(record(value)))['rows'] == []


@pytest.mark.parametrize('boundary', ['root', 'numeric_macro', 'candidate_vector', 'releases'])
@pytest.mark.parametrize('declaration', [{'synthetic': True}, {'context_only': True}, {'horizon_minutes': 60.}])
def test_official_macro_parent_declarations_are_enforced(boundary, declaration):
    value = snapshot()
    value['edge_family_sources'] = {}
    root, _ = official_macro_source('release')
    value['macro_context_v1'] = root
    target = root if boundary == 'root' else root['numeric_macro']
    if boundary in {'candidate_vector', 'releases'}:
        target = target[boundary]
    target.update(declaration)
    assert build(archive(record(value)))['rows'] == []


def test_rejected_official_release_does_not_contaminate_independent_valid_family_or_release():
    value = snapshot()
    root, release = official_macro_source('release')
    root['numeric_macro']['releases']['BAD'] = {**release, 'synthetic': True}
    root['numeric_macro']['candidate_vector']['macro.bad_actual'] = 9.
    value['macro_context_v1'] = root
    value['edge_family_models'] = {'synthetic': True, 'future': {'received_ts': T0 + 100.}}
    result = build(archive(record(value)))
    assert {row['family_id'] for row in result['rows']} == {'macro', 'intermarket'}
    macro_rows = [row for row in result['rows'] if row['family_id'] == 'macro']
    assert all(row['features'] == {'macro.cpi_actual': 3.} for row in macro_rows)
    assert any(item.get('source_path', '').endswith('BAD') for item in result['exclusions'])


@pytest.mark.parametrize('level', ['input_audit', 'rows', 'instrument_price'])
def test_malformed_candidate_input_audit_is_isolated_to_its_episode(level):
    value = snapshot()
    value['policy_manager']['input_audit'] = {'rows': {'instrument_price': {}}}
    if level == 'input_audit': value['policy_manager']['input_audit'] = ['bad']
    elif level == 'rows': value['policy_manager']['input_audit']['rows'] = ['bad']
    else: value['policy_manager']['input_audit']['rows']['instrument_price'] = ['bad']
    malformed = record(value)
    malformed['review_id'] = 'bad-review'
    episodes = [malformed, record()]
    result = build({'contract_version': 'edge-family-archive-v1', 'episodes': episodes,
                    'dataset_sha256': digest(episodes)})
    assert result['rows'] == build(archive())['rows']
    assert any(item['review_id'] == 'bad-review' and
               item['reason'] == 'FROZEN_POLICY_GEOMETRY_INVALID' for item in result['exclusions'])
