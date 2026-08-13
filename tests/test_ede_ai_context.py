from __future__ import annotations

from seiltanzer.edge_discovery import ai_context


def test_feature_coverage_is_data_ready_but_does_not_promote_edge(monkeypatch):
    monkeypatch.setattr(
        ai_context, "_data_readiness",
        lambda engine: ("DATA_READY_EARLY", [{
            "horizon_minutes": 15, "raw": 120, "effective": 60,
            "temporal_blocks": 2, "data_maturity": "DATA_READY_EARLY",
        }]))
    monkeypatch.setattr(ai_context, "_latest_frozen_context", lambda engine, snapshot: {
        "observation_t0": 99.0,
        "option_static": {"available": True, "iv": .22, "skew": -.04},
        "option_dynamics": {"available": True, "derivatives": {"iv": {"slope": .001}}},
        "cross_asset": {"available": True}, "macro": {"available": True},
        "price_volatility": {"available": True},
    })
    snapshot = {
        "captured_ts": 100.0, "strategy": {"instrument": "NAS100"},
        "policy_manager": {"evidence": {}, "option_derivative_state": {
            "available": True, "option_state_score": .2, "metrics": {}}},
    }
    result = ai_context.build_ai_ede_context(object(), snapshot)
    assert result["data_maturity"] == "DATA_READY_EARLY"
    assert result["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["families"]["OPTIONS"]["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["confidence_modifier"] == 0.0
    assert result["authority"]["production_directional_authority"] is False
    assert result["authority"]["may_trigger_exit_or_close"] is False
    assert "IV/GEX/skew подтверждают удержание" in result["context_lines_ru"][0]
