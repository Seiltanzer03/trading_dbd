"""Strict ingestion contract for immutable deterministic FOMC releases.

The base parser can measure a standalone statement, but rate/text deltas require
the immediately previous official statement.  Production archive materialization
uses this store so a transient failure on the previous page cannot permanently
freeze the current release with missing derivative fields.
"""
from __future__ import annotations

from typing import Any

from .macro_fomc_deterministic_bootstrap import (
    FOMCDeterministicReleaseStore,
    FOMCStatementSpec,
)


STRICT_FOMC_STORE_CONTRACT_VERSION = "fomc-deterministic-strict-previous-v1"


class StrictFOMCDeterministicReleaseStore(FOMCDeterministicReleaseStore):
    """Reject non-initial releases until their exact predecessor is materialized."""

    def ingest(self, spec: FOMCStatementSpec, *, html: str,
               previous_source_url: str | None = None,
               fetched_at: float | None = None) -> dict[str, Any]:
        if previous_source_url and self._previous(previous_source_url) is None:
            raise ValueError("FOMC_PREVIOUS_RELEASE_MISSING")
        result = super().ingest(
            spec, html=html, previous_source_url=previous_source_url,
            fetched_at=fetched_at)
        result["strict_previous_contract"] = STRICT_FOMC_STORE_CONTRACT_VERSION
        return result

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "strict_previous_contract": STRICT_FOMC_STORE_CONTRACT_VERSION,
            "current_release_can_freeze_without_known_previous": False,
        }
