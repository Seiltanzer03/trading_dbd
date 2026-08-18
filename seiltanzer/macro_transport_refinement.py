"""Production transport hardening for official BLS/ISM acquisition.

The production VPS can receive HTTP 403 from otherwise public official endpoints
while the same deterministic fetch succeeds from GitHub-hosted runners.  Keep the
source URLs, parsers, provenance checks and payloads unchanged; only the outbound
HTTP transport is refined.  When configured, the numeric macro client reuses the
terminal's outbound proxy.  A browser-compatible User-Agent is used in both modes
to avoid rejecting the official request merely because it advertises a bot name.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


TRANSPORT_VERSION = "macro-official-transport-v2"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_INSTALLED = False


def macro_proxy_url() -> str | None:
    """Resolve transport only; never expose the proxy value in public status."""
    return (
        os.environ.get("MACRO_HTTP_PROXY", "").strip()
        or os.environ.get("OPENROUTER_PROXY", "").strip()
        or None
    )


def macro_transport_status() -> dict[str, Any]:
    return {
        "contract_version": TRANSPORT_VERSION,
        "proxy_configured": macro_proxy_url() is not None,
        "proxy_source": (
            "MACRO_HTTP_PROXY" if os.environ.get("MACRO_HTTP_PROXY", "").strip()
            else "OPENROUTER_PROXY" if os.environ.get("OPENROUTER_PROXY", "").strip()
            else None
        ),
        "official_source_urls_unchanged": True,
        "payload_or_parser_fallback_added": False,
        "research_only": True,
        "production_authority": False,
    }


def install_macro_transport_refinement() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .macro_numeric_data import OfficialNumericMacroSource

    def refined_client(self: OfficialNumericMacroSource) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_sec,
            follow_redirects=True,
            proxy=macro_proxy_url(),
            trust_env=False,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
                "Cache-Control": "no-cache",
            },
        )

    OfficialNumericMacroSource._client = refined_client
    _INSTALLED = True
