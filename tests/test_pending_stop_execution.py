from copy import deepcopy

import pytest

from seiltanzer.position_state import PositionLedger
from test_llm_shadow_action_execution import _trade, _shadow, _register


def test_identical_stop_reuses_pending_command_across_reviews_and_restart(tmp_path):
    path = str(tmp_path / 'pending.db')
    trade = _trade()
    ledger = PositionLedger(path)
    first = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105., 'anchor': 'STRUCTURE'}))
    ledger.close()
    ledger = PositionLedger(path)
    again = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105., 'anchor': 'PREVIOUS_UNCONFIRMED_STOP_REFERENCE'}), captured=2300., review='review-2')
    assert again['action_id'] == first['action_id']
    assert again['first_issued_at'] == 2000.
    assert again['last_validated_review_id'] == 'review-2'
    assert len(ledger.shadow_actions(trade['id'])) == 1
    out = ledger.acknowledge_shadow_action(action_id=first['action_id'], trade=trade,
        executed=True, execution_price=110., execution_r=1.)
    assert out['position_state']['active_stop_price'] == 105.
    assert ledger.acknowledge_shadow_action(action_id=first['action_id'], trade=trade,
        executed=True, execution_price=110., execution_r=1.)['idempotent'] is True
    ledger.close()


def test_failed_publication_does_not_cancel_reused_published_stop(tmp_path):
    ledger = PositionLedger(str(tmp_path / 'rollback.db')); trade = _trade()
    first = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}))
    with pytest.raises(RuntimeError, match='journal failed'):
        with ledger.decision_publication(trade['id'], 'review-2'):
            again = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}), captured=2300., review='review-2')
            assert again['action_id'] == first['action_id']
            raise RuntimeError('journal failed')
    assert ledger.shadow_actions(trade['id'])[0]['status'] == 'pending_execution'
    ledger.close()


def test_third_identical_review_keeps_command_continuity_despite_newer_base_holds(tmp_path):
    from seiltanzer.app import _extended_manual_decision
    from test_decision_publication import _snapshot
    ledger = PositionLedger(str(tmp_path / 'three-reviews.db')); trade = _trade()
    decisions = []
    for n, captured in enumerate((2000., 2100., 2200.)):
        snapshot = _snapshot(ledger, trade, 'HOLD', captured)
        base = ledger.register_decision(snapshot, f'review-{n}', trade)
        action = ledger.register_shadow_action(snapshot, f'review-{n}', trade,
            _shadow('TIGHTEN_STOP', {'stop_price': 105.}))
        decisions.append(_extended_manual_decision(base, action))
        ledger.supersede_other_pending_actions(trade['id'], action['action_id'])
    assert len({d['decision_id'] for d in decisions}) == 1
    last = decisions[-1]
    assert last['continuity'] == 'continue_same_pending_decision'
    assert last['previous']['decision_id'] == decisions[0]['decision_id']
    assert last['previous']['policy'] == 'TIGHTEN_STOP'
    assert last['previous']['issued_at'] == 2000.
    assert last['last_validated_review_id'] == 'review-2'
    ledger.close()


@pytest.mark.parametrize('change', ['stop', 'geometry', 'declined'])
def test_changed_or_declined_stop_is_not_reused(tmp_path, change):
    ledger = PositionLedger(str(tmp_path / 'changed.db')); trade = _trade()
    first = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}))
    if change == 'geometry':
        trade['take'] = 135.
    if change == 'declined':
        ledger.acknowledge_shadow_action(action_id=first['action_id'], trade=trade,
            executed=False, execution_price=None, execution_r=None)
    again = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 106. if change == 'stop' else 105.}), captured=2300., review='review-2')
    assert again['action_id'] != first['action_id']
    assert len(ledger.shadow_actions(trade['id'])) == 2
    ledger.close()


def test_hold_report_still_warns_about_unconfirmed_stop():
    from seiltanzer.ai_verdict_v19 import _unified_command_sections
    context = {'recorded_stop_price': 3976., 'proposed_stop_price': 4040.41,
        'distance_price': 3.325, 'distance_r': .110833333, 'pending_age_sec': 600.,
        'broker_execution_confirmed': False, 'proposed_stop_crossed': False}
    report = '\n'.join(_unified_command_sections([], {'policy_manager': {
        'management_decision': {'policy': 'HOLD', 'execution_status': 'not_required',
            'stop_execution_context': context}}}))
    assert '3976' in report and '4040.41' in report
    assert '3.325' in report and '0.111R' in report
    assert '600' in report
    assert 'исполнение не подтверждено' in report


def test_expected_budget_exclusion_exposes_tail_tradeoff_without_reclassifying_admission():
    from seiltanzer.unified_edge_ensemble import rank_candidates, SCHEMES
    from test_unified_decision_utility import rows, opinions, context
    candidates = rows(.290, 2.147, 'TIGHTEN_STOP') + [rows(.242, 2.258, 'EXIT')[1]]
    for row in candidates:
        row.update(bank_id='same', economics_basis='common_frozen_weighted_option_driver_paths')
    _, ranked, _ = rank_candidates(candidates, opinions(), context(), SCHEMES['quant100'])
    exit = ranked[-1]
    assert exit['ranking_reason'] == 'EXPECTED_SACRIFICE_EXCEEDS_BUDGET'
    check = exit['expected_sacrifice_check']
    assert check['candidate_tail_gain_vs_reference_r'] == pytest.approx(.111)
    assert check['economic_relation'] == 'EXPECTED_TAIL_TRADEOFF'
    assert check['threshold_basis'] == 'CONFIGURED_POLICY_NOT_HISTORICAL_CALIBRATION'


def test_identical_legacy_unresolved_stop_keeps_earliest_issuance(tmp_path):
    ledger = PositionLedger(str(tmp_path / 'legacy.db')); trade = _trade()
    _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}))
    ledger.supersede_other_pending_actions(trade['id'], 'intervening-hold')
    _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}), captured=2300., review='two')
    context = ledger.stop_execution_context(trade, current_price=108., captured_ts=2600.)
    assert context['pending_age_sec'] == 600.
    ledger.close()


def test_confirmed_protection_clears_warning_even_with_older_superseded_reference(tmp_path):
    ledger = PositionLedger(str(tmp_path / 'confirmed.db')); trade = _trade()
    _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 105.}))
    ledger.supersede_other_pending_actions(trade['id'], 'intervening-hold')
    current = _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 106.}), captured=2300., review='two')
    ledger.acknowledge_shadow_action(action_id=current['action_id'], trade=trade,
        executed=True, execution_price=110., execution_r=1.)
    assert ledger.state(trade)['active_stop_price'] == 106.
    assert ledger.stop_execution_context(trade, current_price=110., captured_ts=2600.) is None
    ledger.close()


@pytest.mark.parametrize('short,current,distance,crossed', [
    (False, 108., 3., False), (False, 104., -1., True),
    (True, 92., 3., False), (True, 96., -1., True),
    (False, None, None, None),
])
def test_unconfirmed_stop_context_keeps_recorded_stop_and_original_age(tmp_path, short, current, distance, crossed):
    ledger = PositionLedger(str(tmp_path / 'context.db')); trade = _trade()
    if short:
        trade.update(direction='short', stop=110., take=70.)
    _register(ledger, trade, _shadow('TIGHTEN_STOP', {'stop_price': 95. if short else 105.}))
    before = deepcopy(ledger.state(trade))
    context = ledger.stop_execution_context(trade, current_price=current, captured_ts=2600.)
    assert context['recorded_stop_price'] == (110. if short else 90.)
    assert context['proposed_stop_price'] == (95. if short else 105.)
    assert context['broker_execution_confirmed'] is False
    assert context['pending_age_sec'] == 600.
    assert context['distance_price'] == distance
    assert context['distance_r'] == (None if distance is None else distance / 10.)
    assert context['proposed_stop_crossed'] is crossed
    assert ledger.state(trade) == before
    ledger.close()
