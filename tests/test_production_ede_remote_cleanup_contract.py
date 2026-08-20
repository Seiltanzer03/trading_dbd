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


def test_exact_snapshot_cleanup_uses_fresh_always_session_and_numeric_run_id() -> None:
    text = _workflow_text()
    export_pos = text.index("- name: Export immutable production DB and release exact-run gate")
    cleanup_pos = text.index(
        "- name: Guarantee exact remote EDE snapshot cleanup with a fresh SSH session"
    )
    upload_pos = text.index("- uses: actions/upload-artifact@v4", cleanup_pos)
    block = text[cleanup_pos:upload_pos]

    assert export_pos < cleanup_pos < upload_pos
    assert "if: always()" in block
    assert "uses: appleboy/ssh-action@v1" in block
    assert "RUN_ID: ${{ github.run_id }}" in block
    assert "''|*[!0-9]*" in block
    assert 'snapshot="/tmp/seiltanzer-ede-source-${RUN_ID}.sqlite3"' in block
    assert 'rm -f -- "$snapshot" "${snapshot}-wal" "${snapshot}-shm"' in block
    assert 'test ! -e "$snapshot"' in block
    assert 'test ! -e "${snapshot}-wal"' in block
    assert 'test ! -e "${snapshot}-shm"' in block
    assert "/opt/seiltanzer/data" not in block


def test_stale_cleanup_runs_before_new_snapshot_export() -> None:
    text = _workflow_text()
    stale_pos = text.index("- name: Remove only stale closed EDE snapshot leftovers")
    export_pos = text.index("- name: Export immutable production DB and release exact-run gate")
    assert stale_pos < export_pos
