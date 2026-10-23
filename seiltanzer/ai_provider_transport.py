"""Provider HTTP transport with a total SOCKS handshake deadline."""
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

import httpx

_SOCKS_CLIENT = threading.local()


def _close_socks_client():
    cached = getattr(_SOCKS_CLIENT, 'cached', None)
    _SOCKS_CLIENT.cached = None
    if cached is not None:
        cached[1].close()


def _socks_client(url, proxy, factory, ca_path, curl_options):
    # The single provider worker owns this session and its in-flight request.
    # Never close its handle from the outer deadline/cancellation thread.
    target = urlsplit(url)
    key = (target.scheme, target.netloc, proxy, ca_path, factory)
    cached = getattr(_SOCKS_CLIENT, 'cached', None)
    if cached is None or cached[0] != key:
        _close_socks_client()
        client = factory(trust_env=False, verify=ca_path,
                         curl_options=curl_options, discard_cookies=True)
        _SOCKS_CLIENT.cached = (key, client)
    return _SOCKS_CLIENT.cached[1]


def post_json(url, *, proxy, body, headers, timeout):
    """Keep the existing response contract; bound SOCKS via installed libcurl.

    httpcore's synchronous SOCKS setup omits read/write timeouts. The optional
    curl backend already installed with the market-data client has a total
    transfer timeout, including that handshake. Never fall back to an unbounded
    SOCKS request if the bounded backend is unavailable.
    """
    if not proxy or urlsplit(proxy).scheme.lower() not in {'socks5', 'socks5h'}:
        with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False) as client:
            return client.post(url, json=body, headers=headers)
    try:
        import certifi
        from curl_cffi import CurlOpt
        from curl_cffi import requests as curl_requests
    except ImportError:
        raise httpx.ProxyError('Bounded SOCKS transport unavailable') from None
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    try:
        # libcurl still honors NO_PROXY; curl_cffi's session also reads CA
        # environment variables independently of trust_env. Override both per
        # session without mutating the multithreaded process environment.
        client = _socks_client(url, proxy, curl_requests.Session, certifi.where(),
                               {CurlOpt.NOPROXY: ''})
        response = client.post(url, data=payload, headers=headers, proxy=proxy,
                               timeout=timeout, allow_redirects=False, verify=True,
                               discard_cookies=True)
    except curl_requests.exceptions.Timeout:
        _close_socks_client()
        raise httpx.TimeoutException('SOCKS provider transport deadline exceeded') from None
    except curl_requests.exceptions.RequestException:
        _close_socks_client()
        # Exception messages may include proxy credentials or request details.
        raise httpx.ProxyError('SOCKS provider connection failed') from None
    # libcurl already decoded the body; retaining its wire encoding would make
    # httpx decompress it again. Let httpx calculate the decoded content length.
    response_headers = httpx.Headers(dict(response.headers))
    for name in ('content-encoding', 'content-length', 'transfer-encoding'):
        response_headers.pop(name, None)
    return httpx.Response(response.status_code, content=response.content,
                          headers=response_headers, request=httpx.Request('POST', url))
