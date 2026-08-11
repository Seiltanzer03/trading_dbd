import json

from seiltanzer.g1_shadow_artifact_refinement import _artifact_payload, _artifact_valid
from seiltanzer.g1_shadow_runtime import _sha


def _model():
    return {
        "algorithm_version": "g1c-platt-logit-v1",
        "model_family": "PLATT",
        "scope_key": "GLOBAL_TERMINAL_Q:proxy:inverse",
        "scope_json": json.dumps({
            "kind": "global_terminal_q_semantic",
            "q_relation": "proxy",
            "proxy_transform": "inverse",
        }, sort_keys=True),
        "training_cut_id": "cut-1",
        "training_cut_sha256": "a" * 64,
        "parameters_json": json.dumps({"a": 1.2, "b": -0.1}, sort_keys=True),
    }


def test_model_artifact_sha_revalidation_detects_parameter_tampering():
    model = _model()
    model["artifact_sha256"] = _sha(_artifact_payload(model))
    assert _artifact_valid(model) is True
    model["parameters_json"] = json.dumps({"a": 9.9, "b": -0.1}, sort_keys=True)
    assert _artifact_valid(model) is False


def test_model_artifact_sha_revalidation_detects_scope_tampering():
    model = _model()
    model["artifact_sha256"] = _sha(_artifact_payload(model))
    assert _artifact_valid(model) is True
    model["scope_json"] = json.dumps({
        "kind": "global_terminal_q_semantic",
        "q_relation": "proxy",
        "proxy_transform": "direct",
    }, sort_keys=True)
    assert _artifact_valid(model) is False
