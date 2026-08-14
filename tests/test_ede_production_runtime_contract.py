from pathlib import Path


def test_ede_production_workflow_cannot_orphan_research_after_controller_timeout():
    root = Path(__file__).resolve().parents[1]
    ede = (root / ".github/workflows/production-ede-v12-audit.yml").read_text(encoding="utf-8")
    post = (root / ".github/workflows/production-post-research.yml").read_text(encoding="utf-8")

    # Preserve production-safety constraints while giving the proven-slow 20%
    # audit enough controller wall time to finish.
    assert "timeout-minutes: 300" in ede
    assert "command_timeout: 285m" in ede
    assert "--property=CPUQuota=20%" in ede
    assert "--max-time 3" in ede
    assert '"$elapsed" -ge 3000' in ede

    # Each remote transient service has its own hard backstop independent of SSH.
    assert '--property=RuntimeMaxSec="${runtime_sec}s"' in ede
    assert "run_research primary 10800" in ede
    assert "run_research transition 2100" in ede
    assert "systemctl kill --kill-who=all --signal=KILL" in ede

    # The cooperative worker pause must outlive the maximum exact-SHA audit
    # envelope; final EDE releases it immediately instead of waiting for expiry.
    assert "--ttl-seconds 21600" in post
