"""The existing cold-start budget must inspect readiness after its final delay."""
from pathlib import Path
import os
import subprocess
import textwrap

import pytest


@pytest.mark.parametrize('service,final_http,expected', [
    ('active', '200', 0), ('inactive', '200', 1), ('active', '503', 1),
])
def test_real_deploy_loop_does_not_reject_a_service_ready_during_final_sleep(tmp_path, service, final_http, expected):
    source = Path('.github/workflows/deploy.yml').read_text()
    block = textwrap.dedent(source[source.index('            cold_start_attempts=360'):
                                     source.index("            echo '=== POST-RESTART MEMORY ==='")])
    # Execute the real production loop without waiting: one synthetic final
    # iteration, closed host commands and the state transition at its delay.
    commands = '''
seq() { echo 1; }
systemctl() { [ "$1" != is-active ] || echo "$SERVICE_STATE"; return 0; }
find() { return 0; }
journalctl() { return 0; }
curl() { if [ -f "$READY_FILE" ]; then echo "$FINAL_HTTP 0.001"; else echo '000 0.000'; fi; }
sleep() { : > "$READY_FILE"; }
'''
    env = dict(os.environ, SERVICE_STATE=service, FINAL_HTTP=final_http,
               READY_FILE=str(tmp_path/'ready-after-last-delay'))
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', commands+block],
                            capture_output=True, text=True, env=env, timeout=3)
    assert result.returncode == expected, result.stdout+result.stderr
