from __future__ import annotations

from seiltanzer import ai_verdict


def _snapshot() -> dict:
    return {
        "metric_coverage": {
            "summary": {
                "available_groups": 7,
                "total_groups": 8,
                "coverage_ratio": 0.875,
            }
        },
        "policy_manager": {
            "input_audit": {
                "available_count": 2,
                "total_count": 3,
                "all_required_available": False,
                "missing_required": ["cross_asset"],
                "degraded_inputs": ["option_chain"],
                "rows": {
                    "live_price": {
                        "available": True,
                        "status": "LIVE",
                        "source": "OANDA",
                        "role": "required",
                        "age_sec": 0.4,
                        "quality_score": 0.99,
                        "is_proxy": False,
                    },
                    "option_chain": {
                        "available": True,
                        "status": "DEGRADED_PROXY",
                        "source": "LAST_GOOD_CHAIN_PLUS_LIVE_UNDERLYING",
                        "role": "required",
                        "age_sec": 420.0,
                        "proxy_quality": "degraded",
                        "is_proxy": True,
                        "fallback_tier": "LAST_GOOD_CACHE",
                    },
                    "cross_asset": {
                        "available": False,
                        "status": "UNAVAILABLE",
                        "source": None,
                        "role": "required",
                        "reason": "NO_VALID_SOURCE",
                    },
                },
            },
            "policies": {},
            "evidence": {},
        },
    }


def test_availability_contract_is_explicit_and_never_converts_missing_to_zero():
    snapshot = _snapshot()
    ai_verdict._enforce_snapshot_budget_with_report_integrity(snapshot)
    contract = snapshot["metric_availability_contract"]

    assert contract["contract_version"] == "ai-metric-availability-v1"
    assert contract["missing_is_zero"] is False
    assert contract["fabrication_allowed"] is False
    assert contract["fallback_order"] == [
        "PRIMARY", "FALLBACK_SOURCE", "LAST_GOOD_CACHE", "MATHEMATICAL_PROXY",
    ]
    assert contract["inputs"]["live_price"]["source"] == "OANDA"
    assert contract["inputs"]["live_price"]["is_proxy"] is False
    assert contract["inputs"]["option_chain"]["is_proxy"] is True
    assert contract["inputs"]["option_chain"]["fallback_tier"] == "LAST_GOOD_CACHE"
    assert contract["inputs"]["cross_asset"]["available"] is False
    assert contract["inputs"]["cross_asset"]["status"] == "UNAVAILABLE"
    assert "value" not in contract["inputs"]["cross_asset"]


def test_second_budget_pass_keeps_richer_first_pass_provenance():
    snapshot = _snapshot()
    ai_verdict._enforce_snapshot_budget_with_report_integrity(snapshot)
    first = snapshot["metric_availability_contract"]
    assert first["inputs"]["option_chain"]["source"] == "LAST_GOOD_CHAIN_PLUS_LIVE_UNDERLYING"

    # Emulate a later already-compacted pass with the detailed audit rows gone.
    snapshot["policy_manager"]["input_audit"]["rows"] = {}
    ai_verdict._enforce_snapshot_budget_with_report_integrity(snapshot)
    second = snapshot["metric_availability_contract"]

    assert second["inputs"]["option_chain"]["source"] == "LAST_GOOD_CHAIN_PLUS_LIVE_UNDERLYING"
    assert second["inputs"]["option_chain"]["is_proxy"] is True
    assert second["inputs"]["cross_asset"]["reason"] == "NO_VALID_SOURCE"
    assert snapshot["snapshot_budget"]["final_bytes"] < ai_verdict.SNAPSHOT_LIMIT_BYTES
