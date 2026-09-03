from pathlib import Path


WORKFLOW = Path('.github/workflows/production-health-recovery.yml')


def test_health_recovery_requires_green_exact_main_sha_before_dispatch():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'refs/heads/main' in source
    assert 'ci/full-webkit' in source
    assert 'test "$ci" = success' in source
    assert 'expected_sha=$expected_sha' in source


def test_health_recovery_requires_repeated_failed_public_probes():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'for attempt in 1 2 3' in source
    assert 'http://94.241.171.182:8790/api/state' in source
    assert 'unhealthy=true' in source


def test_health_recovery_only_dispatches_existing_fail_closed_recovery():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'production-single-slot-backup-recovery.yml/dispatches' in source
    assert 'expected_sha' in source
    assert 'rm -f /opt/seiltanzer/data/trades.db' not in source
    assert 'rm -rf /opt/seiltanzer/data' not in source
    assert 'sqlite3' not in source


def test_health_recovery_captures_crash_evidence_before_dispatch():
    source = WORKFLOW.read_text(encoding='utf-8')
    diagnostics = source.index('Capture server diagnostics before recovery')
    dispatch = source.index('Dispatch fail-closed single-slot recovery')
    assert diagnostics < dispatch
    assert "journalctl -u seiltanzer --since '-12 hours'" in source
    assert "journalctl -k --since '-12 hours'" in source
    assert 'systemctl show seiltanzer' in source
