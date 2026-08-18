from seiltanzer.macro_numeric_data import OfficialNumericMacroSource
from seiltanzer import macro_transport_refinement as transport


def test_macro_proxy_prefers_dedicated_override_and_status_never_leaks_value(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROXY", "http://shared-secret-proxy:1111")
    monkeypatch.setenv("MACRO_HTTP_PROXY", "http://macro-secret-proxy:2222")

    assert transport.macro_proxy_url() == "http://macro-secret-proxy:2222"
    status = transport.macro_transport_status()
    assert status["proxy_configured"] is True
    assert status["proxy_source"] == "MACRO_HTTP_PROXY"
    assert status["official_source_urls_unchanged"] is True
    assert status["payload_or_parser_fallback_added"] is False
    assert "macro-secret-proxy" not in str(status)
    assert "shared-secret-proxy" not in str(status)


def test_numeric_macro_client_uses_refined_transport_only(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("MACRO_HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(transport.httpx, "Client", FakeClient)
    transport.install_macro_transport_refinement()

    client = OfficialNumericMacroSource(timeout_sec=7)._client()
    assert isinstance(client, FakeClient)
    assert captured["proxy"] == "http://proxy.invalid:8080"
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is True
    assert captured["timeout"] == 7.0
    assert captured["headers"]["User-Agent"].startswith("Mozilla/5.0")
