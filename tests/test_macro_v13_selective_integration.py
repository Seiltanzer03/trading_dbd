from seiltanzer.edge_discovery import selective
from seiltanzer.macro_bls_historical_ede_refinement import (
    install_bls_historical_ede_refinement,
)


def test_macro_feature_registry_reaches_v13_selective_templates():
    install_bls_historical_ede_refinement()
    feature_id = "macro.cpi_headline_mom_pct"

    assert feature_id in selective._FAMILY
    assert selective._FAMILY[feature_id] == "MACRO_NUMERIC"
    assert selective._DEPENDENCY[feature_id] == "macro_release:CPI"

    templates = selective.selective_templates({feature_id})
    assert templates
    assert all(template.complexity == 1 for template in templates)
    assert {
        condition.feature_id
        for template in templates
        for condition in template.conditions
    } == {feature_id}


def test_v13_adapter_import_keeps_macro_selective_registry_materialized():
    from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter

    assert ProspectiveFeatureAdapter is not None
    assert "macro.nfp_payroll_change_k" in selective._FAMILY
    assert getattr(selective, "_macro_registry_runtime_refresh", None)
