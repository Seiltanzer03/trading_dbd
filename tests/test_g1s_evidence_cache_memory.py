import gc
import json
import tracemalloc
from types import SimpleNamespace

from seiltanzer.g1_short_horizon_evidence_nonblocking import _cache_report, _present_report, _present_report_json


def test_cached_report_releases_expanded_input_graph():
    runtime = SimpleNamespace()
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        body = {"rows": [{"i": i, "label": str(i), "context": {"v": [i, i+1], "ok": True}} for i in range(22000)],
                "production_authority": False, "materialization": {"generated_ts": 1.0}}
        _cache_report(runtime, "ablation", body)
        del body
        gc.collect()
        encoded = _present_report_json(runtime, "ablation")
        retained = tracemalloc.get_traced_memory()[0] - before
        assert retained < len(encoded)*3 + 1024**2, (retained, len(encoded))
    finally:
        tracemalloc.stop()
    decoded = json.loads(encoded)
    assert len(decoded["rows"]) == 22000
    assert decoded["rows"][-1] == {"i": 21999, "label": "21999", "context": {"v": [21999, 22000], "ok": True}}
    assert decoded["production_authority"] is False
    assert decoded["materialization"]["age_sec"] > 0


def test_legacy_dict_output_matches_http_without_sharing_mutable_children():
    runtime = SimpleNamespace()
    _cache_report(runtime, "ablation", {"rows": [{"n": 3}], "production_authority": False})
    expected = json.loads(_present_report_json(runtime, "ablation"))
    assert _present_report(runtime, "ablation") == expected
    _present_report(runtime, "ablation")["rows"][0]["n"] = 999
    assert _present_report(runtime, "ablation") == expected


def test_corrupt_legacy_cache_fails_closed_without_database():
    runtime = SimpleNamespace(_g1s_evidence_json_cache={"ablation": (b"broken", None)})
    assert _present_report(runtime, "ablation")["materialized"] is False


def test_http_keeps_preencoded_path_without_decoding(monkeypatch):
    runtime = SimpleNamespace()
    _cache_report(runtime, "ablation", {"rows": [{"n": 3}], "production_authority": False})
    expected = _present_report_json(runtime, "ablation")
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not reconstruct a report graph")
    monkeypatch.setattr(json, "loads", forbidden)
    assert _present_report_json(runtime, "ablation") == expected


def test_invalid_report_fails_closed_for_both_readers():
    runtime = SimpleNamespace()
    _cache_report(runtime, "ablation", {"value": float("nan"), "production_authority": False})
    assert _present_report(runtime, "ablation")["materialized"] is False
    assert json.loads(_present_report_json(runtime, "ablation"))["materialized"] is False
