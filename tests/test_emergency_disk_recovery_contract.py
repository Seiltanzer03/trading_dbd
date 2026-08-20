from pathlib import Path


def _workflow() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / ".github/workflows/emergency-production-disk-recovery.yml").read_text(
        encoding="utf-8"
    )


def test_emergency_recovery_verifies_newest_backup_before_pruning_old_pairs():
    workflow = _workflow()

    sha_check = workflow.index("NEWEST_SHA_MATCH")
    sha_failure = workflow.index("newest verified backup SHA256 mismatch")
    keep_eight = workflow.index("keep = verified[:8]")
    remove_old = workflow.index("remove = verified[8:]")
    unlink_database = workflow.index("database.unlink(missing_ok=True)")

    assert sha_check < sha_failure < keep_eight < remove_old < unlink_database
    assert 'authoritative = Path("/opt/seiltanzer/data/trades.db").resolve()' in workflow
    assert "database == authoritative" in workflow
    assert "not confined(database)" in workflow
    assert 'root = Path("/opt/seiltanzer/data/backups/local").resolve()' in workflow


def test_emergency_recovery_keeps_cleanup_bounded_and_open_ede_scratch_safe():
    workflow = _workflow()

    assert "lsof -- \"$candidate\"" in workflow
    assert 'lsof +D "$candidate"' in workflow
    assert "-mmin +360" in workflow
    assert "seiltanzer-ede-source-*.sqlite3" in workflow
    assert "ede-v*-production-*" in workflow
    assert "STALE_EDE_BYTES_REMOVED" in workflow
    assert "rm -rf /opt/seiltanzer/data" not in workflow
    assert "rm -f /opt/seiltanzer/data/trades.db" not in workflow


def test_one_gib_gate_runs_only_after_backup_prune_service_restart_and_db_integrity():
    workflow = _workflow()

    prune = workflow.index("VERIFIED_PAIRS_REMOVED")
    restart = workflow.index("systemctl start seiltanzer\n            restart_needed=0")
    quick_check = workflow.index("PRAGMA quick_check")
    quick_check_gate = workflow.index('test "$db_check" = ok')
    headroom = workflow.index('if [ "$avail_kb" -lt 1048576 ]')

    assert prune < restart < quick_check < quick_check_gate < headroom
    assert "Less than 1 GiB available after safe system + verified-backup cleanup" in workflow
    assert "du -xhd1 /opt" in workflow
    assert "du -xhd1 /var" in workflow
    assert "du -xhd1 /root" in workflow


def test_emergency_recovery_restarts_service_on_pre_restart_failure():
    workflow = _workflow()

    stop = workflow.index("systemctl stop seiltanzer")
    trap = workflow.index("trap 'if [ \"${restart_needed:-0}\" = 1 ]; then systemctl start seiltanzer || true; fi' EXIT")
    sha_check = workflow.index("NEWEST_SHA_MATCH")

    assert stop < trap < sha_check
    assert "test \"$(systemctl is-active seiltanzer)\" = active" in workflow
    assert 'con=sqlite3.connect("file:/opt/seiltanzer/data/trades.db?mode=ro", uri=True)' in workflow
