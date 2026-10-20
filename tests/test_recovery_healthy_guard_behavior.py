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


@pytest.mark.parametrize('current_sha',['older-accepted-sha',''])
def test_undelivered_sha_refuses_recovery_before_service_stop(tmp_path,current_sha):
    workflow=yaml.safe_load(Path('.github/workflows/production-single-slot-backup-recovery.yml').read_text())
    script=workflow['jobs']['recover']['steps'][0]['with']['script']
    guard=script.split('# Recovery deliberately stops')[0]
    for name,body in {'git':'echo "$CURRENT_SHA"',
                      'systemctl':'echo "$*" >> "$COMMAND_TRACE"; exit 0',
                      'curl':'echo 200'}.items():
        p=tmp_path/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
    trace=tmp_path/'commands'
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],
         'EXPECTED_SHA':'requested-new-sha','CURRENT_SHA':current_sha,'COMMAND_TRACE':str(trace)}
    # If admission falls through, the actual next operation would stop serving.
    result=subprocess.run(['bash','-c',guard+'\nsystemctl stop seiltanzer\n'],env=env,text=True,capture_output=True)
    assert result.returncode!=0,'an undelivered release entered backup recovery'
    assert not trace.exists() or 'stop seiltanzer' not in trace.read_text()


@pytest.mark.parametrize('age_sec,pid,phase,deferred', [
    (755, '123', 'schema.backup.begin', True),
    (400, '123', 'storage.prepare.begin', True),
    (961, '123', 'schema.backup.begin', False),
    (755, '0', 'schema.backup.begin', False),
    (755, '123', 'unrecognized', False),
])
def test_recovery_spares_the_current_bounded_startup(tmp_path, age_sec, pid, phase, deferred):
    workflow=yaml.safe_load(Path('.github/workflows/production-single-slot-backup-recovery.yml').read_text())
    guard=workflow['jobs']['recover']['steps'][0]['with']['script'].split('# Recovery deliberately stops')[0]
    commands={
        'git':'echo "$EXPECTED_SHA"',
        'curl':'echo 000',
        'systemctl':'''case "$*" in
          *MainPID*) echo "$START_PID";;
          *ActiveEnterTimestampMonotonic*) echo 1000000;;
          *) exit 0;; esac''',
        'awk':'echo "$NOW_US"',
        'journalctl':'''case "$*" in *"_PID=$START_PID"*) echo "STARTUP_PHASE phase=$PHASE elapsed_sec=400.000";; esac''',
    }
    for name, body in commands.items():
        p=tmp_path/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],'EXPECTED_SHA':'accepted-main',
         'START_PID':pid,'NOW_US':str((age_sec+1)*1000000),'PHASE':phase}
    result=subprocess.run(['bash','-euo','pipefail','-c',guard+'\necho WOULD_STOP_SERVICE\n'],
                          env=env,text=True,capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
    if deferred:
        assert 'RECOVERY_DEFERRED_STARTUP_IN_PROGRESS=1' in result.stdout
        assert 'WOULD_STOP_SERVICE' not in result.stdout
    else:
        assert 'WOULD_STOP_SERVICE' in result.stdout


def test_deferred_recovery_is_pending_and_never_requests_redeploy(tmp_path):
    workflow=yaml.safe_load(Path('.github/workflows/production-single-slot-backup-recovery.yml').read_text())
    steps=workflow['jobs']['recover']['steps']
    classify=next(s['run'] for s in steps if s.get('id')=='rotation-result')
    output=tmp_path/'outputs'
    env={**os.environ,'GITHUB_OUTPUT':str(output),'REMOTE_STDOUT':'RECOVERY_DEFERRED_STARTUP_IN_PROGRESS=1'}
    result=subprocess.run(['bash','-euo','pipefail','-c',classify],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    values=dict(line.split('=',1) for line in output.read_text().splitlines())
    assert values.get('rotated')=='false'
    assert values.get('deferred')=='true'
    assert workflow['jobs']['recover']['outputs'].get('deferred')
    publish=next(s['run'] for s in steps if s.get('name')=='Publish exact-SHA recovery status')
    body=tmp_path/'body'
    env.update(OUTCOME='success',ROTATED='false',DEFERRED='true',EXPECTED_SHA='accepted-main',BODY=str(body),GH_TOKEN='test-placeholder')
    for expression in ('github.repository','github.server_url','github.run_id'):
        publish=publish.replace('${{ '+expression+' }}','test-placeholder')
    script='curl() { for arg in "$@"; do last="$arg"; done; echo "$last" > "$BODY"; };\n'+publish
    result=subprocess.run(['bash','-euo','pipefail','-c',script],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    import json
    assert json.loads(body.read_text())['state']=='pending'
