"""Execute the handoff shell: production must never run the full feature audit."""
import json
import os
from pathlib import Path
import subprocess
import shlex
from types import SimpleNamespace

import pytest
import yaml

from scripts import production_research_acceptance as acceptance
from scripts import production_ede_offload as offload

ROOT=Path(__file__).resolve().parents[1]


def test_export_ready_marker_does_not_claim_completed_inventory(tmp_path):
    acceptance.write_marker('ede-inventory-ready','123','a'*40,marker_dir=tmp_path)
    ready=acceptance.marker_path('ede-inventory-ready','123',marker_dir=tmp_path)
    assert json.loads(ready.read_text())['stage']=='ede-inventory-ready'
    assert not acceptance.marker_path('ede-inventory','123',marker_dir=tmp_path).exists()


@pytest.mark.parametrize('function',[offload.snapshot,offload.live_snapshot])
def test_existing_snapshot_paths_wait_for_export_ready_without_a_completion_cycle(tmp_path,monkeypatch,function):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    commands=[]
    client=SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(offload,'_connect',lambda _:client)
    monkeypatch.setattr(offload,'_verify_sha',lambda *_:None)
    monkeypatch.setattr(offload,'_release_gate',lambda *a,**kw:None)
    def execute(_client,command,**kwargs):
        commands.append(shlex.split(command))
        raise RuntimeError('STOP_BEFORE_EXPORT')
    monkeypatch.setattr(offload,'_exec',execute)
    args=SimpleNamespace(output_db=str(tmp_path/'same-existing-export.sqlite3'),
        require_acceptance_marker=True,acceptance_run_id='123',password='offline',
        expected_sha='a'*40)
    with pytest.raises(RuntimeError,match='STOP_BEFORE_EXPORT'):function(args)
    assert len(commands)==1
    call=commands[0]
    assert call[call.index('--stage')+1]=='ede-inventory-ready'


def test_production_handoff_prepares_export_without_running_heavy_audit(tmp_path):
    workflow=yaml.safe_load((ROOT/'.github/workflows/production-ede-inventory.yml').read_text())
    step=next(s for s in workflow['jobs']['inventory']['steps'] if s.get('id')=='inventory')
    script=step['with']['script']
    log=tmp_path/'calls.jsonl'
    python=tmp_path/'python'
    python.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
        'with open(os.environ["CALL_LOG"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\n'
        'if any(x.endswith("production_ede_inventory.py") for x in sys.argv):\n'
        ' print("HEAVY_INVENTORY_ON_VPS");sys.exit(73)\n')
    python.chmod(0o700)
    git=tmp_path/'git';git.write_text('#!/bin/sh\nprintf "%s\\n" "$EXPECTED_SHA"\n');git.chmod(0o700)
    script=script.replace('/opt/seiltanzer/.venv/bin/python',str(python))
    script=script.replace('mkdir -p /var/lock','mkdir -p '+str(tmp_path/'lock'))
    result=subprocess.run(['bash','-c',script],env={**os.environ,'EXPECTED_SHA':'a'*40,
        'ACCEPTANCE_RUN_ID':'123','CALL_LOG':str(log),'PATH':str(tmp_path)+':'+os.environ['PATH']},
        capture_output=True,text=True,timeout=3)
    assert result.returncode==0,result.stdout+result.stderr
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    writes=[row for row in calls if 'write-marker' in row]
    assert len(writes)==1
    assert writes[0][writes[0].index('--stage')+1]=='ede-inventory-ready'
    assert any('validate-gate' in row for row in calls)


def test_runner_audit_failure_cannot_write_completion_marker(tmp_path):
    workflow=yaml.safe_load((ROOT/'.github/workflows/production-ede-v12-audit.yml').read_text())
    steps=workflow['jobs']['audit']['steps']
    audit=next((s for s in steps if s.get('id')=='coverage_inventory'),None)
    assert audit is not None,'full feature audit must run on the existing offhost snapshot'
    assert 'run' in audit and 'uses' not in audit
    completion=next(s for s in steps if s.get('id')=='inventory_complete')
    assert steps.index(audit)<steps.index(completion)
    # GitHub's default success() rule skips completion after a failing audit.
    assert completion['if']=="success() && needs.resolve.outputs.exact_chain == 'true'"
    shim=tmp_path/'python'
    shim.write_text('#!/bin/sh\nexit 73\n');shim.chmod(0o700)
    (tmp_path/'ede-source').mkdir()
    result=subprocess.run(['bash','-c',audit['run']],env={**os.environ,
        'SOURCE_DB':str(tmp_path/'existing-verified.sqlite3'),
        'RUNNER_TEMP':str(tmp_path),
        'PATH':str(tmp_path)+':'+os.environ['PATH']},timeout=3)
    assert result.returncode==73


def test_inventory_completion_rejects_deployed_sha_drift_before_any_marker(tmp_path):
    workflow=yaml.safe_load((ROOT/'.github/workflows/production-ede-v12-audit.yml').read_text())
    completion=next(s for s in workflow['jobs']['audit']['steps'] if s.get('id')=='inventory_complete')
    git=tmp_path/'git';git.write_text('#!/bin/sh\nprintf "wrong-sha\\n"\n');git.chmod(0o700)
    log=tmp_path/'unexpected-marker'
    python=tmp_path/'python'
    python.write_text('#!/bin/sh\ntouch '+str(log)+'\n');python.chmod(0o700)
    script=completion['with']['script'].replace('/opt/seiltanzer/.venv/bin/python',str(python))
    result=subprocess.run(['bash','-c',script],env={**os.environ,'EXPECTED_SHA':'a'*40,
        'ACCEPTANCE_RUN_ID':'123','PATH':str(tmp_path)+':'+os.environ['PATH']},timeout=3)
    assert result.returncode!=0
    assert not log.exists()
