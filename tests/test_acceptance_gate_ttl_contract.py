from pathlib import Path

import pytest

from seiltanzer.research_acceptance_gate import (
    MAX_ACCEPTANCE_GATE_TTL_SEC,
    write_acceptance_gate,
)


def test_acceptance_gate_max_ttl_matches_short_production_path(tmp_path):
    assert MAX_ACCEPTANCE_GATE_TTL_SEC == 7200

    gate = tmp_path / "acceptance.json"
    payload = write_acceptance_gate(
        "smoke-final",
        "sha-final",
        ttl_seconds=7200,
        path=gate,
        now=100.0,
    )
    assert payload["expires_at"] == 7300.0

    with pytest.raises(ValueError, match="ttl_seconds must be"):
        write_acceptance_gate(
            "smoke-too-long",
            "sha-final",
            ttl_seconds=7201,
            path=gate,
            now=100.0,
        )


def test_deploy_workflow_requests_supported_gate_ttl_before_readiness():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "--ttl-seconds 7200" in workflow
    assert workflow.index("--ttl-seconds 7200") < workflow.index(
        "/opt/seiltanzer/scripts/production_readiness_check.py"
    )
