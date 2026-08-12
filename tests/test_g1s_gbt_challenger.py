from __future__ import annotations

import numpy as np

from seiltanzer.g1_short_horizon_gbt_refinement import (
    GBT_CONTRACT_VERSION, MODEL_FAMILY, _fit_gbt, _predict_gbt,
)


def test_gbt_challenger_is_deterministic_and_probability_bounded():
    x=np.asarray([[i/20.0, (-1.0 if i<10 else 1.0)] for i in range(20)],dtype=float)
    y=np.asarray([0.0]*10+[1.0]*10,dtype=float)
    a=_fit_gbt(x,y); b=_fit_gbt(x,y)
    assert a == b
    assert a["contract_version"] == GBT_CONTRACT_VERSION
    assert a["deterministic"] is True
    assert a["hyperparameter_search"] is False
    assert a["stumps"]
    p=_predict_gbt(x,a)
    assert np.all(p>0) and np.all(p<1)
    assert float(np.mean(p[10:])) > float(np.mean(p[:10]))


def test_gbt_is_only_a_research_model_family():
    assert MODEL_FAMILY == "SHALLOW_GBT_STUMPS"
