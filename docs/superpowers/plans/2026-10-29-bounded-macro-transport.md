# Bounded official macro transport implementation plan

> Inline implementation under superpowers:executing-plans and TDD. The user
> explicitly requested rapid completion without another planning approval.

**Goal:** An unresponsive SOCKS handshake must not prevent official macro
acquisition from reaching its existing validated off-host fallback.

**Architecture:** Keep the direct/HTTP proxy HTTPX path. For SOCKS only, use
the already installed libcurl backend with a total transfer deadline, the same
TLS trust and an HTTPX response/exception contract. Do not change the inference
provider transport, macro endpoints, parsers, source clocks or fallback order.

**Tech stack:** Python, HTTPX, existing curl_cffi, pytest/native Python3.11 CI.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md` and
`docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`; preserve bounded official acquisition.

## Evidence and constraints

- Main4d35f82 installed; canonical40759291680 failed because numeric refresh
  remained running for120s. Current direct/proxy failures are explicit; expired
  off-host bundles remain rejected. Never accept IN_PROGRESS as successful.
- Installed HTTPcore synchronous SOCKS setup reads without a timeout. Existing
  AI provider already uses libcurl for this reason. Reproduce on loopback first.
- No new sources, paid probes, model/timeout increases, DB transfers, live
  orders, clock rewrites, gate changes or synthetic observations.
- Capacity is an independent operational constraint; source DB19.57GB and
  usable free space0.99GB cannot become ready by ignoring its original guard.

## Task1: preserve macro client semantics with a bounded SOCKS backend

**Files:** new `seiltanzer/macro_http_transport.py`, modify
`seiltanzer/macro_transport_refinement.py`, new
`tests/test_macro_http_transport.py`.
**Interface:** `official_http_client(**kwargs)` accepts the existing client
arguments and returns a context-managed get/post client. SOCKS returns HTTPX
responses and sanitized HTTPX timeout/proxy errors; other routes stay unchanged.

- [x] RED: real loopback stalled SOCKS through the installed source._client
  must finish within0.5s for0.1s timeout; baseline fails.
- [x] Implement only the transport factory and use it at the three existing
  client construction sites; preserve method, JSON, query parameters, original
  URL/final URL, headers, TLS and explicit per-request remaining timeout.
- [ ] Verify response decoding, HTTP errors, session close and credential-safe
  failures, plus existing BLS/ISM/fallback contracts. Native loopback belongs to
  real Python3.11 CI if this host's known native curl crash recurs.
- [ ] One whole-branch independent final review, one important fix pass,
  mandatory full CI and authorized exact-SHA release with actual receipts.

## Review focus

Redirects and decompression must retain one valid response; expired exact-SHA
bundles stay rejected; backend failure must never fall back to unbounded SOCKS;
proxy credentials must not appear in public errors; resource close must run on
success and failure. Native and actual production completion remain separate.

## Verification and final review

- Real baseline regression failed: a0.1s HTTPX source timeout remained in
  SOCKS setup until the0.8s loopback server closed, then raised
  socksio.ProtocolError rather than a deadline exception. No external request.
- Owning macro/source/refresh/off-host/smoke profile:52PASS,1native case
  deselected,1.11s. Native GREEN attempt on this host aborts at the pre-existing
  Python3.12 curl_cffi import BusError(exit135); native Python3.11 CI is required.
  There is no product shim or disabled native test in the branch.
- One independent final review on882e5f4:0Critical,0Important,2Minor.
  No implementation changes or repeated review after that review.
- Deferred minor: accepted submillisecond timeouts can round to libcurl zero;
  existing production callers clamp at100ms, so this is unreachable today.
- Deferred minor: a buffered response may retain transfer-encoding after
  decoding; the current .text/.json source consumers do not re-frame the body.
- Native semantic behavior and full Python3.11/WebKit CI are release gates,
  not established by the mock-backed local profile. Real production completion
  remains required to claim the historical120s failure is resolved.
- Physical capacity is independent: actual root free0.987GB, unchanged minimum
  1.107GB, DB19.573GB, only43MB ext4 reserved. Disposable caches were already
  reclaimed; other applications and the database remain untouched. Do not
  merge into an automatic deploy while its capacity prerequisite is known false.
- Model/source readiness, profitability and trading authority remain outside
  this transport patch. Current matrix is58/80 trained noncrypto families;
  missing event/order-flow history remains explicit.
