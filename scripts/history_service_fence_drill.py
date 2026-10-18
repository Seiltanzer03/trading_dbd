"""Finite actual systemd startup-fence proof on disposable runner files only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid


def _run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=check)


def run_drill():
    if os.geteuid() != 0 or not Path('/run/systemd/system').is_dir():
        raise RuntimeError('ACTUAL_SYSTEMD_ROOT_REQUIRED')
    started = time.monotonic()
    name = 'seiltanzer-history-fence-proof-'+uuid.uuid4().hex+'.service'
    unit = Path('/run/systemd/system')/name
    report = dict(contract='sqlite-history-systemd-proof-v1', verified=False,
                  production_touched=False, provider_calls=0, orders_sent=0, cases=[])
    with tempfile.TemporaryDirectory(prefix='history-fence-proof-', dir='/tmp') as directory:
        root = Path(directory)
        data = root/'data'; data.mkdir()
        app = root/'application'; app.mkdir()
        script = root/'history-start-guard.py'
        shutil.copyfile(Path(__file__).with_name('history_service_fence.py'), script)
        database = data/'trades.db'
        with sqlite3.connect(database) as conn:
            conn.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY)')
        prestart, launch = root/'prestart-ran', root/'application-ran'
        guard, journal = data/'cutover.active', data/'cutover.journal.json'
        content = (f'[Unit]\nDescription=Disposable history fence proof\n'
                   f'ConditionPathExists=!{guard}\n[Service]\nType=oneshot\nTimeoutStartSec=10\n'
                   f'ExecCondition=/usr/bin/python3 {script} --database {database} --application-root {app}\n'
                   f'ExecStartPre=/usr/bin/touch {prestart}\nExecStart=/usr/bin/touch {launch}\n')
        # UUID-owned exclusive file; no existing unit or production definition.
        with unit.open('x') as stream:
            stream.write(content)
        try:
            _run('systemctl', 'daemon-reload')
            for case in ('plain', 'active_guard', 'dangling_guard', 'pending_journal', 'original_restored'):
                _run('systemctl', 'stop', name, check=False)
                _run('systemctl', 'reset-failed', name, check=False)
                for path in (prestart, launch, guard, journal):
                    path.unlink(missing_ok=True)
                if case == 'active_guard':
                    guard.write_text('active')
                elif case == 'dangling_guard':
                    guard.symlink_to(root/'missing-target')
                elif case in ('pending_journal', 'original_restored'):
                    phase = 'APPLYING_HOT' if case == 'pending_journal' else 'ORIGINAL_RESTORED'
                    value = dict(contract='sqlite-history-transaction-v1', run_id='systemd-proof',
                                 plan_sha256='1'*64, phase=phase, service_started=False)
                    journal.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')))
                    journal.chmod(0o600)
                command = _run('systemctl', 'start', name, check=False)
                status = _run('systemctl', 'show', name, '-p', 'ConditionResult', '-p', 'Result')
                props = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
                actual = dict(case=case, start_returncode=command.returncode,
                              condition_result=props.get('ConditionResult'), result=props.get('Result'),
                              prestart_executed=prestart.exists(), application_executed=launch.exists())
                report['cases'].append(actual)
                should_start = case in ('plain', 'original_restored')
                if prestart.exists() != should_start or launch.exists() != should_start:
                    raise RuntimeError('PRESTART_OR_APPLICATION_FENCE_FAILED')
                if should_start and (command.returncode != 0 or props.get('Result') != 'success'):
                    raise RuntimeError('VALID_ORIGINAL_START_FAILED')
                if case == 'active_guard' and props.get('ConditionResult') != 'no':
                    raise RuntimeError('SYSTEMD_PATH_CONDITION_NOT_OBSERVED')
                if case == 'pending_journal' and props.get('Result') != 'exec-condition':
                    raise RuntimeError('EXEC_CONDITION_ORDER_NOT_OBSERVED')
            report.update(verified=True, elapsed_ms=round((time.monotonic()-started)*1000, 1))
            return report
        except Exception as exc:
            report.update(error_code=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__,
                          elapsed_ms=round((time.monotonic()-started)*1000, 1))
            return report
        finally:
            _run('systemctl', 'stop', name, check=False)
            _run('systemctl', 'reset-failed', name, check=False)
            unit.unlink()
            _run('systemctl', 'daemon-reload')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = run_drill()
    raw = json.dumps(report, sort_keys=True, indent=2)+'\n'
    if args.output is not None:
        with args.output.open('x') as stream:
            stream.write(raw)
    print(raw, end='')
    return 0 if report['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
