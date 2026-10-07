"""Execute the workflow's guard; a queued dispatch must spare a healthy release."""
import os
from pathlib import Path
import subprocess
import pytest
import yaml


@pytest.mark.parametrize('event',['workflow_run','workflow_dispatch'])
@pytest.mark.parametrize('healthy',[True,False])
def test_queued_recovery_rechecks_current_service_before_stopping(tmp_path,event,healthy):
    workflow=yaml.safe_load(Path('.github/workflows/production-single-slot-backup-recovery.yml').read_text())
    script=workflow['jobs']['recover']['steps'][0]['with']['script']
    guard=script.split('# Recovery deliberately stops')[0]
    # External host commands are unavailable in this test. Supply their actual
    # output/exit contract; execute the real guard with bash and observe its exit.
    for name,body in {'git':'echo "$EXPECTED_SHA"',
                      'systemctl':'exit 0',
                      'curl':'echo "$HEALTH_HTTP"'}.items():
        p=tmp_path/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],
         'EVENT_NAME':event,'EXPECTED_SHA':'accepted-main','HEALTH_HTTP':'200' if healthy else '503'}
    result=subprocess.run(['bash','-c',guard+'\necho RECOVERY_REQUESTED\n'],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    if healthy:
        assert result.stdout.strip()=='RECOVERY_NOT_REQUIRED_EXACT_SHA_HEALTHY=1'
    else:
        assert result.stdout.strip()=='RECOVERY_REQUESTED'
