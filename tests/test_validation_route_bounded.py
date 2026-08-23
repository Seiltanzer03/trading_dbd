from __future__ import annotations

from seiltanzer import app as app_module
from seiltanzer.app import _validation_report_from_score
from seiltanzer.journal import Journal


def _score_fixture() -> dict:
    return {
        "version": "q-calibration-scorecard-f1-v1",
        "n": 7,
        "censored_n": 2,
        "outcome_counts": {"take": 3, "stop_or_be": 2, "no_touch": 0},
        "oos_scorecard": {"split": "chronological_70_30_train_test"},
        "take": {
            "q_model_brier": 0.125,
            "q_model_log_loss": 0.42,
            "reliability_curve": [{"predicted": 0.5, "observed": 0.4}],
        },
    }


def test_validation_projection_matches_journal_compatibility_contract():
    score = _score_fixture()
    policy_shadow = {"observations": 4, "promotion_allowed": False}
    journal = Journal(":memory:")
    try:
        journal.q_calibration_report = lambda: score  # type: ignore[method-assign]
        journal.policy_shadow_report = lambda: policy_shadow  # type: ignore[method-assign]
        assert _validation_report_from_score(score, policy_shadow) == journal.validation_report()
    finally:
        journal.close()


def test_validation_route_computes_q_calibration_once(monkeypatch):
    score = _score_fixture()

    class FakeJournal:
        def __init__(self):
            self.q_calls = 0

        def q_calibration_report(self):
            self.q_calls += 1
            return score

        def policy_shadow_report(self):
            return {"observations": 1, "promotion_allowed": False}

        def counterfactual_report(self):
            return {"reviews": 0}

    class FakeEngine:
        def __init__(self):
            self.journal = FakeJournal()
            self.stream_hub = None

    engine = FakeEngine()
    monkeypatch.setattr(app_module, "Engine", lambda _settings: engine)
    app = app_module.create_app(settings=object())  # type: ignore[arg-type]
    endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", None) == "/api/validation"
    )

    report = endpoint()

    assert engine.journal.q_calls == 1
    assert report["q_calibration"] is score
    assert report["counterfactual_replay"] == {"reviews": 0}
    assert report["n"] == score["n"]
