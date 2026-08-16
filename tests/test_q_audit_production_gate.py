from pathlib import Path


def test_production_readiness_enforces_q_audit_3000ms_gate():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production-readiness.yml").read_text(
        encoding="utf-8"
    )
    assert "Q_AUDIT_3000MS_GATE" in workflow
    assert "api/research/g1/q/audit?limit=5000" in workflow
    assert "assert elapsed_ms < 3000.0" in workflow
    assert "timeout=5.0" in workflow
