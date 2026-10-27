"""Regressions for actual review contradictions; no provider or profit fixtures."""
from copy import deepcopy

import pytest


@pytest.mark.parametrize('family_opinion', [False, True])
def test_contradictory_hold_score_cannot_vote_for_opposing_actions(family_opinion):
    from test_unified_edge_ensemble import snapshot
    from test_edge_family_working import frozen_all, opinion
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from seiltanzer.llm_decision_shadow import VALID_POLICIES
    row = frozen_all() if family_opinion else snapshot()
    llm = dict(status='ok', policy='HOLD', confidence=.5, captured_ts=row['captured_ts'],
               policy_scores={name: -1. if name == 'HOLD' else .1 for name in VALID_POLICIES})
    if family_opinion:
        llm['family_assessments'] = {'value_carry': opinion('value_carry')}
    before = deepcopy(llm)
    audit = build_unified_ensemble(row, llm)
    current = next(c for c in audit['components'] if c['component_id'] == 'current_llm')
    assert current['effective_weight'] == 0., 'HOLD=-1 must not become an opposing positive vote'
    assert current['reason'] == 'CURRENT_LLM_POLICY_SCORE_CONTRADICTION'
    assert all(c['contribution'] == 0. for candidate in audit['candidates']
               for c in candidate['component_contributions'] if c['component_id'] == 'current_llm')
    assert llm == before


def test_frozen_inputs_received_after_cached_quote_remain_causal(tmp_path, monkeypatch):
    from test_family_reference_market import reference_runtime, T0, market
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, row = reference_runtime(tmp_path, monkeypatch)
    row['captured_ts'] = T0 + 100
    row['policy_manager']['input_audit'] = {'snapshot_ts': T0 - 1}
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time', lambda: T0 + 10)
    frozen = capture_market_inputs(engine)
    original = deepcopy(frozen.market.family_reference_series)
    market().attach_market_family_context(frozen, row)
    result = build_edge_family_evidence(row)['families']['session']
    assert result['forecast_available'] is True, 'cached quote time wrongly rejects already-frozen references'
    assert result['forecast_origin_ts'] == T0
    assert row['edge_family_market_context']['market_observation_ts'] == T0 + 10
    assert row['policy_manager']['input_audit']['snapshot_ts'] == T0 - 1
    assert frozen.market.family_reference_series == original


@pytest.mark.parametrize('case', ['future_receipt', 'wrong_hash', 'wrong_symbol', 'stale'])
def test_explicit_freeze_keeps_reference_rejection_gates(tmp_path, monkeypatch, case):
    from test_family_reference_market import reference_runtime, T0, market
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, row = reference_runtime(tmp_path, monkeypatch)
    reference = engine.market.family_reference_series
    if case == 'future_receipt': reference['received_ts'] = T0+11
    elif case == 'wrong_hash': reference['bars'][-1]['close'] += 1
    elif case == 'wrong_symbol': reference['ticker'] = 'OANDA:XAUUSD'
    frozen_ts = T0+901 if case == 'stale' else T0+10
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time', lambda: frozen_ts)
    frozen = capture_market_inputs(engine)
    row['captured_ts'] = T0+1000
    row['policy_manager']['input_audit'] = {'snapshot_ts': T0-1}
    market().attach_market_family_context(frozen, row)
    assert build_edge_family_evidence(row)['families']['session']['forecast_available'] is False


def test_higher_score_for_a_blocked_policy_does_not_discard_valid_llm_opinion():
    from test_unified_edge_ensemble import snapshot, llm
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    row = snapshot()
    opinion = llm(policy='CLOSE_10', policy_scores={'HOLD': 0., 'CLOSE_10': .5, 'EXIT': 1.})
    audit = build_unified_ensemble(row, opinion)
    current = next(c for c in audit['components'] if c['component_id'] == 'current_llm')
    assert current['effective_weight'] > 0.
    assert next(c for c in audit['candidates'] if c['policy'] == 'EXIT')['eligible'] is False


def test_equal_admissible_scores_still_allow_an_explicit_choice():
    from test_unified_edge_ensemble import snapshot, llm
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    audit = build_unified_ensemble(snapshot(), llm(policy='HOLD',
        policy_scores={'HOLD': 1., 'CLOSE_10': 1., 'CLOSE_25': 0.}))
    assert next(c for c in audit['components'] if c['component_id'] == 'current_llm')['effective_weight'] > 0.


def test_observed_capture_rounding_replays_real_path_without_rewriting_it():
    from test_decision_research import _snapshot
    from seiltanzer.decision_research import counterfactual_replay
    row = _snapshot()
    row['policy_manager']['inputs'].update(r0=.1507, max_r=.1507, rungs=[])
    actual = .1506666667
    points = [dict(ts=row['captured_ts'], r=actual),
              dict(ts=row['captured_ts']+900, r=-.379)]
    before = deepcopy(points)
    result = counterfactual_replay(row, points)
    # CLOSE_10: .1*observed_initial + .9*terminal - .01 cost.
    assert result['policies']['CLOSE_10']['net_realized_r'] == pytest.approx(-.33603333333, abs=1e-6)
    assert result['policies']['EXIT']['net_realized_r'] == pytest.approx(.1406666667, abs=1e-6)
    assert points == before


def test_local_feedback_resolves_a_real_rounded_initial_point(tmp_path):
    from test_g1m_local_feedback import _make_runtime, _insert_source
    from seiltanzer.g1_management_local_runtime import ManagementLocalRuntime
    from seiltanzer.g1_management_runtime import _json, _sha_text
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    captured = local.activation_ts+1
    review_id, row = _insert_source(engine, management, trade_id=61, captured_ts=captured)
    row['policy_manager']['inputs'].update(r0=.1507, max_r=.1507, rungs=[])
    raw = _json(row)
    conn = engine.passive._conn
    conn.execute('UPDATE decision_snapshots SET snapshot_json=?,snapshot_sha256=? WHERE review_id=?',
                 (raw, _sha_text(raw), review_id))
    local.materialize_windows()
    target = conn.execute('SELECT target_ts FROM g1m_local_windows WHERE trade_id=61 AND horizon_minutes=15').fetchone()[0]
    conn.executemany('INSERT INTO decision_path_points VALUES(?,?,?,?)',
        [(review_id,captured,101.506666667,.1506666667), (review_id,target,96.21,-.379)])
    conn.commit()
    assert local.resolve_due(now=target+1) == 1, local._last_error
    out = local.outcomes()['items'][0]
    assert out['production_local_r'] == pytest.approx(-.246583333325, abs=1e-6)
    assert out['hold_local_r'] == pytest.approx(-.379, abs=1e-6)
    conn.close()


def test_true_capture_mismatch_is_not_hidden_as_rounding():
    from test_decision_research import _snapshot
    from seiltanzer.decision_research import counterfactual_replay
    from seiltanzer.g1_management_runtime import ManagementEdgeRuntime
    row = _snapshot()
    row['policy_manager']['inputs']['r0'] = .15
    points = [dict(ts=row['captured_ts'], r=.16),
              dict(ts=row['captured_ts']+900, r=-.379)]
    for replay in (counterfactual_replay, ManagementEdgeRuntime._original_plan_outcome):
        with pytest.raises(ValueError, match='observed capture R differs'):
            replay(row, points)
