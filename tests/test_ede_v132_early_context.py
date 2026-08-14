from __future__ import annotations

from seiltanzer.ai_verdict import _normalize_ede_maturity_language
from seiltanzer.edge_discovery.evidence_ledger import compact_primary_candidate


def _candidate(maturity: str) -> dict:
    return {
        "candidate_id": "c1",
        "hypothesis_id": "h1",
        "horizon_minutes": 15,
        "edge_maturity": maturity,
        "aggregate_scope": "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY",
        "inner_primary_folds": 1,
        "primary_only_aggregate": {"raw_n": 120},
        "folds_evaluated": 1,
        "folds_positive": 1,
        "q_value": 0.50,
        "global_ret5_comparison": {"brier_delta": 0.001, "logloss_delta": 0.002},
        "where_it_helps": True,
        "where_it_hurts": False,
        "conditions": [{"feature_id": "option.iv"}],
    }


def test_early_context_is_preserved_as_frozen_explanation_evidence():
    compact = compact_primary_candidate(_candidate("EARLY_CONTEXT"))

    assert compact is not None
    assert compact["edge_maturity"] == "EARLY_CONTEXT"
    assert compact["directional_evidence"] == "SUPPORTS_PERSISTENCE"
    assert compact["aggregate_scope"] == "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY"


def test_ai_language_does_not_call_early_context_validated_edge():
    context = {
        "edge_maturity": "EARLY_CONTEXT",
        "context_lines_ru": [
            "IV/GEX/skew дают смешанный контекст без самостоятельного сигнала.",
            "Conditional edge подтверждён замороженным primary evidence.",
        ],
    }

    _normalize_ede_maturity_language(context)

    line = context["context_lines_ru"][1]
    assert "ранний" in line
    assert "ещё не edge-сигнал" in line
    assert "production decision score равен нулю" in line


def test_ai_language_keeps_provisional_as_shadow_only():
    context = {
        "edge_maturity": "PROVISIONAL_EDGE",
        "context_lines_ru": ["Conditional edge подтверждён замороженным primary evidence."],
    }

    _normalize_ede_maturity_language(context)

    assert "provisional" in context["context_lines_ru"][0]
    assert "shadow evidence" in context["context_lines_ru"][0]
    assert "CLOSE/EXIT" in context["context_lines_ru"][0]
