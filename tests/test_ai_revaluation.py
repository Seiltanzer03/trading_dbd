from seiltanzer.ai_policy_v13 import _compact, _evidence_item
from seiltanzer.ai_verdict_v14 import _revaluation_line


def _rev(weighted=-0.32, confidence=0.62, samples=30, mode="indicative_mapping"):
    return {
        "available": True,
        "version": "lattice-revaluation-v1",
        "sample_count": samples,
        "age_sec": 120,
        "entry": {
            "p_take": 0.55, "barrier_ev_r": 0.20,
            "q50_r": 0.30, "width_r": 3.0,
            "buckets": {"stop_tail": .10, "red_zone": .20,
                        "green_zone": .50, "take_tail": .20},
        },
        "average": {
            "p_take": 0.48, "barrier_ev_r": 0.05,
            "q50_r": 0.10, "width_r": 3.2,
            "buckets": {"stop_tail": .14, "red_zone": .24,
                        "green_zone": .47, "take_tail": .15},
        },
        "current": {
            "p_take": 0.40, "barrier_ev_r": -0.15,
            "q50_r": -0.20, "width_r": 3.4,
            "buckets": {"stop_tail": .20, "red_zone": .28,
                        "green_zone": .42, "take_tail": .10},
        },
        "change_from_entry": {
            "p_take": -0.15, "barrier_ev_r": -0.35,
            "q50_r": -0.50, "width_r": 0.40,
            "buckets": {"stop_tail": .10, "red_zone": .08,
                        "green_zone": -.08, "take_tail": -.10},
        },
        "change_from_average": {
            "p_take": -0.08, "barrier_ev_r": -0.20,
            "q50_r": -0.30, "width_r": 0.20,
        },
        "momentum": {
            "p_take_pp_per_min": -0.8,
            "barrier_ev_r_per_min": -0.02,
            "center_r_per_min": -0.03,
            "p_take_noise_pp": 1.2,
            "direction_consistency": 0.75,
        },
        "score": {
            "raw": -0.52, "weighted": weighted,
            "direction": "deteriorating",
            "confidence_weight": confidence,
            "source_weight": 0.62,
            "sample_weight": 1.0,
            "noise_weight": 0.9,
        },
        "source_quality": {
            "mode": mode, "label": "INDICATIVE MAPPING",
            "weight": 0.62, "chain_age_sec": 120,
            "experimental_proxy": False, "context_only": False,
        },
    }


def test_material_indicative_revaluation_is_adverse_not_disabled():
    compact = _compact(_rev())
    item, role = _evidence_item(compact)
    assert role == "adverse"
    assert item["family"] == "option_distribution"
    assert item["independent_vote"] is False
    assert item["authority"] == "weighted_derived_same_family"
    assert item["source_mode"] == "indicative_mapping"


def test_immature_or_low_weight_history_stays_context_only():
    compact = _compact(_rev(weighted=-0.28, confidence=0.25, samples=3))
    item, role = _evidence_item(compact)
    assert role == "context"
    assert item["context_only"] is True


def test_report_explains_entry_average_current_and_weight():
    manager = {"lattice_revaluation": _compact(_rev())}
    line = _revaluation_line(manager)
    assert line is not None
    assert "вход/среднее/сейчас" in line
    assert "INDICATIVE MAPPING" in line
    assert "одна семья option_distribution" in line
