from seiltanzer.config import Settings
from seiltanzer.g1_shadow_refinement import _semantic_model_matches, _semantic_scope_definitions
from seiltanzer.g1_shadow_runtime import _record_error
from seiltanzer.passive_learning import PassiveLearningEngine


def _row(observation_id, instrument, relation, transform, cohort):
    return {
        "observation_id": observation_id,
        "instrument": instrument,
        "base_cohort_id": cohort,
        "base_cohort": {
            "q_relation": relation,
            "proxy_transform": transform,
            "instrument": instrument,
            "horizon_bucket": "1D_3D",
        },
    }


def test_global_and_instrument_scopes_never_mix_q_proxy_semantics():
    rows = [
        _row("native", "NAS100", "self", "direct", "c-native"),
        _row("direct", "SP500", "proxy", "direct", "c-direct"),
        _row("inverse", "USDCAD", "proxy", "inverse", "c-inverse"),
    ]
    scopes = _semantic_scope_definitions(rows)
    global_scopes = [(key, scope, members) for key, scope, members in scopes if scope["kind"] == "global_terminal_q_semantic"]
    assert len(global_scopes) == 3
    assert all(len(members) == 1 for _, _, members in global_scopes)
    assert {scope["proxy_transform"] for _, scope, _ in global_scopes} == {"direct", "inverse"}
    assert not any(key == "GLOBAL_TERMINAL_Q" for key, _, _ in scopes)

    inverse_scope = next(scope for key, scope, _ in scopes if key == "GLOBAL_TERMINAL_Q:proxy:inverse")
    model = {"scope_json": __import__("json").dumps(inverse_scope)}
    assert _semantic_model_matches(model, rows[2]) is True
    assert _semantic_model_matches(model, rows[0]) is False
    assert _semantic_model_matches(model, rows[1]) is False


def test_rejected_prediction_is_not_a_critical_g1d_integrity_error(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    _record_error(engine, "PREDICTION_T0_CONTRACT_REJECTED", observation_id="x", detail="manual")
    status = engine.g1c_status()
    assert status["contract_error_n"] == 1
    assert status["critical_contract_error_n"] == 0
    assert "CRITICAL_CONTRACT_ERRORS" not in status["g1d_readiness"]["blockers"]
    _record_error(engine, "TRAINING_CUT_MUTATED", fit_run_id="f", detail="tamper")
    status = engine.g1c_status()
    assert status["critical_contract_error_n"] == 1
    assert "CRITICAL_CONTRACT_ERRORS" in status["g1d_readiness"]["blockers"]
    engine.close()
