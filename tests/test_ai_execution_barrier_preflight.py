"""Reached execution barriers require broker reconciliation, not a new forecast."""
from copy import deepcopy

import pytest

import seiltanzer.app as app_module
from seiltanzer import ai_report_semantics_guard as guard
from seiltanzer.ai_api import AI_API_VERSION
from seiltanzer import ai_verdict_base as producer
from test_ai_authoritative_price_preflight import smoke, _install_smoke_replies
from test_ai_verdict_api import _client, _snapshot


def snapshot(short=False, current=125.):
    result = _snapshot()
    result["trade_geometry"] = {
        "entry": 100., "original_stop": 110. if short else 90.,
        "active_risk_barrier": 105. if short else 95.,
        "final_take": 75. if short else 125., "current": current,
        # Deliberately wrong rounded coordinates must never own this decision.
        "current_r": 0.,
    }
    result["policy_manager"]["input_audit"] = {"rows": {"instrument_price": {
        "available": True, "status": "live", "source": "executing-broker",
        "production_authority": True}}}
    return result


def body(kind="take", current_r=2.5, barrier_r=2.5):
    return {"ok": False, "api_version": AI_API_VERSION, "error": {
        "code": "execution_barrier_reached", "message": "Проверьте исполнение у брокера",
        "request_id": "ai-0123456789abcdef0123", "retriable": False},
        "execution_barrier": {"kind": kind, "current_r": current_r,
            "barrier_r": barrier_r, "price_authority": True,
            "execution_confirmed": False}}


@pytest.mark.parametrize("short,current,kind", [
    (False, 125., "take"), (False, 126., "take"),
    (False, 95., "stop"), (False, 94., "stop"),
    (True, 75., "take"), (True, 74., "take"),
    (True, 105., "stop"), (True, 106., "stop"),
])
def test_reached_barrier_returns_before_mutation_provider_and_rate_consumption(
        tmp_path, monkeypatch, short, current, kind):
    def forbidden(*args, **kwargs):
        raise AssertionError("barrier must return before downstream work")
    app, client = _client(tmp_path, monkeypatch, forbidden)
    frozen = snapshot(short, current)
    monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: deepcopy(frozen))
    before = app.state.engine.journal.active_trade()
    before_events = app.state.engine.position.events(before["id"])
    before_actions = app.state.engine.position.shadow_actions(before["id"])
    for owner, name in [
        (app.state.engine.position, "sync_be"),
        (app_module, "_refresh_management_decision"),
        (app_module, "_attach_family_source_bundle"),
        (app_module, "_publish_unified_review"),
        (app_module, "render_policy_report"),
        (app.state.engine.journal, "record_ai_verdict"),
    ]:
        monkeypatch.setattr(owner, name, forbidden)
    try:
        for _ in range(2):
            response = client.post("/api/ai/verdict")
            assert response.status_code == 400
            result = response.json()
            assert isinstance(result["error"]["message"], str) and result["error"]["message"].strip()
            expected = body(kind, (100.-current)/10. if short else (current-100.)/10.,
                            2.5 if kind == "take" else -.5)
            expected["error"]["request_id"] = result["error"]["request_id"]
            expected["error"]["message"] = result["error"]["message"]
            assert result == expected
        assert app.state.engine.journal.active_trade() == before
        assert app.state.engine.position.events(before["id"]) == before_events
        assert app.state.engine.position.shadow_actions(before["id"]) == before_actions
    finally:
        app.state.engine.close()


@pytest.mark.parametrize("short,current", [(False,124.999999), (False,95.000001),
                                           (True,75.000001), (True,104.999999)])
def test_inside_barrier_retains_provider_success(tmp_path, monkeypatch, short, current):
    calls = []
    def provider(frozen):
        calls.append(frozen)
        return {"verdict": "LLM", "model": "test-model"}
    app, client = _client(tmp_path, monkeypatch, provider)
    monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: snapshot(short,current))
    try:
        assert client.post("/api/ai/verdict").status_code == 200
        assert len(calls) == 1
    finally:
        app.state.engine.close()


@pytest.mark.parametrize("current,expected_status", [
    (124.999999, 200), (90.000001, 200),
    (125., 400), (125.000001, 400), (90., 400), (89.999999, 400),
])
def test_canonical_snapshot_producer_preserves_inside_boundary_for_api(
        tmp_path, monkeypatch, current, expected_status):
    calls = []
    def provider(frozen):
        calls.append(frozen)
        return {"verdict": "LLM", "model": "test-model"}
    app, client = _client(tmp_path, monkeypatch, provider)
    engine = app.state.engine
    trade = engine.journal.active_trade()
    tick = {"instrument": "NAS100", "trade": trade,
            "feeds": {"price": {"value": current}},
            "prob": {"r": (current - 100.) / 10., "T": 2.5}}
    monkeypatch.setattr(engine, "canonical_tick_payload", lambda: deepcopy(tick))
    monkeypatch.setattr(producer, "analyze_policies", lambda *args, **kwargs:
                        deepcopy(snapshot()["policy_manager"]))
    try:
        # Exercise the real machine geometry producer, not an injected geometry.
        frozen = producer.build_snapshot(engine)
        assert frozen["trade_geometry"]["current"] == current
        monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: deepcopy(frozen))
        response = client.post("/api/ai/verdict")
        assert response.status_code == expected_status
        assert len(calls) == (1 if expected_status == 200 else 0)
        if expected_status == 400:
            assert response.json()["error"]["code"] == "execution_barrier_reached"
    finally:
        engine.close()


@pytest.mark.parametrize("variant", ["missing", "nan", "infinity", "bool", "string",
    "huge_integer", "zero_risk", "negative", "reversed", "undeclared", "untrusted"])
def test_incomplete_or_invalid_geometry_never_proves_barrier(variant):
    frozen = snapshot()
    geometry = frozen["trade_geometry"]
    if variant == "missing": del geometry["active_risk_barrier"]
    elif variant == "nan": geometry["entry"] = float("nan")
    elif variant == "infinity": geometry["final_take"] = float("inf")
    elif variant == "bool": geometry["entry"] = True
    elif variant == "string": geometry["entry"] = "100"
    elif variant == "huge_integer": geometry["entry"] = 10**1000
    elif variant == "zero_risk": geometry["original_stop"] = 100.
    elif variant == "negative": geometry["active_risk_barrier"] = -1.
    elif variant == "reversed": geometry["final_take"] = 94.
    elif variant == "undeclared": del frozen["policy_manager"]["input_audit"]
    else: frozen["policy_manager"]["input_audit"]["rows"]["instrument_price"]["production_authority"] = False
    helper = getattr(guard, "execution_barrier_reached", None)
    assert callable(helper), "execution barrier preflight is missing"
    assert helper(frozen) is None


@pytest.mark.parametrize("kind,current,barrier", [("take",2.5,2.5),("stop",-.6,-.5)])
def test_smoke_accepts_exact_barrier_contract_after_positive_actions(monkeypatch,kind,current,barrier):
    calls = _install_smoke_replies(monkeypatch,400,body(kind,current,barrier),3.)
    smoke.verify_ai_verdict()
    assert calls == ["isolated_12_action_contract"]


@pytest.mark.parametrize("variant", ["authority", "confirmed", "nan", "bool", "string",
    "inside_take", "inside_stop", "kind", "extra", "proof_extra", "proof_missing",
    "proof_null", "wrong_version", "empty_message", "ok_zero", "retriable", "request_id",
    "slow", "422"])
def test_smoke_rejects_invalid_barrier_negative(monkeypatch,variant):
    result = body()
    proof = result["execution_barrier"]
    if variant == "authority": proof["price_authority"] = 1
    elif variant == "confirmed": proof["execution_confirmed"] = 0
    elif variant == "nan": proof["current_r"] = float("nan")
    elif variant == "bool": proof["current_r"] = True
    elif variant == "string": proof["current_r"] = "2.5"
    elif variant == "inside_take": proof["current_r"] = 2.4
    elif variant == "inside_stop": proof.update(kind="stop",current_r=0.,barrier_r=-.5)
    elif variant == "kind": proof["kind"] = "other"
    elif variant == "extra": result["verdict"] = "published"
    elif variant == "proof_extra": proof["decision_id"] = "published"
    elif variant == "proof_missing": del proof["barrier_r"]
    elif variant == "proof_null": result["execution_barrier"] = None
    elif variant == "wrong_version": result["api_version"] = "wrong"
    elif variant == "empty_message": result["error"]["message"] = " "
    elif variant == "ok_zero": result["ok"] = 0
    elif variant == "retriable": result["error"]["retriable"] = True
    elif variant == "request_id": result["error"]["request_id"] = "invalid"
    _install_smoke_replies(monkeypatch,422 if variant=="422" else 400,result,
                          12000. if variant=="slow" else 3.)
    with pytest.raises(AssertionError): smoke.verify_ai_verdict()
