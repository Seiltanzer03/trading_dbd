"""Real loopback SOCKS stalls must not monopolize the provider executor."""
import importlib
import json
import socketserver
import threading
import time
from contextlib import contextmanager

import httpx
import pytest


@pytest.fixture(autouse=True)
def close_owned_transport_session(monkeypatch):
    yield
    transport=importlib.import_module('seiltanzer.ai_provider_transport')
    close=getattr(transport,'_close_socks_client',None)
    if close is not None:close()


@contextmanager
def keepalive_socks():
    """Local SOCKS endpoint terminating HTTP, without any external upstream."""
    state={'connections':0,'requests':[]}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(2)
            state['connections']+=1
            version,n=self.rfile.read(2)
            assert version==5
            self.rfile.read(n);self.wfile.write(b'\x05\x00');self.wfile.flush()
            version,command,_,kind=self.rfile.read(4)
            assert (version,command)==(5,1)
            size=self.rfile.read(1)[0] if kind==3 else {1:4,4:16}[kind]
            self.rfile.read(size+2)
            self.wfile.write(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50')
            self.wfile.flush()
            while self.rfile.readline():
                headers={}
                while (line:=self.rfile.readline()) not in (b'\r\n',b''):
                    key,value=line.decode().split(':',1);headers[key.lower()]=value.strip()
                body=self.rfile.read(int(headers.get('content-length','0')))
                state['requests'].append((headers,json.loads(body)))
                if json.loads(body).get('stall'):
                    time.sleep(.3)
                    return
                payload=b'{"ok":true}'
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n'
                    b'Connection: keep-alive\r\nSet-Cookie: private=do-not-replay\r\n\r\n'+payload)
                self.wfile.flush()

    class Server(socketserver.ThreadingTCPServer):daemon_threads=True
    with Server(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        thread.start()
        try:yield f'socks5h://127.0.0.1:{server.server_address[1]}',state
        finally:server.shutdown();thread.join(1)


def test_real_socks_reuses_connection_without_replaying_cookies_or_auth_headers():
    transport=importlib.import_module('seiltanzer.ai_provider_transport')
    with keepalive_socks() as (proxy,state):
        for headers in ({'Authorization':'first-private-key'},{}):
            response=transport.post_json('http://provider.invalid/review',proxy=proxy,
                body={'test':'same route'},headers=headers,timeout=.5)
            assert response.json()=={'ok':True}
        assert state['connections']==1
        assert len(state['requests'])==2
        first,second=[row[0] for row in state['requests']]
        assert first['authorization']=='first-private-key'
        assert 'authorization' not in second and 'cookie' not in second


def test_reused_socks_connection_obeys_each_new_deadline_and_resets_after_failure():
    transport=importlib.import_module('seiltanzer.ai_provider_transport')
    with keepalive_socks() as (proxy,state):
        transport.post_json('http://provider.invalid/review',proxy=proxy,
            body={},headers={},timeout=.5)
        started=time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            transport.post_json('http://provider.invalid/review',proxy=proxy,
                body={'stall':True},headers={},timeout=.05)
        assert time.monotonic()-started<.2
        transport.post_json('http://provider.invalid/review',proxy=proxy,
            body={},headers={},timeout=.5)
        assert state['connections']==2


def test_socks_connection_isolated_by_thread_and_origin():
    transport=importlib.import_module('seiltanzer.ai_provider_transport')
    with keepalive_socks() as (proxy,state):
        def request(url='http://provider.invalid/review'):
            return transport.post_json(url,proxy=proxy,body={},headers={},timeout=.5).json()
        assert request()=={'ok':True}
        from concurrent.futures import ThreadPoolExecutor
        def other_thread():
            try:return request()
            finally:transport._close_socks_client()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(other_thread).result(1)=={'ok':True}
        assert request()=={'ok':True}
        assert state['connections']==2
        assert request('http://different-provider.invalid/review')=={'ok':True}
        assert state['connections']==3


@contextmanager
def stalled_socks():
    release = threading.Event()
    entered = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            entered.set()
            release.wait(1.5)

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


def test_real_socks_handshake_deadline_leaves_next_provider_attempt_available():
    transport = importlib.import_module('seiltanzer.ai_provider_transport')
    with stalled_socks() as (proxy, entered):
        started = time.monotonic()
        for _ in range(2):
            with pytest.raises(httpx.TimeoutException):
                transport.post_json('https://example.invalid', proxy=proxy,
                    body={'probe': 'no external request'}, headers={}, timeout=.1)
        assert entered.is_set()
        assert time.monotonic() - started < 1.0


def test_direct_transport_preserves_existing_httpx_contract(monkeypatch):
    transport = importlib.import_module('seiltanzer.ai_provider_transport')
    captured = []
    expected = object()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {'proxy': None, 'timeout': 8, 'trust_env': False}

        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            captured.append((url, kwargs))
            return expected

    monkeypatch.setattr(httpx, 'Client', Client)
    body, headers = {'model': 'unchanged'}, {'Authorization': 'private-test-key'}
    assert transport.post_json('https://example.invalid', proxy=None,
        body=body, headers=headers, timeout=8) is expected
    assert captured == [('https://example.invalid', {'json': body, 'headers': headers})]


@pytest.mark.parametrize('status', [200, 429])
def test_curl_decoded_body_keeps_json_and_http_status_contract(monkeypatch, status):
    from curl_cffi import requests
    transport = importlib.import_module('seiltanzer.ai_provider_transport')

    class Session:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self): pass
        def post(self, url, **kwargs):
            assert kwargs['timeout'] == .25
            assert kwargs['verify'] is True
            assert kwargs['allow_redirects'] is False
            assert kwargs['proxy'] == 'socks5://private-user:private-pass@localhost:9050'
            assert json.loads(kwargs['data']) == {'model': 'unchanged', 'text': 'тест'}
            assert kwargs['headers']['Authorization'] == 'private-test-key'
            # libcurl decompresses content but retains the wire encoding headers.
            return type('Response', (), {'status_code': status,
                'content': b'{"result":"decoded"}',
                'headers': {'Content-Encoding': 'gzip', 'Content-Length': '123',
                            'X-Request-ID': 'safe-request-id'}})()

    monkeypatch.setattr(requests, 'Session', Session)
    response = transport.post_json('https://example.invalid',
        proxy='socks5://private-user:private-pass@localhost:9050',
        body={'model': 'unchanged', 'text': 'тест'},
        headers={'Authorization': 'private-test-key'}, timeout=.25)
    assert response.json() == {'result': 'decoded'}
    assert response.headers['X-Request-ID'] == 'safe-request-id'
    if status == 429:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            response.raise_for_status()
        assert exc.value.response.status_code == 429
    else:
        response.raise_for_status()


def test_curl_failure_does_not_expose_credentials_or_retry_unbounded_backend(monkeypatch):
    from curl_cffi import requests
    transport = importlib.import_module('seiltanzer.ai_provider_transport')

    class Session:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self): pass
        def post(self, *args, **kwargs):
            raise requests.exceptions.RequestException('private-user:private-pass private-key')

    monkeypatch.setattr(requests, 'Session', Session)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('unbounded fallback'))
    with pytest.raises(httpx.ProxyError) as exc:
        transport.post_json('https://example.invalid', proxy='socks5h://localhost:9050',
                            body={}, headers={}, timeout=.1)
    assert 'private' not in str(exc.value)
    assert exc.value.__suppress_context__


def test_real_curl_options_ignore_ambient_proxy_bypass_and_ca_paths(monkeypatch):
    import certifi
    from curl_cffi import Curl, CurlError, CurlOpt
    transport = importlib.import_module('seiltanzer.ai_provider_transport')
    for name in ('NO_PROXY', 'no_proxy'):
        monkeypatch.setenv(name, '*')
    for name in ('REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE'):
        monkeypatch.setenv(name, '/nonexistent/private-environment-ca.pem')
    options, performed = {}, []
    original_setopt = Curl.setopt

    def setopt(self, option, value):
        options[option] = value
        return original_setopt(self, option, value)

    def perform(self, **kwargs):
        performed.append(dict(options))
        raise CurlError('offline test deadline', 28)

    monkeypatch.setattr(Curl, 'setopt', setopt)
    monkeypatch.setattr(Curl, 'perform', perform)
    with pytest.raises(httpx.TimeoutException):
        transport.post_json('https://example.invalid', proxy='socks5h://localhost:9050',
                            body={}, headers={}, timeout=.1)
    assert len(performed) == 1
    assert performed[0].get(CurlOpt.NOPROXY) in ('', b'')
    assert performed[0][CurlOpt.CAINFO] == certifi.where()
    assert performed[0][CurlOpt.PROXY_CAINFO] == certifi.where()
    assert performed[0].get(CurlOpt.SSL_VERIFYPEER) != 0
    assert performed[0].get(CurlOpt.SSL_VERIFYHOST) != 0
