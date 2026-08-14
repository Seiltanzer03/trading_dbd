from __future__ import annotations

from seiltanzer.edge_discovery.registry import FEATURES
from seiltanzer.edge_discovery.selective import selective_templates


def _definition(feature_id: str):
    return next(item for item in FEATURES if item.feature_id == feature_id)


def test_registry_matches_materialized_cross_and_wavelet_types():
    assert _definition("cross.confirmation").datatype == "category"
    assert _definition("regime.wavelet_phase").datatype == "float"


def test_selective_map_uses_categorical_cross_and_macro_states():
    templates = selective_templates({
        "cross.confirmation", "regime.macro", "regime.wavelet_phase",
    })
    conditions = [condition for template in templates for condition in template.conditions]

    cross = [item for item in conditions if item.feature_id == "cross.confirmation"]
    assert cross
    assert {item.kind for item in cross} == {"categorical"}
    assert {item.state for item in cross} >= {"SAME", "OPPOSITE"}

    macro = [item for item in conditions if item.feature_id == "regime.macro"]
    assert macro
    assert {item.kind for item in macro} == {"categorical"}
    assert "CHOP" in {item.state for item in macro}

    wavelet = [item for item in conditions if item.feature_id == "regime.wavelet_phase"]
    assert wavelet
    assert {item.kind for item in wavelet} == {"train_relative"}
