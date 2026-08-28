from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_has_bounded_progress_visible_prestart_window():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    # Production measured ~188 seconds to real HTTP availability on 2026-08-25
    # with the ~3.7 GiB source DB. Keep bounded headroom without changing any
    # live readiness/smoke timeout or acceptance criterion.
    assert "cold_start_attempts=105" in workflow
    assert 'seq 1 "$cold_start_attempts"' in workflow
    assert "prestart_backup_bytes=$(find" in workflow
    assert "-name '.*.tmp.sqlite3'" in workflow
    assert "prestart_backup_bytes=${prestart_backup_bytes:-0}" in workflow
    assert "measured about 188 seconds" in workflow

    # A slow stop may SIGKILL the old process while it owns a young hidden backup
    # temp. The next start must not inherit that multi-GiB orphan and attempt a
    # second full SQLite backup on top of it.
    stop = workflow.index("systemctl stop seiltanzer")
    cleanup = workflow.index("DEPLOY_ORPHAN_PRESTART_TEMPS_REMOVED")
    start = workflow.index("systemctl start seiltanzer")
    assert stop < cleanup < start
    assert "/opt/seiltanzer/data/backups/local" in workflow
    assert "DEPLOY_ORPHAN_PRESTART_TEMP_BYTES_REMOVED" in workflow
    assert "systemctl restart seiltanzer" not in workflow

    # A SIGKILL during the byte-identical readiness drill can likewise leave its
    # disposable full-size copy. Remove only its exact /tmp prefix after stop,
    # and fail rather than unlink any file still open by another process.
    restore_cleanup = workflow.index("DEPLOY_ORPHAN_RESTORE_TEMPS_REMOVED")
    assert stop < restore_cleanup < start
    assert "/tmp/seiltanzer-service-restore-drill-*.sqlite3" in workflow
    assert 'lsof -- "$orphan"' in workflow
    assert "Refusing open restore temp after service stop" in workflow
    assert "DEPLOY_ORPHAN_RESTORE_TEMP_BYTES_REMOVED" in workflow

    # If a restart loop fills the disk before git fetch, recovery must first stop
    # the writer, remove only exact hidden prestart temps, and re-check the same
    # unchanged 512 MiB fetch headroom gate.
    prefetch_cleanup = workflow.index(
        "DEPLOY_PREFETCH_ORPHAN_PRESTART_TEMPS_REMOVED"
    )
    fetch = workflow.index("git fetch origin main")
    headroom_error = workflow.index("Insufficient safe pre-fetch headroom")
    assert workflow.rfind("systemctl stop seiltanzer", 0, prefetch_cleanup) >= 0
    assert prefetch_cleanup < headroom_error < fetch
    assert "DEPLOY_PREFETCH_ORPHAN_PRESTART_TEMP_BYTES_REMOVED" in workflow
    assert "required_kb=$((512 * 1024))" in workflow

    # Cold-start durability allowance must not weaken live acceptance limits.
    assert "--connect-timeout 1 --max-time 3" in workflow
    assert "production_readiness_check.py" in workflow
    assert "production_functional_smoke.py" in workflow
