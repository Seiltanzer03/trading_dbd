import os
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
PARTICIPANTS=('deploy.yml','production-sha-stage.yml','production-single-slot-backup-recovery.yml','production-network-diagnostics.yml')


def test_all_shared_production_participants_preserve_pending_requests():
    for name in PARTICIPANTS:
        lines=(ROOT/'.github/workflows'/name).read_text().splitlines()
        found=False
        for i,line in enumerate(lines):
            if line.strip()!='group: production-seiltanzer':continue
            found=True;indent=len(line)-len(line.lstrip());block=[]
            for following in lines[i+1:]:
                if following.strip() and len(following)-len(following.lstrip())<indent:break
                block.append(following.strip())
            assert 'queue: max' in block,name
            assert 'cancel-in-progress: false' in block,name
        assert found,name


@pytest.mark.parametrize('case',['current','superseded','http_failure','malformed'])
def test_main_guard_executes_real_fail_closed_http_boundary(tmp_path,case):
    fake=tmp_path/'curl'
    fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$CURL_CALL_LOG\"\nif [ \"$FAKE_CASE\" = http_failure ]; then exit 22; fi\nprintf '{\"object\":{\"sha\":\"%s\"}}\\n' \"$FAKE_MAIN_SHA\"\n")
    fake.chmod(0o700)
    expected='a'*40;main=expected if case=='current' else ('not-a-sha' if case=='malformed' else 'b'*40)
    env={**os.environ,'PATH':str(tmp_path)+os.pathsep+os.environ['PATH'],
         'GH_TOKEN':'isolated-fixture-token','GITHUB_REPOSITORY':'owner/repo',
         'EXPECTED_SHA':expected,'FAKE_MAIN_SHA':main,'FAKE_CASE':case,'CURL_CALL_LOG':str(tmp_path/'calls')}
    result=subprocess.run(['bash',str(ROOT/'scripts/require_current_main.sh')],env=env,capture_output=True,text=True,timeout=5)
    assert (result.returncode==0)==(case=='current'),result.stderr
    assert ('CURRENT_MAIN_VERIFIED' in result.stdout)==(case=='current')
    assert 'isolated-fixture-token' not in result.stdout+result.stderr
    assert (tmp_path/'calls').exists()
    assert 'https://api.github.com/repos/owner/repo/git/ref/heads/main' in (tmp_path/'calls').read_text()


def test_main_guard_precedes_stage_and_deploy_mutations():
    for name,mutation in [('deploy.yml','name: Deliver exact SHA over SSH'),('production-sha-stage.yml','name: Build exact-SHA Git bundle off-host')]:
        text=(ROOT/'.github/workflows'/name).read_text()
        assert text.index('uses: actions/checkout@v4')<text.index('bash scripts/require_current_main.sh')<text.index(mutation)
