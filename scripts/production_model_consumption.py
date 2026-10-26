"""Read-only acceptance of models served by the exact production release."""
from __future__ import annotations
import argparse
import json
import re
import shlex
import time
import urllib.request


def verify_report(report, *, expected_sha, captured_ts):
    from seiltanzer.config import ALL_INSTRUMENTS
    from seiltanzer.edge_family_market import FAMILIES, _model_reason
    if (re.fullmatch('[0-9a-f]{40}',expected_sha) is None or not isinstance(report,dict)
            or report.get('state')!='WORKING_MODELS_LOADED'
            or report.get('published_for_sha')!=expected_sha
            or report.get('automatic_execution') is not False or report.get('production_authority') is not False):
        raise ValueError('EXACT_PRODUCTION_MODEL_REPORT_UNAVAILABLE')
    matrix=report.get('family_market_models')
    if not isinstance(matrix,dict) or not matrix or not set(matrix)<=set(ALL_INSTRUMENTS):
        raise ValueError('PRODUCTION_MODEL_MATRIX_INVALID')
    count=0
    for code,rows in matrix.items():
        if not isinstance(rows,dict) or not set(rows)<=set(FAMILIES):
            raise ValueError('PRODUCTION_MODEL_FAMILIES_INVALID')
        for family,row in rows.items():
            if not isinstance(row,dict):raise ValueError('PRODUCTION_MODEL_ROW_INVALID')
            if row.get('status')!='TRAINED':continue
            reason=_model_reason(row,code,family,captured_ts)
            if reason:raise ValueError(f'PRODUCTION_MODEL_REJECTED:{code}:{family}:{reason}')
            count+=1
    summary=report.get('family_market_model_summary') or {}
    declared=summary.get('trained_models')
    if type(declared) is not int or count!=declared or count<=0:
        raise ValueError('PRODUCTION_MODEL_COUNT_MISMATCH')
    return dict(state=report['state'],published_for_sha=expected_sha,validated_models=count,
        trained_models=declared,working_supported_models=summary.get('working_supported_models'))


def verify_local(expected_sha):
    with urllib.request.urlopen('http://127.0.0.1:8790/api/research/mathematical-edge',timeout=6) as response:
        raw=response.read(2_100_001)
        if len(raw)>2_100_000:raise ValueError('PRODUCTION_MODEL_RESPONSE_EXCEEDS_BOUND')
    result=verify_report(json.loads(raw),expected_sha=expected_sha,captured_ts=time.time())
    print('LIVE_MODEL_CONSUMPTION_VERIFIED='+json.dumps(result),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha',required=True)
    parser.add_argument('--password')
    args=parser.parse_args()
    if re.fullmatch('[0-9a-f]{40}',args.expected_sha) is None:raise ValueError('EXPECTED_SHA_INVALID')
    if not args.password:
        verify_local(args.expected_sha)
        return
    from scripts.production_ede_offload import _connect
    client=_connect(args.password,attempts=1)
    try:
        command='cd /opt/seiltanzer && .venv/bin/python -m scripts.production_model_consumption --expected-sha '+shlex.quote(args.expected_sha)
        _,stdout,stderr=client.exec_command(command,timeout=40)
        output=stdout.read(32000).decode();error=stderr.read(4000).decode()
        status=stdout.channel.recv_exit_status()
        print(output,flush=True)
        if error:print(error,flush=True)
        if status or 'LIVE_MODEL_CONSUMPTION_VERIFIED=' not in output:
            raise RuntimeError('LIVE_MODEL_CONSUMPTION_FAILED')
    finally:client.close()


if __name__=='__main__':main()
