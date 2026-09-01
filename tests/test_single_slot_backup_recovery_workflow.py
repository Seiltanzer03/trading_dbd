from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "production-single-slot-backup-recovery.yml"


def test_recovery_is_manual_exact_sha_and_serialized_with_production():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "expected_sha:" in workflow
    assert "group: production-seiltanzer" in workflow
    assert "cancel-in-progress: false" in workflow
    assert 'test "$(git -C /opt/seiltanzer rev-parse HEAD)" = "$EXPECTED_SHA"' in workflow
    assert 'statuses.get("ci/full-webkit", "missing")' in workflow


def test_recovery_can_remove_only_backup_pairs_and_hidden_backup_temps():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'root = Path("/opt/seiltanzer/data/backups/local").resolve()' in workflow
    assert 'live = Path("/opt/seiltanzer/data/trades.db").resolve()' in workflow
    assert 'root.glob(".*.tmp.sqlite3")' in workflow
    assert 'root.glob("*.manifest.json")' in workflow
    assert "backup_db.unlink()" in workflow
    assert "manifest_path.unlink()" in workflow
    assert "live.unlink" not in workflow
    assert "trades.db).unlink" not in workflow
    assert "AUTHORITATIVE_DB_QUICK_CHECK" in workflow
    assert "NEWEST_BACKUP_SHA_MATCH" in workflow


def test_recovery_requires_headroom_then_a_new_verified_exact_sha_backup():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "live.stat().st_size + 1024 * 1024 * 1024" in workflow
    assert "insufficient headroom after backup-only rotation" in workflow
    assert 'payload.get("reason") == "prestart"' in workflow
    assert 'str(payload.get("git_commit") or "") == expected_sha' in workflow
    assert "no new exact-SHA verified prestart backup" in workflow
    assert "POST_RECOVERY_DB_QUICK_CHECK" in workflow
    assert "ops/single-slot-backup-recovery" in workflow
