from pathlib import Path


def test_ede_heavy_research_is_offloaded_from_production_vps():
    root = Path(__file__).resolve().parents[1]
    ede = (root / ".github/workflows/production-ede-v12-audit.yml").read_text(
        encoding="utf-8"
    )
    offload = (root / "scripts/production_ede_offload.py").read_text(
        encoding="utf-8"
    )
    post = (root / ".github/workflows/production-post-research.yml").read_text(
        encoding="utf-8"
    )
    deploy = (root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    # Production is now only a snapshot/transport boundary. The expensive
    # selective and transition audits execute on a GitHub-hosted runner.
    assert "Run EDE v1.3 research off production VPS" in ede
    assert "python scripts/production_ede_v13_audit.py" in ede
    assert "python scripts/production_ede_transition_audit.py" in ede
    assert "/opt/seiltanzer/scripts/production_ede_v13_audit.py" not in ede
    assert "systemd-run" not in ede
    assert "--property=CPUQuota" not in ede

    # Exact-run acceptance is still chained through the inventory marker, but
    # the cooperative production pause is released before heavy research starts.
    assert '"wait-marker"' in offload
    assert '"ede-inventory"' in offload
    assert '"validate-gate"' in offload
    assert "EDE_OFFLOAD_GATE_RELEASED=1" in offload
    assert ede.index("Export immutable production DB and release exact-run gate") < ede.index(
        "Run EDE v1.3 research off production VPS"
    )

    # The only DB work left on production is a gentle online immutable snapshot,
    # guarded by the same fail-closed 3-second API health budget.
    assert "progress=report_backup_progress" in offload
    assert "EDE_REMOTE_SNAPSHOT_PROGRESS" in offload
    assert "transport.set_keepalive(SSH_KEEPALIVE_SECONDS)" in offload
    assert "ionice -c2 -n7 nice -n 15" in offload
    assert "API_PROBE_MAX_TIME_SECONDS = 3" in offload
    assert 'f"--max-time {API_PROBE_MAX_TIME_SECONDS} "' in offload
    assert "PRAGMA quick_check" in offload

    # Heavy jobs are serialized off-host; compact outputs are returned atomically
    # so existing production research paths remain compatible.
    assert "group: ede-v13-heavy-research" in ede
    assert "actions/upload-artifact@v4" in ede
    assert "actions/download-artifact@v4" in ede
    assert "sftp.posix_rename" in offload

    # With heavy EDE off production, the lease only covers the short serialized
    # acceptance path and is released immediately after the snapshot.
    assert "--ttl-seconds 7200" in post

    # The first offload deployment must evict any legacy pre-v1.3.13 research
    # process that was already running on the VPS before the workflow changed.
    assert "Stopping legacy production EDE unit" in deploy
    assert "seiltanzer-ede-*.service" in deploy
    assert "pkill -f '/opt/seiltanzer/scripts/[p]roduction_ede_v13_audit.py'" in deploy
    assert "pkill -f '/opt/seiltanzer/scripts/[p]roduction_ede_transition_audit.py'" in deploy
    assert "pkill -f '/opt/seiltanzer/scripts/production_ede_v13_audit.py'" not in deploy
    assert "Legacy production EDE unit survived deployment" in deploy
