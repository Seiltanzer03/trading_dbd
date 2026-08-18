from seiltanzer.edge_discovery.macro_registry import MACRO_FEATURE_DEFINITIONS
from seiltanzer.edge_discovery.universal_templates import (
    universal_candidate_templates,
    universal_feature_definitions,
)


def test_macro_registry_is_future_only_and_training_eligible_for_prospective_evidence():
    assert MACRO_FEATURE_DEFINITIONS
    for item in MACRO_FEATURE_DEFINITIONS:
        assert item.feature_id.startswith("macro.")
        assert item.family == "MACRO"
        assert item.historical_availability == "UNAVAILABLE"
        assert item.live_availability == "AVAILABLE"
        assert item.training_eligibility is True
        assert item.research_scope == "G1S"
        assert "production authority false" in item.notes


def test_universal_research_definitions_include_macro_without_mutating_legacy_registry():
    definitions = universal_feature_definitions()
    ids = {item.feature_id for item in definitions}
    assert "macro.cpi_core_yoy_pct" in ids
    assert "macro.nfp_payroll_change_k" in ids
    assert "macro.ism_manufacturing_pmi" in ids
    assert "macro.ism_services_pmi" in ids
    assert "macro.fomc_policy_tone" in ids


def test_eligible_macro_feature_gets_bounded_interpretable_single_templates():
    templates = universal_candidate_templates(
        eligible_feature_ids={"macro.cpi_core_yoy_pct"},
    )
    ids = {item.template_id for item in templates}
    assert any("macro.cpi_core_yoy_pct" in template_id and "ABOVE_MEDIAN" in template_id for template_id in ids)
    assert any("macro.cpi_core_yoy_pct" in template_id and "BELOW_MEDIAN" in template_id for template_id in ids)
