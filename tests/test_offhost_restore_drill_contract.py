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

    restore_step = "Fully restore and verify private Object Storage snapshot"
    retire_step = "Retire only the exact restored legacy local backup"
    assert workflow.index(restore_step) < workflow.index(retire_step)
    assert "OFFHOST_FULL_RESTORE_VERIFIED=1" in restore
    assert "PRAGMA quick_check" in restore
    assert "?mode=ro&immutable=1" in restore
    assert "compressed_digest.hexdigest() != compressed_sha" in restore
    assert "database_digest.hexdigest() != database_sha" in restore
    assert "if: github.event_name == 'push'" in workflow
    assert "--expected-backup-id 20260905T180524Z-local-524722" in workflow
    assert "--key backups/v1/daily-slot-4/snapshot.sqlite3.gz" in workflow
    assert "database.unlink()" in retire
    assert "manifest_path.unlink()" in retire
    assert "LOCAL_BACKUP_ALREADY_RETIRED=1" in retire
