from pathlib import Path

import pytest

from seiltanzer.research_acceptance_gate import (
    MAX_ACCEPTANCE_GATE_TTL_SEC,
    write_acceptance_gate,
)


def test_acceptance_gate_max_ttl_matches_final_ede_envelope(tmp_path):
    assert MAX_ACCEPTANCE_GATE_TTL_SEC == 21600

    gate = tmp_path / "acceptance.json"
    payload = write_acceptance_gate(
        "smoke-final",
        "sha-final",
        ttl_seconds=21600,
        path=gate,
        now=100.0,
    )
    assert payload["expires_at"] == 21700.0

    with pytest.raises(ValueError, match="ttl_seconds must be"):
        write_acceptance_gate(
            "smoke-too-long",
            "sha-final",
            ttl_seconds=21601,
            path=gate,
            now=100.0,
        )


def test_post_research_workflow_requests_supported_gate_ttl():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-post-research.yml").read_text(
        encoding="utf-8"
    )
    assert "--ttl-seconds 21600" in workflow
