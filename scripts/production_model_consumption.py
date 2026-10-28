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


def verify_setup_coverage(report, *, expected_sha, captured_ts):
    """Verify every configured noncrypto cell without claiming live readiness."""
    from seiltanzer.config import INSTRUMENTS, SETUPS, CRYPTO_INSTRUMENTS
    from seiltanzer.edge_family_market import FAMILIES
    verify_report(report, expected_sha=expected_sha, captured_ts=captured_ts)
    setups = {str(number): setup.instrument for number, setup in SETUPS.items()
              if setup.instrument not in CRYPTO_INSTRUMENTS}
    if not setups or not set(setups.values()) <= set(INSTRUMENTS):
        raise ValueError('CONFIGURED_SETUP_INSTRUMENT_INVALID')
    instruments = {}
    for code in dict.fromkeys(setups.values()):
        rows = report['family_market_models'].get(code)
        if not isinstance(rows, dict) or set(rows) != set(FAMILIES):
            raise ValueError('CONFIGURED_SETUP_MODEL_COVERAGE_MISSING:'+code)
        families = {}; trained = []; supported = []
        for family in FAMILIES:
            row = rows[family]
            status = row.get('status') if isinstance(row, dict) else None
            reason = row.get('reason') if isinstance(row, dict) else None
            if not isinstance(status, str) or not status.strip():
                raise ValueError('CONFIGURED_SETUP_MODEL_STATUS_MISSING:'+code+':'+family)
            if status == 'TRAINED':
                trained.append(family)
                if any(head.get('working_supported') is True for head in row['heads'].values()):
                    supported.append(family)
                reason = 'VALIDATED_HISTORICAL_MARKET_MODEL_NOT_LIVE_INPUT_RECEIPT'
            elif not isinstance(reason, str) or not reason.strip():
                raise ValueError('CONFIGURED_SETUP_MODEL_REASON_MISSING:'+code+':'+family)
            source = row.get('source') or {}
            families[family] = dict(status=status, reason=reason,
                target_provider=source.get('provider'), target_symbol=source.get('ticker'))
        instruments[code] = dict(setups=[int(n) for n,c in setups.items() if c == code],
            trained_families=trained, historically_supported_families=supported, families=families)
    return dict(state='ALL_NONCRYPTO_SETUP_MODEL_COVERAGE_VERIFIED', published_for_sha=expected_sha,
        scope='CONFIGURED_MODELS_NOT_CURRENT_SETUP_RECOMMENDATIONS', current_inputs_verified=False,
        setup_count=len(setups), instrument_count=len(instruments), setup_instruments=setups,
        trained_noncrypto_families=sum(len(r['trained_families']) for r in instruments.values()),
        historically_supported_noncrypto_families=sum(len(r['historically_supported_families']) for r in instruments.values()),
        instruments=instruments)


def verify_family_runtime(status,report,*,expected_sha,captured_ts):
    from seiltanzer.reference_market_series import PROVIDER,SYMBOLS,number
    from seiltanzer.config import ALL_INSTRUMENTS,CRYPTO_INSTRUMENTS
    if not isinstance(status,dict):raise ValueError('FAMILY_RUNTIME_STATUS_INVALID')
    if status.get('current_trade_id') is None:return {'state':'NO_ACTIVE_TRADE_FORECAST_REQUIRED'}
    receipt=status.get('family_market_forecasts') or {}
    if not isinstance(receipt,dict):raise ValueError('FAMILY_RUNTIME_RECEIPT_INVALID')
    code=receipt.get('instrument')
    if (status.get('ready') is not True or status.get('snapshot_trade_id')!=status.get('current_trade_id')
            or code not in ALL_INSTRUMENTS or status.get('current_instrument')!=code
            or receipt.get('contract_version')!='family-runtime-forecast-receipt-v1'
            or receipt.get('scope')!='FROZEN_INPUTS_AT_CAPTURE_NOT_CURRENT_DECISION'
            or receipt.get('published_for_sha')!=expected_sha
            or any(receipt.get(k) is not False for k in ('network_calls','runtime_fitting','final_action_selected'))
            or report.get('published_for_sha')!=expected_sha):raise ValueError('EXACT_FAMILY_RUNTIME_SNAPSHOT_UNAVAILABLE')
    if code in CRYPTO_INSTRUMENTS:return {'state':'CRYPTO_OUTSIDE_NONCRYPTO_REFERENCE_SCOPE'}
    captured=number(receipt.get('captured_ts'))
    if captured is None or not 0<captured<=captured_ts or captured_ts-captured>=900:
        raise ValueError('FAMILY_RUNTIME_RECEIPT_EXPIRED')
    models=(report.get('family_market_models') or {}).get(code) or {}
    available=[];qualified=[]
    for family,row in (receipt.get('families') or {}).items():
        if not isinstance(row,dict):raise ValueError('FAMILY_RUNTIME_ROW_INVALID')
        predictions=row.get('predictions') or {}
        if not predictions:continue
        model=models.get(family) or {};source=model.get('source') or {}
        if source.get('provider')!=PROVIDER:continue
        if (row.get('model_sha256')!=model.get('model_sha256') or row.get('target_provider')!=PROVIDER
                or row.get('target_symbol')!=SYMBOLS[code] or row.get('target_symbol')!=source.get('ticker')):
            raise ValueError('FAMILY_RUNTIME_MODEL_OR_TARGET_MISMATCH')
        origin=number(row.get('forecast_origin_ts'));expiry=number(row.get('reference_valid_until_ts'))
        unexpired=False
        for name,head in predictions.items():
            probability=number(head.get('probability'));horizon=number(head.get('horizon_minutes'))
            expected=(model.get('heads') or {}).get(name) or {}
            if (isinstance(head.get('probability'),bool) or probability is None or not 0<=probability<=1 or horizon!=expected.get('horizon_minutes')
                    or origin is None or origin>captured or type(head.get('forecast_expired')) is not bool):
                raise ValueError('FAMILY_RUNTIME_PREDICTION_INVALID')
            if not head['forecast_expired'] and expiry is not None and captured_ts<min(expiry,origin+60*horizon):
                unexpired=True
        if unexpired:
            available.append(family)
            if row.get('preference_available_at_capture') is True:qualified.append(family)
    if not available:raise ValueError('NO_CURRENT_CONFIGURED_REFERENCE_FORECAST')
    return {'state':'LIVE_FAMILY_FORECASTS_VERIFIED','published_for_sha':expected_sha,'instrument':code,
        'available_forecasts':len(available),'families':sorted(available),
        'qualified_preferences_at_capture':sorted(qualified),'final_action_selected':False}


def verify_local(expected_sha,*,family_runtime=False):
    with urllib.request.urlopen('http://127.0.0.1:8790/api/research/mathematical-edge',timeout=6) as response:
        raw=response.read(2_100_001)
        if len(raw)>2_100_000:raise ValueError('PRODUCTION_MODEL_RESPONSE_EXCEEDS_BOUND')
    report=json.loads(raw)
    result=verify_report(report,expected_sha=expected_sha,captured_ts=time.time())
    print('LIVE_MODEL_CONSUMPTION_VERIFIED='+json.dumps(result),flush=True)
    setup_coverage=verify_setup_coverage(report,expected_sha=expected_sha,captured_ts=time.time())
    print('ALL_SETUP_MODEL_COVERAGE='+json.dumps(setup_coverage),flush=True)
    if family_runtime:
        deadline=time.monotonic()+300;last_reason='SNAPSHOT_WARMING';last_print=0.
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8790/api/ai/snapshot/status',timeout=6) as response:
                    raw=response.read(100001)
                    if len(raw)>100000:raise ValueError('FAMILY_STATUS_EXCEEDS_BOUND')
                receipt=verify_family_runtime(json.loads(raw),report,expected_sha=expected_sha,captured_ts=time.time())
                print('LIVE_FAMILY_FORECAST_CONSUMPTION='+json.dumps(receipt),flush=True)
                break
            except (ValueError,TimeoutError,OSError) as exc:
                last_reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
                if time.monotonic()-last_print>=30:
                    print('FAMILY_RUNTIME_WARMING='+last_reason,flush=True);last_print=time.monotonic()
                time.sleep(2)
        else:raise RuntimeError('LIVE_FAMILY_FORECAST_CONSUMPTION_FAILED:'+last_reason)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha',required=True)
    parser.add_argument('--password')
    parser.add_argument('--family-runtime',action='store_true')
    args=parser.parse_args()
    if re.fullmatch('[0-9a-f]{40}',args.expected_sha) is None:raise ValueError('EXPECTED_SHA_INVALID')
    if not args.password:
        verify_local(args.expected_sha,family_runtime=args.family_runtime)
        return
    from scripts.production_ede_offload import _connect
    client=_connect(args.password,attempts=1)
    try:
        command='cd /opt/seiltanzer && .venv/bin/python -m scripts.production_model_consumption --expected-sha '+shlex.quote(args.expected_sha)
        if args.family_runtime:command+=' --family-runtime'
        _,stdout,stderr=client.exec_command(command,timeout=360 if args.family_runtime else 40)
        output=stdout.read(32000).decode();error=stderr.read(4000).decode()
        status=stdout.channel.recv_exit_status()
        print(output,flush=True)
        if error:print(error,flush=True)
        if status or 'LIVE_MODEL_CONSUMPTION_VERIFIED=' not in output:
            raise RuntimeError('LIVE_MODEL_CONSUMPTION_FAILED')
    finally:client.close()


if __name__=='__main__':main()
