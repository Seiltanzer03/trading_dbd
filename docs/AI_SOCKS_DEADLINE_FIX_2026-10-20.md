# AI provider SOCKS deadline — 2026-10-20

## Confirmed production defect

On main `6e4275906d248b9d16c4c20fd2d090f0f99d4033`, request
`ai-5636068f4b484d3b9b20` returned deterministic fallback after its existing
six-second provider guard. Correlated telemetry later recorded **119,685.545 ms**
inside `provider_http`, ending in `ProxyError`. Rendering and preparation took
2.209 ms and 2.539 ms; the request was 26,219 bytes. This identifies a blocked
transport, rather than proving slow model generation or an oversized snapshot.

The installed synchronous httpcore SOCKS setup calls stream reads/writes without
their timeout argument. The outer future timeout cannot stop a running worker;
the sole provider worker can therefore remain occupied after the public fallback.

The existing authenticated external proxy refused connections. The existing local
Tor route was confirmed active in the service environment. A separate bounded
probe, run `39423908207`, reached the configured `openai/gpt-4o-mini` through the
already installed `curl_cffi` 0.15.0: HTTP 200 model response in 1,376.4 ms
(10 input / 2 output tokens). This is connectivity evidence, not acceptance of a
complete structured trade review.

## Bounded correction

Only SOCKS provider requests use the existing optional libcurl client, whose
total transfer timeout also covers the proxy handshake. Direct and HTTP-proxy
requests retain their existing httpx path. Missing libcurl fails explicitly;
there is no fallback to an unbounded SOCKS transport. No new dependency, endpoint,
model, retry, timeout increase or provider is introduced.

TLS verification stays enabled; ambient proxy settings and redirects are disabled.
The response adapter preserves JSON, HTTP status handling and telemetry; it removes
wire compression/length headers because libcurl has already decompressed the body.
Proxy errors use fixed messages without credentials or request contents.

The existing six-second outer guard, eight-second transport setting, circuit,
structured response validation, deterministic fallback, risk gates, source
admission and manual execution contracts are unchanged.

## Verification and acceptance boundary

- Real loopback SOCKS server withholding its handshake: two consecutive requests
  time out and return in under one second with a 100 ms transport setting each.
- Decoded compressed responses preserve successful JSON and HTTP 429 handling;
  connection errors neither disclose credentials nor retry through httpx.
- Actual structured report path exercised through the SOCKS adapter, including
  unchanged output validation, telemetry and exact request-byte accounting.
- Focused provider profile: **25 passed in 1.82 s**. The handshake regression was
  observed failing before the helper existed; compressed-response regressions
  failed before the adapter removed the wire headers.

Full mandatory CI and exact-SHA production structured-review acceptance are still
required. A tiny model probe does not establish a nonzero current-LLM contribution,
availability of eight edge families or complete production readiness. Disk
admission and current-history cutover remain separate outstanding work.
