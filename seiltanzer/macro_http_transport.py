"""Keep official macro HTTP semantics while bounding SOCKS setup and transfer."""
from __future__ import annotations

import math
from urllib.parse import urlsplit

import httpx


def official_http_client(*, timeout, follow_redirects, proxy, trust_env, headers):
    if not proxy or urlsplit(proxy).scheme.lower() not in {'socks5', 'socks5h'}:
        return httpx.Client(timeout=timeout, follow_redirects=follow_redirects,
                            proxy=proxy, trust_env=trust_env, headers=headers)
    return _SocksMacroClient(timeout, follow_redirects, proxy, headers)


class _SocksMacroClient:
    def __init__(self, timeout, follow_redirects, proxy, headers):
        try:
            import certifi
            from curl_cffi import CurlOpt
            from curl_cffi import requests
        except ImportError:
            raise httpx.ProxyError('Bounded official SOCKS transport unavailable') from None
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.proxy = proxy
        self.headers = dict(headers)
        self.exceptions = requests.exceptions
        self.session = requests.Session(
            trust_env=False, verify=certifi.where(),
            curl_options={CurlOpt.NOPROXY: ''}, discard_cookies=True,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.session.close()

    def get(self, url, **kwargs):
        return self._request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self._request('POST', url, **kwargs)

    def _request(self, method, url, **kwargs):
        timeout = float(kwargs.pop('timeout', self.timeout))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Official macro transfer deadline must be positive and finite')
        try:
            response = self.session.request(
                method, url, proxy=self.proxy, timeout=timeout,
                headers=self.headers, allow_redirects=self.follow_redirects,
                verify=True, discard_cookies=True, **kwargs,
            )
        except self.exceptions.Timeout:
            raise httpx.TimeoutException('Official SOCKS transfer deadline exceeded') from None
        except self.exceptions.RequestException:
            # curl errors may contain the configured proxy's credentials.
            raise httpx.ProxyError('Official SOCKS connection failed') from None
        headers = httpx.Headers(dict(response.headers))
        # curl has already decoded the payload. Do not decompress it twice.
        headers.pop('content-encoding', None)
        headers.pop('content-length', None)
        return httpx.Response(response.status_code, content=response.content,
                              headers=headers,
                              request=httpx.Request(method, str(response.url)))
