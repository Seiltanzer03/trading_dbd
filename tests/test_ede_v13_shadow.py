from __future__ import annotations

from seiltanzer.edge_discovery.shadow import ShadowLedger


def test_shadow_ledger_starts_empty(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.jsonl")
    assert ledger.events() == []
    assert ledger.unresolved_predictions() == []
