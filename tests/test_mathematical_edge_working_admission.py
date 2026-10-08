"""Working historical admission does not certify profit or scientific stability."""
import pytest

from seiltanzer import mathematical_edge as edge
from test_mathematical_edge import runtime_fixture


@pytest.mark.parametrize('validation,blocks,test_gain,tier,factor', [
    ([8., -1., -1.], [8., -1., -1.], 2., 'LIMITED_HISTORICAL', .5),
    ([2., 2., -1.], [2., 2., -1.], 1., 'STABLE_HISTORICAL', 1.),
    ([2., 2., -10.], [2., 2., 2.], 2., 'UNSUPPORTED', 0.),
    ([2., 2., 2.], [2., 2., 2.], -1., 'UNSUPPORTED', 0.),
    ([2., 2., 2.], [2., None, 2.], 2., 'UNSUPPORTED', 0.),
    ([2., 2., float('nan')], [2., 2., 2.], 2., 'UNSUPPORTED', 0.),
])
def test_historical_gain_is_required_on_both_untouched_stages(validation, blocks, test_gain, tier, factor):
    result = edge.working_admission(validation, blocks, test_gain)
    assert result['tier'] == tier
    assert result['weight_multiplier'] == factor
    assert result['supported'] is (factor > 0)


def test_limited_historical_head_has_half_the_runtime_influence(tmp_path, monkeypatch):
    engine, tick, trade, report, dest = runtime_fixture(tmp_path, monkeypatch)
    stable = edge.runtime_profile(engine, tick, trade)
    model = report['instruments']['NAS100']
    model['diagnostics']['direction'] = {**model['diagnostics']['direction'],
        'working_admission': {'tier': 'LIMITED_HISTORICAL', 'supported': True,
                              'weight_multiplier': .5}}
    model['model_sha256'] = edge.fingerprint({k: v for k, v in model.items() if k != 'model_sha256'})
    import json
    dest.write_text(json.dumps(report))
    limited = edge.runtime_profile(engine, tick, trade)
    assert limited['available']
    assert limited['weight_fraction'] == pytest.approx(stable['weight_fraction'] / 2, abs=1e-6)
    assert limited['evidence_tier'] == 'LIMITED_HISTORICAL'
    assert limited['formal_eligible'] is False and limited['hard_risk_modified'] is False
    assert 'ограниченный исторический допуск' in edge.render_math_edge(limited)


def test_training_retains_positive_aggregate_with_only_one_positive_block(monkeypatch):
    rows = [dict(bar_end_ts=(i+1)*300., open=100+i*.01, high=101+i*.01,
                 low=99+i*.01, close=100+i*.01) for i in range(900)]
    scores = iter([8., 8., -1., -1., -1., -1.] + [8., -1., -1., 2.]*2)
    monkeypatch.setattr(edge, 'gain', lambda *args: next(scores))
    monkeypatch.setattr(edge, 'train_path_heads', lambda *args: {})
    result = edge.train_instrument('NAS100', rows, rows[-1]['bar_end_ts'], horizons=(15,))
    assert result['working_eligible'] is True
    assert result['formal_eligible'] is False and result['net_economic_proof'] is False
    assert all(d['working_admission']['tier'] == 'LIMITED_HISTORICAL'
               for d in result['diagnostics'].values())
    assert result['candidate_audit']['15']['final_test_touched_for_selection'] is False


def test_limited_path_prediction_also_reduces_influence(tmp_path, monkeypatch):
    _, tick, _, report, _ = runtime_fixture(tmp_path, monkeypatch)
    model = report['instruments']['NAS100']
    row = {**model['diagnostics']['direction'], 'head': model['heads']['direction'],
           'training_cutoff': model['training_cutoff'], 'horizon_minutes': 15,
           'target_semantics': edge.PATH_TARGET_CONTRACT['upside_excursion']}
    path_model = {'path_target_contract': edge.PATH_TARGET_CONTRACT,
                  'path_heads': {'upside_excursion': row}}
    features = [0.] * len(edge.FEATURES)
    stable = edge.runtime_path_predictions(path_model, features, tick['ts'])['upside_excursion']
    row['working_admission'] = {'tier': 'LIMITED_HISTORICAL', 'supported': True,
                                'weight_multiplier': .5}
    limited = edge.runtime_path_predictions(path_model, features, tick['ts'])['upside_excursion']
    assert limited['max_effective_weight_fraction'] == pytest.approx(
        stable['max_effective_weight_fraction'] / 2, abs=1e-6)
    assert limited['evidence_tier'] == 'LIMITED_HISTORICAL'
    from seiltanzer.operational_edge_compaction import compact_operational_profile
    compacted = compact_operational_profile({'path_predictions': {'upside_excursion': limited}})
    assert compacted['path_predictions']['upside_excursion']['evidence_tier'] == 'LIMITED_HISTORICAL'


def test_unsupported_runtime_probabilities_are_not_reported_as_stable(tmp_path, monkeypatch):
    engine, tick, trade, report, dest = runtime_fixture(tmp_path, monkeypatch)
    model = report['instruments']['NAS100']
    for name in ('direction', 'movement'):
        model['diagnostics'][name] = {**model['diagnostics'][name],
                                      'working_supported': False, 'gain_mbit': -2.}
    model['model_sha256'] = edge.fingerprint({k: v for k, v in model.items() if k != 'model_sha256'})
    import json
    dest.write_text(json.dumps(report))
    profile = edge.runtime_profile(engine, tick, trade)
    assert not profile['available'] and profile['weight_fraction'] == 0
    text = edge.render_math_edge(profile)
    assert 'устойчивый исторический допуск' not in text
    assert 'допуск не подтверждён' in text
