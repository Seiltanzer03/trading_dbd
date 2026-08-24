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

    # Snapshot export reuses the deploy-created, immutable, verified exact-SHA
    # prestart backup. It must never start a second whole live-DB copy.
    assert "MAX_EXACT_BACKUP_AGE_SECONDS = 60 * 60" in offload
    assert "EDE_VERIFIED_BACKUP_SELECTION" in offload
    assert "DEPLOY_PRESTART_VERIFIED_LOCAL_BACKUP" in offload
    assert "database_sha256" in offload
    assert "src.backup(" not in offload
    assert "EDE_VERIFIED_BACKUP_TRANSFER_PROGRESS" in offload
    assert "transport.set_keepalive(SSH_KEEPALIVE_SECONDS)" in offload
    assert "API_PROBE_MAX_TIME_SECONDS = 3" in offload
    assert 'f"--max-time {API_PROBE_MAX_TIME_SECONDS} "' in offload
    assert "PRAGMA quick_check" in offload
    assert "path: ${{ runner.temp }}/ede-source.sqlite3*" in ede

    # Heavy jobs are serialized off-host; compact outputs are returned atomically
    # so existing production research paths remain compatible.
    assert "group: ede-v13-heavy-research" in ede
    assert "actions/upload-artifact@v4" in ede
    assert "actions/download-artifact@v4" in ede
    assert "sftp.posix_rename" in offload

    # With heavy EDE off production, deploy acquires the existing bounded lease
    # before readiness and downstream validates the same continuous owner until
    # the immutable snapshot releases it.
    assert "--ttl-seconds 7200" in deploy
    assert '"$PY" "$ORCH" acquire-gate' not in post
    assert '"$PY" "$ORCH" validate-gate' in post

    # The first offload deployment must evict any legacy pre-v1.3.13 research
    # process that was already running on the VPS before the workflow changed.
    assert "Stopping legacy production EDE unit" in deploy
    assert "seiltanzer-ede-*.service" in deploy
    assert "pkill -f '/opt/seiltanzer/scripts/[p]roduction_ede_v13_audit.py'" in deploy
    assert "pkill -f '/opt/seiltanzer/scripts/[p]roduction_ede_transition_audit.py'" in deploy
    assert "pkill -f '/opt/seiltanzer/scripts/production_ede_v13_audit.py'" not in deploy
    assert "Legacy production EDE unit survived deployment" in deploy
