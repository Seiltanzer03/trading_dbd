"""Fast fail-closed quote preflight and its exact production smoke contract."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

import seiltanzer.app as app_module
from seiltanzer.ai_api import AI_API_VERSION
from test_ai_verdict_api import _client, _snapshot


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_functional_smoke.py"
_SPEC = importlib.util.spec_from_file_location("production_quote_preflight_smoke", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
smoke = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke)


def _declared_snapshot():
    frozen = _snapshot()
    frozen["trade_geometry"] = {"current": 110.}
    frozen["policy_manager"]["input_audit"] = {"rows": {"instrument_price": {
        "available": True, "status": "live", "source": "executing-broker",
        "production_authority": True}}}
    return frozen


def _negative_body():
    return {
        "ok": False,
        "api_version": AI_API_VERSION,
        "error": {
            "code": "authoritative_price_unavailable",
            "message": "Авторитетная текущая цена инструмента недоступна",
            "request_id": "ai-0123456789abcdef0123",
            "retriable": True,
        },
    }


@pytest.mark.parametrize("variant", [
    "unavailable", "missing_available", "no_data", "missing_current",
    "nonfinite_current", "untrusted", "proxy_source", "null_price_row",
])
def test_unavailable_declared_quote_rejects_before_any_mutation_or_enrichment(
        tmp_path, monkeypatch, variant):
    seen = []

    def forbidden(*_args, **_kwargs):
        seen.append("forbidden_downstream_work")
        raise AssertionError("missing quote must fail before downstream work")

    app, client = _client(tmp_path, monkeypatch, forbidden)
    frozen = _declared_snapshot()
    price = frozen["policy_manager"]["input_audit"]["rows"]["instrument_price"]
    if variant == "unavailable":
        price["available"] = False
    elif variant == "missing_available":
        del price["available"]
    elif variant == "no_data":
        price["status"] = "no_data"
    elif variant == "missing_current":
        del frozen["trade_geometry"]["current"]
    elif variant == "nonfinite_current":
        frozen["trade_geometry"]["current"] = float("nan")
    elif variant == "untrusted":
        price["production_authority"] = False
    elif variant == "proxy_source":
        price["source"] = "Bybit indicative fallback"
    else:
        frozen["policy_manager"]["input_audit"]["rows"]["instrument_price"] = None
    monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: deepcopy(frozen))
    monkeypatch.setattr(app.state.engine.position, "sync_be", forbidden)
    monkeypatch.setattr(app_module, "_refresh_management_decision", forbidden)
    monkeypatch.setattr(app_module, "_attach_family_source_bundle", forbidden)
    monkeypatch.setattr(app_module, "_publish_unified_review", forbidden)
    monkeypatch.setattr(app_module, "render_policy_report", forbidden)
    monkeypatch.setattr(app.state.engine.journal, "record_ai_verdict", forbidden)
    try:
        for _ in range(2):
            response = client.post("/api/ai/verdict")
            assert response.status_code == 503
            body = response.json()
            assert set(body) == {"ok", "api_version", "error"}
            assert body["ok"] is False
            assert body["api_version"] == AI_API_VERSION
            assert set(body["error"]) == {"code", "message", "request_id", "retriable"}
            assert body["error"]["code"] == "authoritative_price_unavailable"
            assert body["error"]["retriable"] is True
            assert body["error"]["request_id"].startswith("ai-")
            assert isinstance(body["error"]["message"], str) and body["error"]["message"]
        # The second response remains the same 503: failed preflight did not
        # consume ai_last_call and become the 15-second rate-limit response.
        assert seen == []
    finally:
        app.state.engine.close()


@pytest.mark.parametrize("declared", [True, False])
def test_valid_declared_quote_and_legacy_undeclared_fixture_keep_success_path(
        tmp_path, monkeypatch, declared):
    calls = []

    def provider(frozen):
        calls.append(frozen)
        return {"verdict": "LLM", "model": "test-model"}

    app, client = _client(tmp_path, monkeypatch, provider)
    frozen = _declared_snapshot() if declared else _snapshot()
    monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: deepcopy(frozen))
    try:
        response = client.post("/api/ai/verdict")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert len(calls) == 1
    finally:
        app.state.engine.close()


def _install_smoke_replies(monkeypatch, code, body, elapsed):
    calls = []

    monkeypatch.setattr(smoke, "verify_management_ack_guard_contract", lambda: None)
    monkeypatch.setattr(smoke, "verify_trade_settlement_contract", lambda: None)
    monkeypatch.setattr(
        smoke, "verify_isolated_unified_management_contract",
        lambda: calls.append("isolated_12_action_contract"))
    monkeypatch.setattr(smoke, "wait_for_ai_snapshot_ready", lambda: {})
    monkeypatch.setattr(
        smoke, "request",
        lambda *_args, **_kwargs: (code, deepcopy(body), elapsed))
    return calls


def test_smoke_accepts_only_fast_exact_quote_unavailable_contract(monkeypatch):
    calls = _install_smoke_replies(monkeypatch, 503, _negative_body(), 3.)
    smoke.verify_ai_verdict()
    assert calls == ["isolated_12_action_contract"]


@pytest.mark.parametrize("code,elapsed,error_code", [
    (503, 12_000., "authoritative_price_unavailable"),
    (503, 13_840., "authoritative_price_unavailable"),
    (422, 3., "invalid_common_economics"),
    (422, 3., "authoritative_price_unavailable"),
    (503, 3., "unrecognized_failure"),
    (504, 3., "authoritative_price_unavailable"),
])
def test_smoke_still_rejects_slow_arbitrary_or_unrecognized_failures(
        monkeypatch, code, elapsed, error_code):
    body = _negative_body()
    body["error"]["code"] = error_code
    _install_smoke_replies(monkeypatch, code, body, elapsed)
    with pytest.raises(AssertionError):
        smoke.verify_ai_verdict()


@pytest.mark.parametrize("mutation", [
    "ok_true", "ok_zero", "wrong_version", "missing_version", "missing_request_id",
    "invalid_request_id", "retriable_false", "retriable_one", "empty_message",
])
def test_smoke_rejects_malformed_quote_unavailable_contract(monkeypatch, mutation):
    body = _negative_body()
    if mutation == "ok_true":
        body["ok"] = True
    elif mutation == "ok_zero":
        body["ok"] = 0
    elif mutation == "wrong_version":
        body["api_version"] = "unknown"
    elif mutation == "missing_version":
        del body["api_version"]
    elif mutation == "missing_request_id":
        del body["error"]["request_id"]
    elif mutation == "invalid_request_id":
        body["error"]["request_id"] = "ai-"
    elif mutation == "retriable_false":
        body["error"]["retriable"] = False
    elif mutation == "retriable_one":
        body["error"]["retriable"] = 1
    else:
        body["error"]["message"] = ""
    _install_smoke_replies(monkeypatch, 503, body, 3.)
    with pytest.raises(AssertionError):
        smoke.verify_ai_verdict()


@pytest.mark.parametrize("field", [
    "verdict", "management_decision", "selected_management_action",
    "llm_shadow_decision", "unified_edge_ensemble", "active_management_candidates",
    "edge_management", "candidates",
])
def test_smoke_rejects_any_published_result_in_quote_unavailable_response(monkeypatch, field):
    body = _negative_body()
    body[field] = {}
    _install_smoke_replies(monkeypatch, 503, body, 3.)
    with pytest.raises(AssertionError):
        smoke.verify_ai_verdict()
