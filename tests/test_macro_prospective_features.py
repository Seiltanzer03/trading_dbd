import json
import sqlite3
import threading

from seiltanzer.edge_discovery import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.macro_registry import MACRO_FEATURE_IDS


class Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()


def _row(t0=2_000_000.0, *, cpi_asof=None, fomc_asof=None):
    cpi_asof = t0 - 60 if cpi_asof is None else cpi_asof
    fomc_asof = t0 - 120 if fomc_asof is None else fomc_asof
    frozen = {
        "macro_context_v1": {
            "numeric_macro": {
                "candidate_vector": {
                    "macro.cpi_headline_mom_pct": 0.2,
                    "macro.cpi_core_yoy_pct": 3.1,
                    "macro.nfp_payroll_change_k": -23.0,
                    "macro.ism_manufacturing_pmi": 55.6,
                    "macro.ism_services_pmi": 54.1,
                },
                "releases": {
                    "CPI": {
                        "status": "VALID", "available_at": cpi_asof,
                        "official_source_verified": True,
                    },
                    "NFP": {
                        "status": "VALID", "available_at": t0 - 180,
                        "official_source_verified": True,
                    },
                    "ISM_MANUFACTURING": {
                        "status": "VALID", "available_at": t0 - 240,
                        "official_source_verified": True,
                    },
                    "ISM_SERVICES": {
                        "status": "VALID", "available_at": t0 - 300,
                        "official_source_verified": True,
                    },
                },
            },
            "fomc": {
                "available": True,
                "available_at": fomc_asof,
                "official_source_verified": True,
                "semantic": {
                    "policy_tone": 0.4,
                    "policy_shift": 0.1,
                    "inflation_concern": 0.8,
                    "growth_concern": 0.3,
                    "forward_guidance_shift": 0.2,
                    "uncertainty": 0.5,
                },
            },
        }
    }
    return {
        "instrument": "NAS100",
        "captured_ts": t0,
        "horizon_minutes": 60,
        "frozen_features_json": json.dumps(frozen),
    }


def test_frozen_official_macro_values_enter_prospective_feature_matrix():
    adapter = ProspectiveFeatureAdapter(Runtime(), available_asof=3_000_000.0)
    values, rejected, provenance = adapter._feature_values(_row(), strict=True)

    expected = {
        "macro.cpi_headline_mom_pct": 0.2,
        "macro.cpi_core_yoy_pct": 3.1,
        "macro.nfp_payroll_change_k": -23.0,
        "macro.ism_manufacturing_pmi": 55.6,
        "macro.ism_services_pmi": 54.1,
        "macro.fomc_policy_tone": 0.4,
        "macro.fomc_inflation_concern": 0.8,
    }
    assert not (set(expected) - MACRO_FEATURE_IDS)
    for feature_id, expected_value in expected.items():
        item = values[feature_id]
        assert item.value == expected_value
        assert item.training_eligible is True
        assert item.asof <= item.t0
        assert item.historical_available is False
        assert provenance[feature_id]["provenance"] == "FROZEN_T0_OFFICIAL_MACRO"
        assert provenance[feature_id]["future_points_used"] is False
        assert provenance[feature_id]["historical_backfill"] is False
        assert provenance[feature_id]["production_authority"] is False
    assert not [feature_id for feature_id in rejected if feature_id.startswith("macro.")]


def test_macro_release_after_t0_is_never_exposed_to_research():
    t0 = 2_000_000.0
    adapter = ProspectiveFeatureAdapter(Runtime(), available_asof=3_000_000.0)
    values, _rejected, _provenance = adapter._feature_values(
        _row(t0, cpi_asof=t0 + 1.0, fomc_asof=t0 + 1.0), strict=True)

    assert "macro.cpi_headline_mom_pct" not in values
    assert "macro.cpi_core_yoy_pct" not in values
    assert "macro.fomc_policy_tone" not in values
    # Other families with independently causal available_at remain valid.
    assert values["macro.nfp_payroll_change_k"].training_eligible is True


def test_stale_monthly_macro_remains_visible_but_not_training_eligible():
    t0 = 10_000_000.0
    stale_asof = t0 - 46 * 24 * 3600
    adapter = ProspectiveFeatureAdapter(Runtime(), available_asof=t0 + 1)
    values, _rejected, provenance = adapter._feature_values(
        _row(t0, cpi_asof=stale_asof), strict=True)

    item = values["macro.cpi_headline_mom_pct"]
    assert item.value == 0.2
    assert item.stale is True
    assert item.training_eligible is False
    assert provenance["macro.cpi_headline_mom_pct"]["historical_backfill"] is False


def test_unverified_macro_source_is_not_admitted():
    row = _row()
    frozen = json.loads(row["frozen_features_json"])
    frozen["macro_context_v1"]["numeric_macro"]["releases"]["CPI"]["official_source_verified"] = False
    row["frozen_features_json"] = json.dumps(frozen)
    adapter = ProspectiveFeatureAdapter(Runtime(), available_asof=3_000_000.0)
    values, _rejected, _provenance = adapter._feature_values(row, strict=True)

    assert "macro.cpi_headline_mom_pct" not in values
    assert "macro.cpi_core_yoy_pct" not in values
