from pathlib import Path


def test_restore_drill_proves_exact_object_before_bounded_local_retirement():
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / ".github/workflows/production-offhost-restore-drill.yml"
    ).read_text(encoding="utf-8")
    restore = (root / "scripts/restore_verified_snapshot.py").read_text(
        encoding="utf-8"
    )
    retire = (
        root / "scripts/retire_local_backup_after_offhost_restore.py"
    ).read_text(encoding="utf-8")

    restore_step = "Restore and verify private Object Storage snapshot off-host"
    retire_step = "Retire only the exact restored legacy local backup"
    assert workflow.index(restore_step) < workflow.index(retire_step)
    assert "OFFHOST_FULL_RESTORE_VERIFIED=1" in restore
    assert "PRAGMA quick_check" in restore
    assert "?mode=ro&immutable=1" in restore
    assert "compressed_digest.hexdigest() != compressed_sha" in restore
    assert "database_digest.hexdigest() != database_sha" in restore
    assert "if: github.event_name == 'push'" in workflow
    assert "offhost-restore-proof-34928308516" in workflow
    assert "run-id: 34928308516" in workflow
    assert "github-token: ${{ github.token }}" in workflow
    assert "database.unlink()" in retire
    assert "manifest_path.unlink()" in retire
    assert "LOCAL_BACKUP_ALREADY_RETIRED=1" in retire
    assert "authoritative live database quick_check failed" in retire
    assert "systemctl start seiltanzer" in retire


def test_scheduled_cloud_drill_accepts_live_snapshots_without_local_db_copy():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/production-offhost-restore-drill.yml').read_text()
    assert 'args=(--snapshot-drill --bucket trading-dbd-backups-2026' in workflow
    assert '${{ runner.temp }}/offhost-restore/restored.sqlite3' in workflow
