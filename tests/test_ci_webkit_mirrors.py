"""Exercise CI mirror selection without networking, sudo, or package writes."""
import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/install_webkit_dependencies.sh'


def run_installer(tmp_path, reachable='', install_exit='0'):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    commands = {
        'curl': '''#!/bin/bash
printf 'curl %s\\n' "$*" >> "$TEST_LOG"
[[ "${*: -1}" == "$REACHABLE/dists/jammy/InRelease" ]]
''',
        'sudo': '''#!/bin/bash
printf 'sudo %s\\n' "$*" >> "$TEST_LOG"
if [[ "$1" == tee ]]; then cat >> "$TEST_LOG"; fi
''',
        'npx': '''#!/bin/bash
printf 'npx %s\\n' "$*" >> "$TEST_LOG"
exit "$INSTALL_EXIT"
''',
    }
    for name, content in commands.items():
        path = bindir / name
        path.write_text(content)
        path.chmod(0o755)
    log = tmp_path / 'commands.log'
    env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}', TEST_LOG=str(log),
               REACHABLE=reachable, INSTALL_EXIT=install_exit)
    result = subprocess.run(['bash', str(SCRIPT)], env=env, text=True,
                            capture_output=True, timeout=5)
    return result, log.read_text() if log.exists() else ''


def test_uses_reachable_https_archive_and_keeps_strict_signed_apt(tmp_path):
    result, log = run_installer(tmp_path, 'https://archive.ubuntu.com/ubuntu')
    assert result.returncode == 0, result.stderr
    assert 'deb https://archive.ubuntu.com/ubuntu jammy-security' in log
    assert 'APT::Update::Error-Mode "any";' in log
    assert '--connect-timeout 3 --max-time 8 --retry 0' in log
    assert log.count('curl ') == 1
    assert 'npx playwright install-deps webkit' in log
    assert 'trusted=yes' not in log and 'AllowUnauthenticated' not in log


def test_falls_back_to_runner_ubuntu_mirror_before_installing(tmp_path):
    result, log = run_installer(tmp_path, 'http://azure.archive.ubuntu.com/ubuntu')
    assert result.returncode == 0, result.stderr
    assert log.count('curl ') == 2
    assert 'deb http://azure.archive.ubuntu.com/ubuntu jammy ' in log
    assert 'deb https://archive.ubuntu.com/ubuntu' not in log
    assert log.count('npx ') == 1


def test_no_reachable_mirror_refuses_install_and_package_writes(tmp_path):
    result, log = run_installer(tmp_path)
    assert result.returncode != 0
    assert 'CI_UBUNTU_MIRRORS_UNAVAILABLE' in result.stderr
    assert log.count('curl ') == 3
    assert 'sudo ' not in log and 'npx ' not in log


def test_dependency_install_failure_remains_failing(tmp_path):
    result, log = run_installer(tmp_path, 'https://archive.ubuntu.com/ubuntu', '100')
    assert result.returncode == 100
    assert log.count('npx ') == 1
