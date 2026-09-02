from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "production-single-slot-backup-recovery.yml"


def test_recovery_is_pr_triggered_exact_sha_and_serialized_with_production():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "paths: [.github/workflows/production-single-slot-backup-recovery.yml]" in workflow
    assert "workflow_run:" in workflow
    assert 'workflows: ["deploy"]' in workflow
    assert "types: [completed]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "expected_sha:" in workflow
    assert "group: production-seiltanzer" in workflow
    assert "cancel-in-progress: false" in workflow
    assert 'test "$(git -C /opt/seiltanzer rev-parse HEAD)" = "$EXPECTED_SHA"' in workflow
    assert 'statuses.get("ci/full-webkit", "missing")' in workflow
    assert 'item.get("name") == "deploy"' in workflow
    assert "needs_recovery=true" in workflow


def test_recovery_can_remove_only_backup_pairs_hidden_temps_and_classified_legacy_copy():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'root = Path("/opt/seiltanzer/data/backups/local").resolve()' in workflow
    assert 'live = Path("/opt/seiltanzer/data/trades.db").resolve()' in workflow
    assert 'root.glob(".*.tmp.sqlite3")' in workflow
    assert 'root.glob("*.manifest.json")' in workflow
    assert "backup_db.unlink()" in workflow
    assert "manifest_path.unlink()" in workflow
    assert 'legacy_raw = Path("/opt/seiltanzer/trades.db")' in workflow
    assert 'authority_path = Path("/opt/seiltanzer/data/database_authority.json")' in workflow
    assert 'item.get("classification") == "NON_AUTHORITATIVE_LEGACY"' in workflow
    assert "legacy_raw.is_symlink()" in workflow
    assert "os.path.samefile(legacy, live)" in workflow
    assert "legacy.unlink()" in workflow
    assert "legacy backup has unmatched critical rows" in workflow
    assert "live.unlink" not in workflow
    assert "trades.db).unlink" not in workflow
    assert "AUTHORITATIVE_DB_QUICK_CHECK" in workflow
    assert "NEWEST_BACKUP_SHA_MATCH" in workflow
    assert "CLOSED_EDE_SNAPSHOTS_REMOVED" in workflow
    assert "/tmp/seiltanzer-ede-source-*.sqlite3" in workflow
    assert "lsof -- \"$candidate\"" in workflow
    assert "seiltanzer-ede-*.service" in workflow
    assert "pkill -f '/opt/seiltanzer/scripts/[p]roduction_ede_offload.py'" in workflow


def test_recovery_can_resume_after_the_verified_slot_was_safely_removed():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "NO_VERIFIED_LOCAL_BACKUP=true" in workflow
    assert "newest = verified[0] if verified else None" in workflow
    assert "if free_bytes() < required and newest is not None:" in workflow
    assert "LEGACY_BACKUP_QUICK_CHECK" in workflow
    assert "LEGACY_BACKUP_COPY_REMOVED" in workflow


def test_recovery_requires_headroom_then_a_new_verified_exact_sha_backup():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "live.stat().st_size + 1024 * 1024 * 1024" in workflow
    assert "insufficient headroom after backup-only rotation" in workflow
    assert 'payload.get("reason") == "prestart"' in workflow
    assert 'str(payload.get("git_commit") or "") == expected_sha' in workflow
    assert "no new exact-SHA verified prestart backup" in workflow
    assert "POST_RECOVERY_DB_QUICK_CHECK" in workflow
    assert "ops/single-slot-backup-recovery" in workflow


def test_successful_recovery_dispatches_existing_deploy_workflow():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "actions: write" in workflow
    assert "needs.recover.result == 'success'" in workflow
    assert "/actions/workflows/deploy.yml/dispatches" in workflow
    assert r'\"expected_sha\":\"$EXPECTED_SHA\"' in workflow
