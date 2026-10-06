import hashlib
import json
from types import SimpleNamespace

import pytest

from seiltanzer.unified_edge_runtime_context import VERSION, attach_unified_edge_context, load_unified_edge_context
from test_expert_registry import registry
from test_unified_edge_ensemble import snapshot

SHA = "a" * 40


def write_context(tmp_path, value):
    path = tmp_path / "context.json"
    raw = json.dumps(value, allow_nan=False).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def document(frozen):
    return {"version": VERSION, "deployment_sha": SHA, "captured_ts": frozen["captured_ts"],
            "instruments": {"NAS100": {"expert_registry": registry(frozen)}}}


def test_pinned_registry_is_used_by_working_request_hook(tmp_path):
    frozen = snapshot()
    path, digest = write_context(tmp_path, document(frozen))
    engine = SimpleNamespace(settings=SimpleNamespace(unified_edge_context_path=str(path),
                                                       unified_edge_context_sha256=digest))
    attach_unified_edge_context(engine, frozen, expected_sha=SHA)
    assert frozen['runtime_code_sha'] == SHA
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_10"
    assert len(audit["counterfactuals"]) == 7
    assert frozen["execution_cost_context_audit"]["reason"] == "BROKER_EXECUTION_COST_CONTEXT_UNCONFIGURED"


@pytest.mark.parametrize("defect", ["hash", "sha", "future", "instrument", "oversize", "registry_budget"])
def test_invalid_import_never_adds_experts(tmp_path, defect):
    frozen = snapshot()
    value = document(frozen)
    if defect == "sha": value["deployment_sha"] = "b" * 40
    elif defect == "future": value["captured_ts"] += 1
    elif defect == "instrument": value["instruments"] = {}
    elif defect == "oversize": value["padding"] = "x" * 48_000
    elif defect == "registry_budget": value["instruments"]["NAS100"]["expert_registry"]["schemes"]["balanced"]["liquidity_model"] += .5
    path, digest = write_context(tmp_path, value)
    if defect == "hash": digest = "0" * 64
    context, audit = load_unified_edge_context(path, expected_document_sha256=digest, expected_sha=SHA, snapshot=frozen)
    assert "expert_registry" not in context
    assert audit["available"] is False


def test_invalid_economics_cannot_publish_or_supersede_pending_work():
    from seiltanzer.app import _publish_unified_review
    with pytest.raises(ValueError, match="invalid common economics"):
        _publish_unified_review(None, {}, {}, "review", {}, {"common_economics_invalid": True})


def test_intraday_archive_hook_has_one_existing_refresh_and_one_local_record():
    from seiltanzer.app import _refresh_intraday_archive
    calls = []
    market = SimpleNamespace(refresh_intraday=lambda: calls.append("existing_refresh"))
    engine = SimpleNamespace(market=market, passive=SimpleNamespace(
        record_configured_intraday_archive=lambda feed: calls.append(feed)))
    _refresh_intraday_archive(engine)
    assert calls == ["existing_refresh", market]
