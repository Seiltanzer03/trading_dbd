"""Execute real workflow guard prefixes against disposable maintenance roots."""
from pathlib import Path
import json
import sqlite3
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD_ERROR = 'HISTORY_CUTOVER_ACTIVE_MUTATION_REFUSED'
WORKFLOWS = ('deploy.yml', 'production-single-slot-backup-recovery.yml',
             'emergency-production-disk-recovery.yml')


def _first_ssh_script(name):
    source = (ROOT/'.github/workflows'/name).read_text()
    tail = source.split('script: |\n', 1)[1]
    lines = []
    for line in tail.splitlines():
        if line.strip() and not line.startswith('            '):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines))


@pytest.mark.parametrize('workflow', WORKFLOWS)
@pytest.mark.parametrize('active', [False, True])
def test_real_mutation_prefix_refuses_active_cutover_before_any_service_change(tmp_path, workflow, active):
    script = _first_ssh_script(workflow)
    assert GUARD_ERROR in script
    end = script.index('\nfi', script.index(GUARD_ERROR))+len('\nfi')
    prefix = script[:end]
    production = tmp_path/'production'
    (production/'data').mkdir(parents=True)
    if active:
        (production/'data/cutover.active').write_text('owned')
    # Execute the actual preamble; path mapping confines its existing flock.
    prefix = prefix.replace('/opt/seiltanzer', str(production))
    result = subprocess.run(['bash', '-c', prefix+'\necho MUTATION_REACHED'],
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == (1 if active else 0)
    assert ('MUTATION_REACHED' in result.stdout) is not active
    assert (GUARD_ERROR in result.stdout) is active


def test_recovery_rechecks_guard_after_existing_shared_host_lock():
    script = _first_ssh_script('production-single-slot-backup-recovery.yml')
    lock = script.index('flock -w 30 9')
    guard = script.index(GUARD_ERROR, lock)
    assert guard < script.index('trap restore_service_on_exit EXIT')


def test_emergency_cleanup_joins_production_queue_and_host_lock_before_cleanup():
    source = (ROOT/'.github/workflows/emergency-production-disk-recovery.yml').read_text()
    assert 'group: production-seiltanzer' in source
    assert 'queue: max' in source
    script = _first_ssh_script('emergency-production-disk-recovery.yml')
    assert script.index('flock -w 30 9') < script.index(GUARD_ERROR) < script.index('journalctl --vacuum-size')


def test_deploy_installs_external_condition_without_resetting_existing_prestart_backup():
    script = _first_ssh_script('deploy.yml')
    assert 'scripts/history_service_fence.py /usr/local/lib/seiltanzer-history-start-guard.py' in script
    assert 'deploy/history-start-fence.conf /etc/systemd/system/seiltanzer.service.d/30-history-cutover.conf' in script
    unit = (ROOT/'deploy/history-start-fence.conf').read_text()
    assert 'ExecCondition=/usr/bin/python3 /usr/local/lib/seiltanzer-history-start-guard.py' in unit
    assert 'ExecStartPre=' not in unit
    assert 'ConditionPathExists=!/opt/seiltanzer/data/cutover.active' in unit


@pytest.mark.parametrize('workflow', WORKFLOWS)
@pytest.mark.parametrize('phase', ['APPLYING_HOT', 'ORIGINAL_RESTORED'])
def test_workflow_journal_guard_uses_actual_read_only_checker(tmp_path, workflow, phase):
    script = _first_ssh_script(workflow)
    end = script.index('\nfi', script.index(GUARD_ERROR))+len('\nfi')
    production = tmp_path/'production'
    data = production/'data'; data.mkdir(parents=True)
    with sqlite3.connect(data/'trades.db') as conn:
        conn.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY)')
    journal = data/'cutover.journal.json'
    journal.write_text(json.dumps(dict(contract='sqlite-history-transaction-v1', run_id='shell-proof',
                                      plan_sha256='1'*64, phase=phase, service_started=False),
                                 sort_keys=True, separators=(',', ':')))
    journal.chmod(0o600)
    prefix = script[:end].replace('/opt/seiltanzer', str(production)).replace(
        '/usr/local/lib/seiltanzer-history-start-guard.py', str(ROOT/'scripts/history_service_fence.py'))
    result = subprocess.run(['bash', '-c', prefix+'\necho MUTATION_REACHED'],
                            capture_output=True, text=True, timeout=3)
    idle = phase == 'ORIGINAL_RESTORED'
    assert result.returncode == (0 if idle else 1), result.stderr
    assert ('MUTATION_REACHED' in result.stdout) is idle


def test_health_does_not_dispatch_backup_rotation_for_a_guarded_cutover():
    source = (ROOT/'.github/workflows/production-health-recovery.yml').read_text()
    assert 'capture_stdout: true' in source
    assert "!contains(steps.diagnostics.outputs.stdout, 'HISTORY_CUTOVER_GUARDED=1')" in source
    assert 'HISTORY_CUTOVER_GUARDED=1' in _first_ssh_script('production-health-recovery.yml')


def test_emergency_cleanup_cannot_be_triggered_by_releasing_its_guard_change():
    source = (ROOT/'.github/workflows/emergency-production-disk-recovery.yml').read_text()
    assert '\n  push:' not in source.split('permissions:', 1)[0]
    assert 'workflow_dispatch:' in source


@pytest.mark.parametrize('workflow', (*WORKFLOWS, 'production-health-recovery.yml'))
def test_modified_ssh_scripts_have_valid_bash_syntax(workflow):
    result = subprocess.run(['bash', '-n'], input=_first_ssh_script(workflow),
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
