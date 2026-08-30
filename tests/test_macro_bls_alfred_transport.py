from __future__ import annotations

import httpx

from seiltanzer import macro_bls_alfred_vintage as alfred
from seiltanzer import macro_transport_refinement


def test_alfred_retries_bounded_transient_read_timeout(monkeypatch):
    calls = []
    sleeps = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url):
            calls.append(url)
            if len(calls) < 3:
                raise httpx.ReadTimeout("slow official response")
            return "official"

    source = alfred.OfficialALFREDBLSVintageSource(
        fetch_attempts=3, retry_delay_sec=0.25
    )
    monkeypatch.setattr(source, "_client", lambda **_kwargs: Client())
    monkeypatch.setattr(source, "_validated", lambda response: response)
    monkeypatch.setattr(macro_transport_refinement, "macro_proxy_url", lambda: None)
    monkeypatch.setattr(alfred.time, "sleep", sleeps.append)

    url = alfred.alfred_series_url(
        "CPIAUCSL", period="2026-07", vintage_date="2026-08-12"
    )
    assert source._fetch(url) == "official"
    assert calls == [url, url, url]
    assert sleeps == [0.25, 0.5]


def test_alfred_does_not_retry_deterministic_validation_failure(monkeypatch):
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url):
            calls.append(url)
            return "invalid"

    source = alfred.OfficialALFREDBLSVintageSource(fetch_attempts=3)
    monkeypatch.setattr(source, "_client", lambda **_kwargs: Client())
    monkeypatch.setattr(
        source, "_validated",
        lambda _response: (_ for _ in ()).throw(ValueError("invalid evidence")),
    )
    monkeypatch.setattr(macro_transport_refinement, "macro_proxy_url", lambda: None)

    url = alfred.alfred_series_url(
        "PAYEMS", period="2026-07", vintage_date="2026-08-07"
    )
    try:
        source._fetch(url)
    except ValueError as exc:
        assert "DIRECT_OFFICIAL:ValueError" in str(exc)
    else:
        raise AssertionError("deterministic validation failure must fail closed")
    assert calls == [url]
