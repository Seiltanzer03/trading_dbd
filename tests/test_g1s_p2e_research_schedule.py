from pathlib import Path


WORKFLOW = Path('.github/workflows/g1s-p2e-research.yml')


def test_heavy_p2e_is_weekly_or_manual_not_a_deploy_competitor():
    source = WORKFLOW.read_text(encoding='utf-8')
    trigger = source.split('permissions:', 1)[0]
    assert 'push:' not in trigger
    assert 'workflow_dispatch:' in trigger
    assert 'schedule:' in trigger
    assert 'cron: "43 12 * * 0"' in trigger


def test_p2e_still_requires_verified_immutable_backup():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'Download newest verified immutable production backup' in source
    assert "item.get('verified') is True" in source
    assert "PRAGMA quick_check" in source
