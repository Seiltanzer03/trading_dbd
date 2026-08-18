import json

import pytest

from seiltanzer import macro_data_factory
from seiltanzer import macro_fomc_extraction_refinement as refinement


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, captured, response_payload, **kwargs):
        self.captured = captured
        self.response_payload = response_payload
        self.captured["client_kwargs"] = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, json=None, headers=None):
        self.captured["url"] = url
        self.captured["body"] = json
        self.captured["headers"] = headers
        return FakeResponse(self.response_payload)


def _valid_semantic():
    return {
        "policy_tone": 0.25,
        "policy_shift": 0.10,
        "inflation_concern": 0.70,
        "growth_concern": 0.35,
        "forward_guidance_shift": 0.15,
        "uncertainty": 0.40,
    }


def test_fomc_v2_requests_strict_six_field_json_schema(monkeypatch):
    captured = {}
    semantic = _valid_semantic()
    provider_payload = {
        "choices": [{"message": {"content": json.dumps(semantic)}}]
    }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        refinement.httpx,
        "Client",
        lambda **kwargs: FakeClient(captured, provider_payload, **kwargs),
    )

    result = refinement._extract_v2("current statement text", "previous statement text", "test/model")

    assert result == semantic
    assert captured["body"]["provider"]["require_parameters"] is True
    response_format = captured["body"]["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == set(semantic)
    assert set(schema["schema"]["properties"]) == set(semantic)


def test_fomc_v2_rejects_provider_object_missing_forward_guidance(monkeypatch):
    captured = {}
    incomplete = _valid_semantic()
    incomplete.pop("forward_guidance_shift")
    provider_payload = {
        "choices": [{"message": {"content": json.dumps(incomplete)}}]
    }

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        refinement.httpx,
        "Client",
        lambda **kwargs: FakeClient(captured, provider_payload, **kwargs),
    )

    with pytest.raises(RuntimeError, match="PROVIDER_SCHEMA_MISMATCH"):
        refinement._extract_v2("current", "previous", "test/model")


def test_install_moves_cache_identity_to_v2_without_mutating_old_rows():
    refinement.install_fomc_extraction_refinement()
    assert macro_data_factory.PROMPT_VERSION == refinement.PROMPT_VERSION_V2
    assert macro_data_factory._openrouter_extract is refinement._extract_v2
    assert macro_data_factory.PROMPT_VERSION != "fomc-semantic-v1"
