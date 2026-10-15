import importlib
import importlib.util

import numpy as np
import pytest


def variants():
    assert importlib.util.find_spec('seiltanzer.forecast_variants'), 'bounded forecast variants missing'
    return importlib.import_module('seiltanzer.forecast_variants')


def test_inverse_and_baseline_shrink_have_explicit_probability_meaning():
    module = variants()
    p = np.array([.8, .2])
    assert module.variant_predictions(p, .5, 'DIRECT') == pytest.approx(p)
    assert module.variant_predictions(p, .5, 'INVERSE') == pytest.approx([.2, .8])
    assert module.variant_predictions(p, .5, 'BASELINE_SHRINK') == pytest.approx([.65, .35])


def test_unknown_variant_and_invalid_probability_fail_closed():
    module = variants()
    for p, baseline, name in [([.8], .5, 'UNVERIFIED'), ([float('nan')], .5, 'DIRECT'),
                              ([1.2], .5, 'INVERSE'), ([.8], -1., 'BASELINE_SHRINK')]:
        with pytest.raises(ValueError):
            module.variant_predictions(p, baseline, name)


def test_family_variant_selection_uses_early_blocks_not_final_test(monkeypatch):
    from seiltanzer import edge_family_market as market
    variants()
    n = 240
    t0 = 1791900000.
    times = t0 + np.arange(n) * 900
    x = np.where(np.arange(n) % 2, 1., -1.).reshape(-1, 1)
    y = np.column_stack([(x[:, 0] < 0).astype(float), np.ones(n)])
    data = {'times': times, 'ends': times + 900, 'x': x, 'y': y,
            'feature_names': ['session.test_feature'], 'groups': [(str(i),) for i in range(n)]}
    monkeypatch.setattr(market, 'fit_head', lambda x, y:
        {'mean': [0.], 'scale': [1.], 'beta': [0., 3.], 'baseline': .5})
    def fit():
        return market._fit_family('NAS100', 'session', [], times[-1]+1000, (15,), {}, [], {},
                                  datasets_override={15: data})
    first = fit()['heads']['direction']
    assert first.get('probability_variant') == 'INVERSE'
    assert first['head'].get('probability_transform') == 'INVERSE'
    original_gain = first['test_gain_mbit']
    y[int(n*.8):, 0] = 1 - y[int(n*.8):, 0]
    second = fit()['heads']['direction']
    assert second['probability_variant'] == first['probability_variant']
    assert second['validation_gains_mbit'] == first['validation_gains_mbit']
    assert second['test_gain_mbit'] != original_gain
    assert second['selection_uses_final_test'] is False
