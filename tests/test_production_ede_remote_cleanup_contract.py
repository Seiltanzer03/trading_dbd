from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-ede-v12-audit.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_stale_snapshot_cleanup_is_bounded_to_closed_old_tmp_files() -> None:
    text = _workflow_text()
    block_start = text.index("- name: Remove only stale closed EDE snapshot leftovers")
    block_end = text.index(
        "- name: Materialize point-in-time macro archives before immutable snapshot",
        block_start,
    )
    block = text[block_start:block_end]

    assert "command -v lsof" in block
    assert "lsof -- \"$candidate\"" in block
    assert "find /tmp -maxdepth 1 -type f" in block
    assert "-mmin +360 -print0" in block
    assert "seiltanzer-ede-source-*.sqlite3" in block
    assert "seiltanzer-ede-source-*.sqlite3-wal" in block
    assert "seiltanzer-ede-source-*.sqlite3-shm" in block
    assert "rm -f -- \"$candidate\"" in block
    assert "/opt/seiltanzer/data" not in block


def test_live_snapshot_transport_cleanup_is_run_scoped_and_unconditional() -> None:
    script = (ROOT / "scripts" / "offhost_sqlite_snapshot.py").read_text(
        encoding="utf-8"
    )
    assert "if not run_id.isdigit()" in script
    assert "remote_dir = f'/tmp/seiltanzer-sqlite-tools-{run_id}'" in script
    assert "finally:" in script
    assert "if created_remote:" in script
    assert "'rm -f -- '" in script
    assert "' && rmdir -- '" in script
    assert "REMOTE_DATABASE" in script
    assert "rm -f -- /opt/seiltanzer/data" not in script


def test_stale_cleanup_runs_before_snapshot_preparation() -> None:
    text = _workflow_text()
    stale_pos = text.index("- name: Remove only stale closed EDE snapshot leftovers")
    materialize_pos = text.index(
        "- name: Materialize point-in-time macro archives before immutable snapshot"
    )
    assert stale_pos < materialize_pos
