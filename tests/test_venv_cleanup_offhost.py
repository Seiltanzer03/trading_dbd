"""The final maintenance check must not require a runner on the small VPS."""
from pathlib import Path
import os
import subprocess
import yaml
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('server_sha,success',[('a'*40,True),('b'*40,False)])
def test_hosted_cleanup_uses_exact_sha_readonly_ssh_contract(tmp_path,server_sha,success):
    job=yaml.safe_load((ROOT/'.github/workflows/production-venv-cleanup-check.yml').read_text())['jobs']['verify']
    assert job['runs-on']=='ubuntu-22.04'
    step=job['steps'][0]
    assert step['uses']=='appleboy/ssh-action@v1'
    assert step['with']['envs']=='EXPECTED_SHA'
    script=step['with']['script']
    log=tmp_path/'cleanup-called'
    git=tmp_path/'git';git.write_text('#!/bin/sh\nprintf "%s\\n" "$SERVER_SHA"\n');git.chmod(0o700)
    python=tmp_path/'python'
    python.write_text('#!/bin/sh\nif [ "$1" = - ]; then echo /existing/site-packages; else printf "%s\\n" "$@" > "$CALL_LOG"; fi\n');python.chmod(0o700)
    script=script.replace('/opt/seiltanzer/.venv/bin/python',str(python))
    result=subprocess.run(['bash','-c',script],env={**os.environ,'EXPECTED_SHA':'a'*40,
        'SERVER_SHA':server_sha,'CALL_LOG':str(log),'PATH':str(tmp_path)+':'+os.environ['PATH']},
        capture_output=True,text=True,timeout=3)
    assert (result.returncode==0)==success
    assert log.exists()==success
    if success:
        assert log.read_text().splitlines()==['/opt/seiltanzer/scripts/g1e1_cleanup_venv.py',
            '--site-packages','/existing/site-packages','--require-clean']
