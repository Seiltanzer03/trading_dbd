from seiltanzer.edge_discovery import selective
from seiltanzer.macro_ism_historical_ede_refinement import (
    install_ism_historical_ede_refinement,
)


def test_macro_feature_registry_reaches_v13_selective_templates():
    install_ism_historical_ede_refinement()
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


def test_deterministic_fomc_registry_reaches_v13_selective_templates():
    install_ism_historical_ede_refinement()
    feature_id = "macro.fomc_target_change_bp"

    assert feature_id in selective._FAMILY
    assert selective._FAMILY[feature_id] == "MACRO_FOMC_DETERMINISTIC"
    assert selective._DEPENDENCY[feature_id] == "macro_release:FOMC_STATEMENT"

    templates = selective.selective_templates({feature_id})
    assert templates
    assert all(template.complexity == 1 for template in templates)
    assert {
        condition.feature_id
        for template in templates
        for condition in template.conditions
    } == {feature_id}


def test_historical_ism_registry_reaches_v13_selective_templates_without_new_ids():
    install_ism_historical_ede_refinement()
    feature_id = "macro.ism_services_pmi_change_pp"

    assert feature_id in selective._FAMILY
    assert selective._FAMILY[feature_id] == "MACRO_NUMERIC"
    assert selective._DEPENDENCY[feature_id] == "macro_release:ISM_SERVICES"
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
    assert "macro.fomc_dissent_share" in selective._FAMILY
    assert "macro.ism_manufacturing_pmi" in selective._FAMILY
    assert getattr(selective, "_macro_registry_runtime_refresh", None)