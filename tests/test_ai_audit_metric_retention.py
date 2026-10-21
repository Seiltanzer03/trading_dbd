from copy import deepcopy

import pytest

from seiltanzer import ai_snapshot_budget_guard as guard, ai_verdict
from seiltanzer.ai_verdict_v19 import _metric_audit_lines


@pytest.mark.parametrize('tier', ['strict', 'emergency'])
def test_compaction_keeps_available_metric_values_derivatives_and_quality(tier):
    state = {'available': True, 'version': 'option-test', 'authority': 'shadow_context',
             'independent_vote': False, 'source_quality': {'status': 'delayed', 'weight': .4},
             'metrics': {
                 'iv': {'available': True, 'value': .23, 'slope': None,
                        'sample_count': 1, 'source_quality': .4, 'confidence': .1,
                        'value_units': 'annualized_fraction'},
                 'p_take': {'available': False, 'value': None, 'slope': None},
             }, 'debug': 'x' * 80_000}
    snapshot = {'policy_manager': {'option_derivative_state': deepcopy(state)}}
    guard._strict_authoritative_compaction(snapshot)
    if tier == 'emergency':
        guard._emergency_authoritative_compaction(snapshot, ai_verdict)
    kept = snapshot['policy_manager'].get('option_derivative_state') or {}
    assert kept['metrics']['iv'] == state['metrics']['iv']
    assert kept['metrics']['p_take']['value'] is None
    assert kept['source_quality'] == state['source_quality']
    assert kept['authority'] == 'shadow_context' and kept['independent_vote'] is False
    assert 'debug' not in kept
    text = '\n'.join(_metric_audit_lines(snapshot))
    assert 'iv: current=0.23' in text and 'slope=UNAVAILABLE' in text
    assert 'p_take: current=UNAVAILABLE' in text
    assert ai_verdict._impl._snapshot_bytes(snapshot) < 60_000


def test_compaction_preserves_evidence_only_option_audit():
    snapshot = {'policy_manager': {'evidence': {'option_derivative_state': {
        'available': True, 'authority': 'shadow_context',
        'metrics': {'rv': {'value': .15, 'source_quality': .5}},
    }}}}
    guard._strict_authoritative_compaction(snapshot)
    assert 'rv: current=0.15' in '\n'.join(_metric_audit_lines(snapshot))
