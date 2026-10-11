"""Demo admission through the actual snapshot, report and position ledger.

The model is deliberately frozen and local. These are execution-contract tests,
not claims about the profitability of its synthetic probabilities.
"""
from copy import deepcopy
import json
import math
import time

import pytest

from seiltanzer import ai_policy, ai_verdict, mathematical_edge as edge
from seiltanzer.active_management import select_active_management
from seiltanzer.config import Settings
from seiltanzer.engine import Engine


@pytest.fixture
def frozen_engine(tmp_path, monkeypatch):
    engine = Engine(Settings(demo=True, data_dir=str(tmp_path)))
    engine.market.refresh_price()
    engine.market.refresh_vols()
    engine.market.refresh_correlation()
    price = float(engine.market.price['value'])
    trade = engine.journal.open_trade(3, 'NAS100', 'long', price, price * .99, price * 1.025)
    engine.on_trade_opened(trade)
    now = time.time()
    end = math.floor(now / 300) * 300
    engine.market.intraday_ohlcv = [
        [end - (13-i) * 300 + minute * 60, price, price * 1.001,
         price * .999, price, 1]
        for i in range(13) for minute in range(5)
    ]
    n = len(edge.FEATURES)
    head = dict(mean=[0.] * n, scale=[1.] * n,
                beta=[math.log(.2 / .8)] + [0.] * n, baseline=.5)
    diagnostic = dict(gain_mbit=20., positive_blocks=3, blocks=3,
                      working_supported=True, test_n=120)
    model = dict(instrument='NAS100', horizon_minutes=15,
                 feature_contract=edge.FEATURE_CONTRACT, features=list(edge.FEATURES),
                 target_contract={'direction': 'UP_GIVEN_ABS_RETURN_GT_2BP',
                                  'movement': 'ABS_RETURN_GT_2BP',
                                  'threshold_log_return': .0002},
                 heads={'direction': deepcopy(head), 'movement': deepcopy(head)},
                 diagnostics={'direction': deepcopy(diagnostic), 'movement': deepcopy(diagnostic)},
                 training_cutoff=now - 3600, training_n=600, status='WORKING_SUPPORTED')
    model['model_sha256'] = edge.fingerprint(model)
    from seiltanzer import runtime_git_identity
    monkeypatch.setattr(runtime_git_identity, 'runtime_git_sha', lambda: 'a' * 40)
    report = dict(contract_version=edge.CONTRACT, created_ts=now - 60,
                  automatic_execution=False, production_authority=False,
                  published_for_sha='a' * 40, instruments={'NAS100': model})
    artifact = tmp_path / 'research' / 'mathematical_edge_latest.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(report))

    # Share precisely the same market/MC generation between reviews, while the
    # ledger's current remainder is re-read for every new snapshot.
    frozen_tick = engine.tick_payload()
    def canonical_tick():
        tick = deepcopy(frozen_tick)
        tick['ts'] = time.time()
        tick['trade'] = engine._managed_trade(engine.journal.active_trade())
        return tick
    monkeypatch.setattr(engine, 'canonical_tick_payload', canonical_tick)
    try:
        yield engine, artifact
    finally:
        engine.close()


def economic_metrics(snapshot):
    keys = ('expected_final_r', 'median_final_r', 'cvar10_r', 'p_final_profit')
    return {name: {key: row[key] for key in keys}
            for name, row in snapshot['policy_manager']['policies'].items()}


def test_demo_snapshot_and_report_do_not_admit_frozen_model(frozen_engine):
    engine, artifact = frozen_engine
    enabled = ai_verdict.build_snapshot(engine)
    manager = enabled['policy_manager']
    profile = manager['mathematical_edge']
    assert profile['available'] is False
    assert profile['reason'] == 'SIMULATED_PRICE_INPUT_NOT_ADMISSIBLE'
    assert not profile.get('probabilities')
    assert manager['combined_edge_soft_weight']['mathematical_component_weight'] == 0
    assert not profile['hard_risk_modified'] and not profile['independent_evidence_vote']
    assert 'combined_edge_soft_weight' not in manager['selection_rule']
    eligible = manager['selection_rule']['eligible']
    metrics = manager['policies']
    assert manager['selection_rule']['best_expected_r'] == max(
        metrics[name]['expected_final_r'] for name in eligible)

    artifact.unlink()
    disabled = ai_verdict.build_snapshot(engine)
    assert not disabled['policy_manager']['mathematical_edge']['available']
    # Installing a frozen artifact cannot give demo inputs a real model vote
    # or alter the common-path economic distributions.
    assert economic_metrics(enabled) == economic_metrics(disabled)
    assert manager['selection_rule']['cvar_floor_r'] == disabled['policy_manager']['selection_rule']['cvar_floor_r']

    report = ai_verdict.render_policy_report(enabled)
    assert '**МАТЕМАТИЧЕСКИЙ EDGE**' in report
    assert 'SIMULATED_PRICE_INPUT_NOT_ADMISSIBLE' in report
    for name, row in metrics.items():
        assert f"{name}: Expected net {row['expected_final_r']:+.3f}R" in report
        assert f"CVaR10 net {row['cvar10_r']:+.3f}R" in report


def test_demo_risk_gate_survives_rejected_math_model(frozen_engine):
    engine, _ = frozen_engine
    snapshot = ai_verdict.build_snapshot(engine)
    assert snapshot['policy_manager']['mathematical_edge']['available'] is False
    selected = select_active_management(snapshot)
    rows = snapshot['active_management_candidates']
    assert len(rows) == 7
    for row in rows:
        assert row['mathematical_edge_ranking_bonus_r'] == 0
    if selected is not None:
        assessment = selected['quant_evaluation']
        assert assessment['status'] == 'eligible'
        assert assessment['paired_delta_ci95_lower_r'] > assessment['materiality_band_r']
        assert assessment['worst_seed_cvar10_net_r'] >= assessment['hard_net_floor_r']


def test_hold_and_hard_floor_survive_installed_runtime_selector(frozen_engine):
    engine, _ = frozen_engine
    snapshot = ai_verdict.build_snapshot(engine)
    profile = snapshot['policy_manager']['combined_edge_soft_weight']
    from seiltanzer.active_edge_policy_weight import _PROFILE_CTX
    # HOLD remains the least intervention inside genuine economic indifference.
    # A larger EXIT Expected cannot become admissible through its soft score.
    metrics = {
        name: dict(name=name, expected_final_r=.1, cvar10_r=-.2)
        for name in ('HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50')
    }
    metrics['EXIT'] = dict(name='EXIT', expected_final_r=10., cvar10_r=-2.)
    token = _PROFILE_CTX.set(profile)
    try:
        policy, rule = ai_policy._raw_policy_choice(metrics, 0., cvar_floor=-.5)
    finally:
        _PROFILE_CTX.reset(token)
    assert policy == 'HOLD'
    assert 'EXIT' not in rule['eligible']
    assert rule['best_expected_r'] == .1
    assert metrics['EXIT']['cvar10_r'] == -2.
