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


def test_bls_transport_tries_direct_before_configured_proxy(monkeypatch):
    calls = []
    sleeps = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, json):
            calls.append((self.kwargs, url, json))
            if self.kwargs["proxy"] is None:
                return Response({"status": "REQUEST_NOT_SUCCEEDED"})
            return Response({"status": "REQUEST_SUCCEEDED", "Results": {"series": []}})

    expected = {"CPI": {"period": "2026-07"}, "NFP": {"period": "2026-07"}}
    monkeypatch.setenv("MACRO_HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(transport.httpx, "Client", Client)
    monkeypatch.setattr(transport.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        "seiltanzer.macro_numeric_data.build_bls_releases",
        lambda payload: expected
        if payload.get("status") == "REQUEST_SUCCEEDED"
        else (_ for _ in ()).throw(ValueError("BLS_REQUEST_NOT_SUCCEEDED")),
    )

    _fetched_at, releases = transport._fetch_bls_official_with_failover(
        OfficialNumericMacroSource(timeout_sec=7),
        now=1_787_220_000.0,
    )

    assert releases == expected
    assert [call[0]["proxy"] for call in calls] == [
        None,
        None,
        "http://proxy.invalid:8080",
    ]
    assert all(call[0]["trust_env"] is False for call in calls)
    assert all(call[0]["timeout"] == 7.0 for call in calls)
    assert all(call[1] == "https://api.bls.gov/publicAPI/v2/timeseries/data/" for call in calls)
    assert all(len(call[2]["seriesid"]) == 7 for call in calls)
    assert sleeps == [transport.BLS_TRANSPORT_RETRY_BACKOFF_SEC]


def test_bls_transport_exhaustion_never_returns_unvalidated_payload(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "REQUEST_NOT_SUCCEEDED"}

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs["proxy"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, json):
            assert json["seriesid"]
            return Response()

    monkeypatch.delenv("MACRO_HTTP_PROXY", raising=False)
    monkeypatch.delenv("OPENROUTER_PROXY", raising=False)
    monkeypatch.setattr(transport.httpx, "Client", Client)
    monkeypatch.setattr(transport.time, "sleep", lambda _seconds: None)

    import pytest

    with pytest.raises(ValueError, match="BLS_OFFICIAL_TRANSPORT_EXHAUSTED"):
        transport._fetch_bls_official_with_failover(
            OfficialNumericMacroSource(timeout_sec=7),
            now=1_787_220_000.0,
        )

    assert calls == [None, None]
