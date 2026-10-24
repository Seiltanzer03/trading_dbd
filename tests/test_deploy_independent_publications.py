"""Execute actual dispatch shells across controlled upstream failure boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('failed_stage', ['readiness', 'macro'])
def test_publication_dispatch_is_independent_of_macro_but_requires_readiness(tmp_path, failed_stage):
    steps = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())['jobs']['deploy']['steps']
    log = tmp_path / 'requests.jsonl'
    curl = tmp_path / 'curl'
    curl.write_text('#!' + sys.executable + '\n'
        'import json,os,sys\n'
        'args=sys.argv[1:]\n'
        'payload=json.loads(args[args.index("-d")+1])\n'
        'urls=[v for v in args if v.startswith("https://api.github.com/")]\n'
        'assert len(urls)==1\n'
        'with open(os.environ["REQUEST_LOG"],"a") as f:f.write(json.dumps({"url":urls[0],"payload":payload})+"\\n")\n')
    curl.chmod(0o700)
    # Fail only the canonical hosted macro acquisition, before any SSH/install.
    python = tmp_path / 'python'
    python.write_text('#!/bin/sh\n'
        'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi\n'
        'if [ "$1" = "scripts/build_offhost_macro_bundle.py" ]; then exit 73; fi\n'
        'exit 91\n')
    python.chmod(0o700)
    environment = {**os.environ, 'PATH':str(tmp_path)+os.pathsep+os.environ['PATH'],
                   'EXPECTED_SHA':'a'*40, 'ACCEPTANCE_RUN_ID':'123',
                   'GH_TOKEN':'offline-test-only', 'REQUEST_LOG':str(log)}
    status = 0
    for step in steps:
        if failed_stage == 'readiness' and step.get('id') == 'readiness':
            # The real SSH readiness boundary has failed; later shells do not run.
            status = 73
            break
        if step.get('id') not in {'mathrefresh','sourcerefresh','fomcrefresh','orchestration'} and step.get('name') != 'Build exact-SHA official macro transport bundle off-host':
            continue
        script = step['run'].replace('${{ github.repository }}', 'Seiltanzer03/trading_dbd')
        result = subprocess.run(['bash','-c',script],env=environment,capture_output=True,text=True,timeout=5)
        status = result.returncode
        if status:
            assert status == 73, result.stdout + result.stderr
            break
    assert status == 73
    requests = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    expected = [] if failed_stage == 'readiness' else [
        'mathematical-edge.yml','edge-family-sources.yml','fomc-prospective-capture.yml']
    assert [r['url'].rsplit('/',2)[1] for r in requests] == expected
    assert all(r['payload'] == {'ref':'main','inputs':{'expected_sha':'a'*40}} for r in requests)
    assert not any('production-post-research.yml' in r['url'] for r in requests)
