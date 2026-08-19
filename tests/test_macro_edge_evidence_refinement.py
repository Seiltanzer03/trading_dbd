from types import SimpleNamespace

from seiltanzer.macro_edge_evidence_refinement import (
    macro_feature_records,
    release_aware_weights,
    release_dependency_rows,
)


def _numeric_frozen(*, available_at=100.0, release_id="cpi-release-1"):
    return {
        "macro_context_v1": {
            "numeric_macro": {
                "candidate_vector": {
                    "macro.cpi_headline_mom_pct": 0.2,
                    "macro.cpi_core_yoy_pct": 3.1,
                },
                "releases": {
                    "CPI": {
                        "status": "VALID",
                        "family": "CPI",
                        "period": "2026-07",
                        "release_id": release_id,
                        "available_at": available_at,
                        "official_source_verified": True,
                    }
                },
            }
        }
    }


def test_macro_numeric_feature_requires_release_available_by_t0():
    values, provenance = macro_feature_records(
        frozen=_numeric_frozen(), instrument="NAS100", t0=101.0, horizon=30)

    headline = values["macro.cpi_headline_mom_pct"]
    assert headline.value == 0.2
    assert headline.asof == 100.0
    assert headline.training_eligible is True
    assert headline.dependency_group == "macro_release:CPI"
    assert provenance["macro.cpi_headline_mom_pct"]["release_id"] == "cpi-release-1"
    assert provenance["macro.cpi_headline_mom_pct"]["future_points_used"] is False

    future_values, _ = macro_feature_records(
        frozen=_numeric_frozen(available_at=102.0),
        instrument="NAS100", t0=101.0, horizon=30)
    assert "macro.cpi_headline_mom_pct" not in future_values


def test_fomc_semantic_feature_uses_document_as_release_id():
    frozen = {
        "macro_context_v1": {
            "fomc": {
                "available": True,
                "available_at": 100.0,
                "published_at": 90.0,
                "document_id": "fomc-doc-1",
                "official_source_verified": True,
                "semantic": {
                    "policy_tone": 0.25,
                    "policy_shift": -0.1,
                    "inflation_concern": 0.8,
                    "growth_concern": 0.4,
                    "forward_guidance_shift": -0.2,
                    "uncertainty": 0.6,
                },
            }
        }
    }
    values, provenance = macro_feature_records(
        frozen=frozen, instrument="XAUUSD", t0=101.0, horizon=60)

    assert values["macro.fomc_policy_tone"].value == 0.25
    assert values["macro.fomc_policy_tone"].dependency_group == "macro_release:FOMC_STATEMENT"
    assert provenance["macro.fomc_policy_tone"]["release_id"] == "fomc-doc-1"


def _row(ts, release_id):
    return {
        "instrument": "NAS100",
        "horizon_minutes": 30,
        "captured_ts": float(ts),
        "feature_values": {
            "macro.cpi_headline_mom_pct": {
                "release_id": release_id,
            }
        },
    }


def test_repeated_t0s_from_one_macro_release_count_as_one_effective_unit():
    rule = SimpleNamespace(conditions=[
        SimpleNamespace(feature_id="macro.cpi_headline_mom_pct")
    ])
    rows = [_row(100.0, "release-a"), _row(120.0, "release-a"), _row(140.0, "release-a")]

    dependent = release_dependency_rows(rows, rule)
    weights, effective = release_aware_weights(dependent)

    assert effective == 1
    assert list(weights) == [1/3, 1/3, 1/3]
    assert all(row["_ede_dependency_unit_kind"] == "OFFICIAL_MACRO_RELEASE_ID"
               for row in dependent)


def test_two_macro_releases_count_as_two_effective_units_not_four_t0s():
    rule = SimpleNamespace(conditions=[
        SimpleNamespace(feature_id="macro.cpi_headline_mom_pct")
    ])
    rows = [
        _row(100.0, "release-a"), _row(120.0, "release-a"),
        _row(200.0, "release-b"), _row(220.0, "release-b"),
    ]

    dependent = release_dependency_rows(rows, rule)
    weights, effective = release_aware_weights(dependent)

    assert effective == 2
    assert list(weights) == [0.5, 0.5, 0.5, 0.5]


def test_non_macro_rule_keeps_default_market_dependency_contract():
    rule = SimpleNamespace(conditions=[SimpleNamespace(feature_id="price.ret_15m")])
    rows = [_row(100.0, "release-a"), _row(120.0, "release-a")]

    untouched = release_dependency_rows(rows, rule)

    assert untouched is rows
    assert all("_ede_dependency_unit_id" not in row for row in untouched)
