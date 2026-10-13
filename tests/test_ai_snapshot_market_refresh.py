from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_unified_candidate_economics import snapshot as priced_snapshot
from seiltanzer.position_state import StaleDecisionError


def fixture(monkeypatch, price=111.):
    from seiltanzer import ai_snapshot_market_refresh as refresh
    s = priced_snapshot([[1., 1., 1.]], [1.])
    s['policy_manager'].pop('unified_scenario_bank')
    s['captured_ts'] = 1000.
    s['trade_geometry'].update(entry=100., original_stop=90., active_risk_barrier=90., final_take=130., current=110., current_r=1.)
    s.update(trade_id=1, materialization={'deterministic_snapshot': True},
             strategy={'instrument': 'NAS100', 'direction': 'long'},
             position_state={'remaining_position_fraction': 1.})
    s['policy_manager']['management_decision'] = {'automatic_execution_allowed': False}
    row = dict(id=1, instrument='NAS100', direction='long', entry=100., stop=90., take=130.)
    tick = dict(ts=1100., instrument='NAS100', trade=row,
                feeds={'price': {'value': price, 'status': 'live', 'source': 'TradingView stream OANDA:NAS100USD', 'ts': 1099.}},
                prob={'available': True, 'r': (price-100.)/10., 'T': 3.}, cone={'sigma_R': .2},
                market={'available': True, 'horizon_years': 60./525600.}, ladder={'max_r': 1.})
    engine = SimpleNamespace(canonical_tick_payload=lambda: deepcopy(tick))
    monkeypatch.setattr(refresh.time, 'time', lambda: 1100.)
    return engine, s, tick


def test_fast_refresh_reprices_exit_at_actual_new_quote_and_keeps_provider_clock(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine, s, _ = fixture(monkeypatch)
    original = deepcopy(s)
    out = refresh_frozen_comparison(engine, s)
    assert out['trade_geometry']['current'] == 111.
    assert out['policy_manager']['inputs']['r0'] == pytest.approx(1.1)
    assert out['policy_manager']['policies']['EXIT']['expected_final_r_net'] == pytest.approx(1.06)
    assert out['policy_manager']['input_audit']['rows']['instrument_price']['timestamp'] == 1099.
    assert out['market_refresh']['heavy_policy_recomputed'] is False
    assert s == original


@pytest.mark.parametrize('bad', [89., 131.])
def test_live_barrier_crossing_never_returns_repriced_action(monkeypatch, bad):
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine, s, _ = fixture(monkeypatch, bad)
    with pytest.raises(StaleDecisionError, match='barrier'):
        refresh_frozen_comparison(engine, s)


def test_publication_guard_rejects_price_move_after_refresh(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import assert_live_review_compatible
    engine, s, tick = fixture(monkeypatch, 111.)
    tick['feeds']['price']['value'] = 89.
    with pytest.raises(StaleDecisionError):
        assert_live_review_compatible(engine, s)


def test_explicit_empirical_bank_is_never_shifted_to_fresh_price(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine, s, _ = fixture(monkeypatch)
    s['policy_manager']['unified_scenario_bank'] = {'distribution_kind': 'empirical'}
    with pytest.raises(StaleDecisionError):
        refresh_frozen_comparison(engine, s)


@pytest.mark.parametrize('quote_changes', [{'ts': None}, {'ts': 1069.},
    {'production_authority': False}, {'production_authority': True, 'source': 'other vendor'},
    {'derived': True}, {'value': 100., 'ts': 1110.}])
def test_refresh_preserves_native_source_and_provider_time_requirements(monkeypatch, quote_changes):
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine, s, tick = fixture(monkeypatch)
    tick['feeds']['price'].update(quote_changes)
    with pytest.raises(StaleDecisionError):
        refresh_frozen_comparison(engine, s)


def test_refresh_does_not_erase_explicit_false_be_confirmation(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine, s, tick = fixture(monkeypatch)
    s['position_state']['be_execution_confirmed'] = False
    tick['trade']['position_state'] = {'be_execution_confirmed': False}
    out = refresh_frozen_comparison(engine, s)
    assert out['policy_manager']['inputs']['be_execution_confirmed'] is False
    assert out['policy_manager']['inputs']['stop_r'] == -1.


def test_publication_rechecks_selected_stop_at_latest_quote(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import assert_live_review_compatible
    engine, s, _ = fixture(monkeypatch, 109.95)
    s['trade_geometry']['current_r'] = 1.
    with pytest.raises(StaleDecisionError):
        assert_live_review_compatible(engine, s, candidate={
            'policy': 'TIGHTEN_STOP', 'parameters': {'stop_price': 109.99}})


def test_publication_rejects_component_that_expired_during_provider_wait(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import assert_live_review_compatible
    engine, s, _ = fixture(monkeypatch, 110.)
    with pytest.raises(StaleDecisionError):
        assert_live_review_compatible(engine, s, components=[{
            'component_id': 'mathematical_edge', 'effective_weight': .15,
            'observed_ts': 1000., 'max_age_sec': 90.}])
