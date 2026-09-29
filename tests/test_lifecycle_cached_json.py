import json
from types import SimpleNamespace

from seiltanzer import llm_edge_lifecycle as lifecycle


def test_http_lifecycle_read_does_not_parse_large_cached_document(monkeypatch):
    runtime = SimpleNamespace()
    payload = json.dumps({"status": "OK", "candidates": [{"evidence": "x" * 100_000}]})
    lifecycle.publish_materialized_lifecycle_cache(runtime, payload)

    def fail_parse(*args, **kwargs):
        raise AssertionError("request path parsed the full lifecycle JSON")

    monkeypatch.setattr(lifecycle.json, "loads", fail_parse)
    assert lifecycle.read_cached_materialized_lifecycle_json(runtime) == payload


def test_invalid_publication_cannot_expose_malformed_json():
    runtime = SimpleNamespace()
    lifecycle.publish_materialized_lifecycle_cache(runtime, '{"broken":')
    payload = json.loads(lifecycle.read_cached_materialized_lifecycle_json(runtime))
    assert payload["status"] == "INITIALIZING"
