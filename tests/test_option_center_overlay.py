from types import SimpleNamespace

import seiltanzer.ai_policy_v12 as policy
import seiltanzer.ai_verdict_v13 as verdict


def _inputs(*, drift=0.12):
    return policy.PolicyInputs(
        r0=2.0,
        T=3.0,
        sigma_R=1.0,
        drift_R=drift,
        skew_R=0.0,
        term_slope=0.0,
        horizon_minutes=480.0,
        max_r=2.0,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=180.0,
        chain_status="delayed",
        proxy_quality="reference_proxy",
        source="options_barrier_first_touch",
    )


def _base_evidence():
    return {
        "cone_rnd": {},
        "adverse_confirmations": [
            {"metric": "rnd_median_r", "family": "option_distribution", "value": 1.8}
        ],
        "supportive_contradictions": [],
        "context_observations": [],
        "decision_roles": {
            "core_path_inputs": ["drift_R"],
            "context_only": [],
        },
        "data_quality": {},
    }


def test_accepted_option_center_is_one_option_family(monkeypatch):
    monkeypatch.setattr(policy, "_BASE_BUILD_EVIDENCE", lambda *args, **kwargs: _base_evidence())
    tick = {
        "cone": {
            "market_mean_r": 2.55,
            "forward_drift_source": "bl_forward_shrunk",
            "forward_drift_rejected": None,
        }
    }
    evidence = policy.build_metric_evidence(
        SimpleNamespace(), tick, {}, {"direction": "long"},
        _inputs(drift=0.12), SimpleNamespace(), {},
    )
    center = evidence["cone_rnd"]["option_center"]
    assert center["raw_mean_r"] == 2.55
    assert center["robust_forward_r"] == 2.12
    assert center["raw_mean_accepted"] is True
    assert center["optimizer_role"] == "core_path_input_via_drift_R"
    rows = evidence["supportive_contradictions"]
    added = next(row for row in rows if row["metric"] == "option_center_robust_gap_r")
    assert added["family"] == "option_distribution"
    assert added["value"] == 0.12
    assert "robust_option_center_drift" in evidence["decision_roles"]["core_path_inputs"]


def test_rejected_raw_mean_stays_context_only(monkeypatch):
    monkeypatch.setattr(policy, "_BASE_BUILD_EVIDENCE", lambda *args, **kwargs: _base_evidence())
    tick = {
        "cone": {
            "market_mean_r": 5.5,
            "forward_drift_source": "carry_neutral",
            "forward_drift_rejected": 3.5,
        }
    }
    evidence = policy.build_metric_evidence(
        SimpleNamespace(), tick, {}, {"direction": "long"},
        _inputs(drift=0.0), SimpleNamespace(), {},
    )
    center = evidence["cone_rnd"]["option_center"]
    assert center["raw_mean_accepted"] is False
    assert center["optimizer_role"] == "context_only_rejected"
    assert not any(
        row.get("metric") == "option_center_robust_gap_r"
        for row in evidence["adverse_confirmations"] + evidence["supportive_contradictions"]
    )
    row = next(
        row for row in evidence["context_observations"]
        if row["metric"] == "option_center_raw_rejected"
    )
    assert row["context_only"] is True
    assert row["family"] == "option_distribution"


def test_report_explains_raw_mean_and_robust_forward(monkeypatch):
    monkeypatch.setattr(
        verdict,
        "_BASE_RENDER",
        lambda snapshot: "\n".join([
            "**ДЕЙСТВИЕ СЕЙЧАС** — HOLD.",
            "",
            "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —",
            "Базовые подтверждения.",
            "",
            "**КАЧЕСТВО ДАННЫХ** —",
            "Надёжность низкая.",
        ]),
    )
    snapshot = {
        "policy_manager": {
            "option_center": {
                "available": True,
                "raw_mean_r": 2.55,
                "raw_gap_r": 0.55,
                "robust_forward_r": 2.12,
                "robust_gap_r": 0.12,
                "source": "bl_forward_shrunk",
                "raw_mean_accepted": True,
                "raw_rejected_gap_r": None,
            }
        }
    }
    report = verdict.render_policy_report(snapshot)
    assert "raw RND mean H +2.550R" in report
    assert "robust forward +2.120R" in report
    assert "Expected/CVaR" in report
    assert "одной семьёй option_distribution" in report
