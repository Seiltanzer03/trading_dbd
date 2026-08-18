"""Causal admission refinement for macro semantic records.

A document published before Data Factory activation is retrospective relative to
this subsystem, but once it is actually extracted it is legitimate context for
*further* T0 observations.  It must never appear in an earlier T0.  Therefore
`available_at <= captured_ts` is the causal boundary; `retrospective_only` stays
as provenance and never grants historical backfill.
"""
from __future__ import annotations

from typing import Any

from .macro_data_factory import DATA_FACTORY_CONTRACT_VERSION, MacroDataFactory, _finite


CAUSALITY_REFINEMENT_VERSION = "macro-data-factory-causality-v1"


def install_macro_data_factory_causality_refinement() -> None:
    if getattr(MacroDataFactory, "_causality_refinement", None) == CAUSALITY_REFINEMENT_VERSION:
        return

    def latest_admissible(self, captured_ts: float, *, family: str = "FOMC_STATEMENT") -> dict[str, Any]:
        cutoff = _finite(captured_ts)
        family = str(family).strip().upper()
        from .macro_data_factory import SUPPORTED_FAMILIES
        if cutoff is None or family not in SUPPORTED_FAMILIES:
            return {
                "contract_version": DATA_FACTORY_CONTRACT_VERSION,
                "status": "UNAVAILABLE", "reason": "INVALID_QUERY",
                "research_only": True, "production_authority": False,
            }
        with self._lock:
            row = self._conn.execute("""
                SELECT e.*,d.family,d.source,d.source_url,d.published_at,d.fetched_at,d.document_sha256
                FROM macro_extractions e JOIN macro_documents d USING(document_id)
                WHERE d.family=? AND e.status='VALID'
                  AND e.available_at IS NOT NULL AND e.available_at<=?
                ORDER BY d.published_at DESC,e.available_at DESC LIMIT 1
            """, (family, cutoff)).fetchone()
        if row is None:
            return {
                "contract_version": DATA_FACTORY_CONTRACT_VERSION,
                "status": "UNAVAILABLE",
                "reason": "NO_CAUSALLY_AVAILABLE_SEMANTIC_OBSERVATION",
                "family": family, "captured_ts": float(cutoff),
                "research_only": True, "production_authority": False,
            }
        payload = self._row_payload(dict(row), cache_hit=True)
        payload["captured_ts"] = float(cutoff)
        payload["causal_admission"] = "available_at<=captured_ts"
        payload["retrospective_publication_never_backfilled"] = True
        payload["prospectively_usable_after_extraction"] = True
        return payload

    MacroDataFactory.latest_admissible = latest_admissible
    MacroDataFactory._causality_refinement = CAUSALITY_REFINEMENT_VERSION
