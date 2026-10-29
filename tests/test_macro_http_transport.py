"""Official source transport must honor the timeout during SOCKS setup."""
from contextlib import contextmanager
import socketserver
import threading
import time
import sys
import types

import httpx
import pytest


@contextmanager
def stalled_socks():
    release = threading.Event()
    entered = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            entered.set()
            release.wait(.8)

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            yield f'socks5h://127.0.0.1:{server.server_address[1]}', entered
        finally:
            release.set()
            server.shutdown()
            thread.join(1)


def test_real_macro_socks_handshake_respects_remaining_timeout(monkeypatch):
    from seiltanzer.macro_numeric_data import OfficialNumericMacroSource
    from seiltanzer.macro_transport_refinement import install_macro_transport_refinement

    install_macro_transport_refinement()
    with stalled_socks() as (proxy, entered):
        monkeypatch.setenv('MACRO_HTTP_PROXY', proxy)
        source = OfficialNumericMacroSource()
        source.timeout_sec = .1
        started = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            with source._client() as client:
                client.get('https://official-source.invalid/no-external-request')
        assert entered.is_set()
        assert time.monotonic() - started < .5


@pytest.fixture
def curl_backend(monkeypatch):
    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    calls = []

    class Session:
        def __init__(self, **kwargs):
            self.closed = False
            self.error = None
            self.response = types.SimpleNamespace(
                status_code=200, content=b'{"official":true}',
                headers={'content-encoding': 'gzip', 'content-length': '999'},
                url='https://www.ismworld.org/final-report',
            )
            calls.append((self, kwargs))

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if self.error:
                raise self.error
            return self.response

        def close(self):
            self.closed = True

    module = types.ModuleType('curl_cffi')
    module.CurlOpt = types.SimpleNamespace(NOPROXY=10177)
    module.requests = types.SimpleNamespace(
        Session=Session,
        exceptions=types.SimpleNamespace(Timeout=Timeout, RequestException=RequestException),
    )
    monkeypatch.setitem(sys.modules, 'curl_cffi', module)
    return calls, module.requests.exceptions


def client(**kwargs):
    from seiltanzer.macro_http_transport import official_http_client
    return official_http_client(
        timeout=.3, follow_redirects=True, proxy='socks5h://user:private@localhost:9050',
        trust_env=False, headers={'User-Agent': 'existing official-source client'}, **kwargs,
    )


def test_socks_preserves_official_query_json_decoded_response_and_tls(curl_backend):
    calls, _ = curl_backend
    with client() as transport:
        response = transport.get('https://api.bls.gov/series',
                                 params={'startyear': '2025'}, timeout=.1)
        assert response.json() == {'official': True}
        assert str(response.url) == 'https://www.ismworld.org/final-report'
        assert response.request.method == 'GET'
        assert 'content-encoding' not in response.headers
        assert response.headers['content-length'] == str(len(response.content))
        transport.post('https://api.bls.gov/series', json={'seriesid': ['unchanged']})
    session, options = calls[0]
    assert session.closed
    assert options['trust_env'] is False and options['verify']
    assert options['curl_options'] == {10177: ''}
    assert calls[1][2]['params'] == {'startyear': '2025'}
    assert calls[1][2]['timeout'] == .1
    assert calls[1][2]['verify'] is True and calls[1][2]['allow_redirects'] is True
    assert calls[1][2]['discard_cookies'] is True
    assert calls[2][2]['json'] == {'seriesid': ['unchanged']}
    assert calls[2][2]['timeout'] == .3


@pytest.mark.parametrize('kind', ['Timeout', 'RequestException'])
def test_socks_failure_is_sanitized_and_closes_without_unbounded_retry(curl_backend, kind):
    calls, exceptions = curl_backend
    with pytest.raises(httpx.TimeoutException if kind == 'Timeout' else httpx.ProxyError) as error:
        with client() as transport:
            transport.session.error = getattr(exceptions, kind)('user:private proxy details')
            transport.get('https://www.ismworld.org/report')
    assert calls[0][0].closed and len(calls) == 2
    assert 'private' not in str(error.value) and 'user:' not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_socks_preserves_http_status_for_existing_fallback(curl_backend):
    with client() as transport:
        transport.session.response.status_code = 503
        response = transport.get('https://www.ismworld.org/report')
        with pytest.raises(httpx.HTTPStatusError):
            response.raise_for_status()


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf')])
def test_socks_never_disables_the_total_deadline(curl_backend, timeout):
    calls, _ = curl_backend
    with client() as transport, pytest.raises(ValueError, match='deadline'):
        transport.get('https://www.ismworld.org/report', timeout=timeout)
    assert len(calls) == 1 and calls[0][0].closed


def test_missing_bounded_backend_never_uses_unbounded_httpx(monkeypatch):
    monkeypatch.setitem(sys.modules, 'curl_cffi', None)
    with pytest.raises(httpx.ProxyError, match='Bounded official SOCKS transport unavailable'):
        client()
