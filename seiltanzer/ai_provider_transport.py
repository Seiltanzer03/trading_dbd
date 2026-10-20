"""Provider HTTP transport with a total SOCKS handshake deadline."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx


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
        from curl_cffi import requests as curl_requests
    except ImportError:
        raise httpx.ProxyError('Bounded SOCKS transport unavailable') from None
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    try:
        with curl_requests.Session(trust_env=False) as client:
            response = client.post(url, data=payload, headers=headers, proxy=proxy,
                                   timeout=timeout, allow_redirects=False, verify=True)
    except curl_requests.exceptions.Timeout:
        raise httpx.TimeoutException('SOCKS provider transport deadline exceeded') from None
    except curl_requests.exceptions.RequestException:
        # Exception messages may include proxy credentials or request details.
        raise httpx.ProxyError('SOCKS provider connection failed') from None
    # libcurl already decoded the body; retaining its wire encoding would make
    # httpx decompress it again. Let httpx calculate the decoded content length.
    response_headers = httpx.Headers(dict(response.headers))
    for name in ('content-encoding', 'content-length', 'transfer-encoding'):
        response_headers.pop(name, None)
    return httpx.Response(response.status_code, content=response.content,
                          headers=response_headers, request=httpx.Request('POST', url))
