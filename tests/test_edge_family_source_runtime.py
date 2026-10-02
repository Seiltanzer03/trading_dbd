import json
import os
from types import SimpleNamespace

import pytest

from seiltanzer.edge_family_adapters import FAMILIES
from seiltanzer.edge_family_source_runtime import (
    CONTRACT, POLICY, PUBLICATION, MAX_BYTES, MAX_SELECTED_BYTES,
    load_family_source_context,
)

T0 = 1_800_000_000.0
SHA = "a" * 40


def bundle():
    source = {"source_id": "actual-related-returns", "source_verified": True,
              "instrument": "BTCUSD", "observed_ts": T0 - 5,
              "received_ts": T0 - 4, "quality": .8,
              "linked_returns": [{"leader": "ETHUSD", "start_ts": T0 - 305,
                                  "end_ts": T0 - 5, "start_price": 100, "end_price": 101}]}
    return {"contract_version": CONTRACT, "edge_policy": POLICY,
            "production_authority": False, "publication_contract_version": PUBLICATION,
            "published_for_sha": SHA, "captured_ts": T0 - 3,
            "instruments": {"BTCUSD": {"edge_family_sources": {"intermarket": [source]},
                "readiness": {family: {"available": family == "intermarket",
                                       "reason": "MEASURED_FACTS_NO_CALIBRATED_HEAD",
                                       "needs_data": ["observed net-action head"]}
                              for family in FAMILIES}}}}


def write_bundle(tmp_path, payload, *, mtime=T0 - 2):
    path = tmp_path / "research" / "edge_family_sources_latest.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload))
    os.utime(path, (mtime, mtime))
    return path


def load(tmp_path, **snapshot):
    return load_family_source_context(SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)),
        {"captured_ts": T0, "strategy": {"instrument": "BTCUSD"}, **snapshot}, SHA)


def test_exact_sha_local_admission_and_immutable_cached_copy(tmp_path):
    write_bundle(tmp_path, bundle())
    result = load(tmp_path)
    assert result["edge_family_source_bundle_audit"]["available"] is True
    assert result["edge_family_source_bundle_audit"]["models_loaded"] == 0
    assert set(result["edge_family_source_bundle_audit"]["families"]) == set(FAMILIES)
    result["edge_family_sources"]["intermarket"][0]["source_id"] = "mutated"
    assert load(tmp_path)["edge_family_sources"]["intermarket"][0]["source_id"] == "actual-related-returns"


@pytest.mark.parametrize("field,value", [("published_for_sha", "b" * 40),
    ("production_authority", True), ("contract_version", "unknown"),
    ("edge_policy", "unknown"), ("publication_contract_version", "unknown"),
    ("captured_ts", T0 + 1), ("captured_ts", T0 - 8 * 3600 - 1)])
def test_contract_capture_and_generation_fail_closed(tmp_path, field, value):
    payload = bundle()
    payload[field] = value
    write_bundle(tmp_path, payload)
    result = load(tmp_path)
    assert result["edge_family_sources"] == {}
    assert result["edge_family_source_bundle_audit"]["available"] is False


@pytest.mark.parametrize("mtime", [T0 + 1, T0 - 8 * 3600 - 1])
def test_file_timestamp_must_be_available_and_fresh(tmp_path, mtime):
    write_bundle(tmp_path, bundle(), mtime=mtime)
    assert load(tmp_path)["edge_family_source_bundle_audit"]["available"] is False


def test_missing_file_or_bad_snapshot_has_no_network_side_effect(tmp_path):
    assert load(tmp_path)["edge_family_sources"] == {}
    for value in (None, True, float("nan"), float("inf"), -1):
        result = load(tmp_path, captured_ts=value)
        assert result["edge_family_source_bundle_audit"]["reason"] == "SOURCE_SNAPSHOT_TIME_UNAVAILABLE"
        assert result["edge_family_source_bundle_audit"]["network_calls"] is False


@pytest.mark.parametrize("change,reason", [
    ({"instrument": "COINBASEBTC-USD", "proxy_mapping": {"validated": False}}, "INSTRUMENT_OR_PROXY_MAPPING_UNVALIDATED"),
    ({"observed_ts": T0 + 1}, "SOURCE_AFTER_SNAPSHOT"),
    ({"observed_ts": T0 - 1000}, "SOURCE_STALE"),
    ({"source_verified": False}, "SOURCE_UNAVAILABLE_OR_UNVERIFIED"),
    ({"hidden": {"received_ts": T0 + 1}}, "FUTURE_FACT_OR_UNSUPPORTED_PLANNED_SCHEDULE"),
])
def test_rejected_facts_remain_audit_only(tmp_path, change, reason):
    payload = bundle()
    payload["instruments"]["BTCUSD"]["edge_family_sources"]["intermarket"][0].update(change)
    write_bundle(tmp_path, payload)
    result = load(tmp_path)
    assert result["edge_family_sources"] == {}
    assert result["edge_family_source_bundle_audit"]["rejected_sources"][0]["reason"] == reason


def test_file_and_selected_record_bounds(tmp_path):
    payload = bundle()
    payload["instruments"]["BTCUSD"]["edge_family_sources"]["intermarket"][0]["unused"] = "x" * MAX_SELECTED_BYTES
    path = write_bundle(tmp_path, payload)
    assert load(tmp_path)["edge_family_source_bundle_audit"]["reason"] == "SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET"
    path.write_text("x" * (MAX_BYTES + 1))
    os.utime(path, (T0 - 1, T0 - 1))
    assert load(tmp_path)["edge_family_source_bundle_audit"]["reason"] == "SOURCE_BUNDLE_EXCEEDS_BOUND"


def test_bundle_freeze_precedes_all_information_and_nested_future_is_strict(tmp_path):
    payload = bundle()
    payload["captured_ts"] = T0 - 20
    write_bundle(tmp_path, payload)
    assert not load(tmp_path)["edge_family_sources"]
    payload = bundle()
    payload["instruments"]["BTCUSD"]["edge_family_sources"]["intermarket"][0]["hidden"] = {"end_ts": T0 - 2.5}
    write_bundle(tmp_path, payload, mtime=T0 - 1)
    assert not load(tmp_path)["edge_family_sources"]


def test_collected_available_is_not_runtime_freshness(tmp_path):
    payload = bundle()
    payload["captured_ts"] = T0 - 1000
    source = payload["instruments"]["BTCUSD"]["edge_family_sources"]["intermarket"][0]
    source.update(observed_ts=T0 - 1005, received_ts=T0 - 1004)
    write_bundle(tmp_path, payload)
    row = load(tmp_path)["edge_family_source_bundle_audit"]["families"]["intermarket"]
    assert row["collected_available"] is True
    assert row["available"] is False and row["loaded_record_n"] == 0


def test_pathological_json_does_not_escape_fail_closed(tmp_path, monkeypatch):
    write_bundle(tmp_path, bundle())
    def recurse(*args):
        raise RecursionError("too deep")
    monkeypatch.setattr("seiltanzer.edge_family_source_runtime._read", recurse)
    assert load(tmp_path)["edge_family_source_bundle_audit"]["available"] is False
