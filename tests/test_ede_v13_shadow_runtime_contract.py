from __future__ import annotations

import json

from seiltanzer.edge_discovery.shadow_runtime import _load_latest_audit


def test_shadow_runtime_accepts_current_v133_audit_contract(tmp_path):
    path = tmp_path / "audit.json"
    payload = {
        "contract_version": "g1s-ede-production-audit-v1.3.3",
        "selective_search": {},
        "frozen_evidence": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _load_latest_audit(path) == payload
