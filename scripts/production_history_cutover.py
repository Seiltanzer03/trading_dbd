"""Off-host driver for the canonical finite VPS history owner.

Reads credentials from the existing workflow environment only. The running
local owner, not this SSH connection, owns exclusion, timeout and rollback.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import sqlite3
import stat
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import history_production_plan as pins
from scripts import sqlite_history_transaction as core
from scripts.history_image_transport import SignedImageTransport
from scripts.history_production_control import launch_command,_validate_epoch
from scripts.production_ede_offload import _connect,_exec,_verify_sha,_probe_api
from scripts.publish_history_image import publish_image,yandex_image_client
from scripts.restore_verified_snapshot import manifest_digest
from scripts.sqlite_history_cutover import build_current_hot_cutover_snapshot
from scripts.sqlite_history_partition import _identity
from scripts.sqlite_history_union import MAX_CATALOG_BYTES,hydrate_current_snapshot

REMOTE_DATA='/opt/seiltanzer/data/'


def capture_frozen(sftp,identity,output,*,deadline):
    """One read of a frozen raw file, exact owner digest and real SQLite check."""
    core._valid_identity(identity)
    output=Path(output)
    if os.path.lexists(output):raise FileExistsError(output)
    if shutil.disk_usage(output.parent).free < identity['database_size_bytes']+2*1024**3:
        raise core.CutoverError('FROZEN_CAPTURE_WORKER_CAPACITY_REFUSED')
    owned=False
    try:
        with output.open('xb') as target:
            owned=True;os.fchmod(target.fileno(),0o600)
            with sftp.open(REMOTE_DATA+'trades.db','rb') as source:
                core._consume(source,identity,deadline,write=target.write)
            target.flush();os.fsync(target.fileno())
        core._verify_database(output,identity,deadline)
        manifest=dict(identity,cold_history_catalog_sha256=pins.CATALOG_SHA256)
        return dict(identity,immutable_sqlite_read=True,immutable_snapshot_verified=True,
                    verified_local_file=_identity(output),storage_manifest=manifest,
                    storage_manifest_sha256=manifest_digest(manifest))
    except BaseException:
        if owned:output.unlink(missing_ok=True)
        raise


def _remote_read(sftp,name):
    path=REMOTE_DATA+name
    try:info=sftp.lstat(path)
    except FileNotFoundError:return None
    except OSError as exc:
        if getattr(exc,'errno',None)==2:return None
        raise core.CutoverError('REMOTE_OWNER_STATE_UNAVAILABLE') from None
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid!=0
            or not 0 < info.st_size <= core.MAX_STATE_BYTES):
        raise core.CutoverError('UNSAFE_REMOTE_OWNER_STATE')
    with sftp.open(path,'rb') as stream:raw=stream.read(core.MAX_STATE_BYTES+1)
    value=json.loads(raw,object_pairs_hook=core._unique)
    if core._canonical(value)!=raw:raise core.CutoverError('NONCANONICAL_REMOTE_OWNER_STATE')
    return value


def _remote_write(sftp,name,value,*,replace=False):
    raw=core._canonical(value)
    current=_remote_read(sftp,name)
    if current is not None and not replace:
        if current!=value:raise core.CutoverError('REMOTE_IMMUTABLE_PLAN_CHANGED')
        return
    temporary=REMOTE_DATA+'.owned-handoff-'+secrets.token_hex(12)
    try:
        with sftp.open(temporary,'wx') as stream:
            sftp.chmod(temporary,0o600)
            stream.write(raw);stream.flush()
        # This atomic request is not retirement authority. The native owner
        # verifies all pins/streams and fsyncs its own immutable accepted plan.
        sftp.posix_rename(temporary,REMOTE_DATA+name)
    finally:
        try:sftp.remove(temporary)
        except OSError:pass


def _private_transport(client,plan):
    result={}
    for kind,public in plan['artifact_receipts'].items():
        receipt={key:value for key,value in public.items() if key!='full_get_verified'}
        url=client.generate_presigned_url('get_object',Params={
            'Bucket':receipt['bucket'],'Key':receipt['object_key']},ExpiresIn=43200)
        result[kind]=dict(receipt=receipt,url=url)
    return result


def _completed_result(sftp,run_id,previous):
    result=_remote_read(sftp,'cutover.result.json')
    if result is None:return None
    if result.get('run_id')!=run_id:raise core.CutoverError('NATIVE_RESULT_EPOCH_MISMATCH')
    if result.get('phase')=='OWNER_RUNNING':return None
    if result==previous and not (result.get('phase')=='COMMITTED' and result.get('service_started') is True):
        return None
    return result


def _catalog_bytes(s3):
    key=pins.CATALOG_PINS['cold_history_catalog_object_key']
    with s3.get_object(Bucket=pins.CATALOG_PINS['cold_history_catalog_bucket'],Key=key)['Body'] as body:
        catalog=body.read(MAX_CATALOG_BYTES+1)
    if len(catalog)>MAX_CATALOG_BYTES or hashlib.sha256(catalog).hexdigest()!=pins.CATALOG_SHA256:
        raise core.CutoverError('PINNED_COLD_CATALOG_MISMATCH')
    return catalog


def _assert_remote_frozen(sftp,state):
    current=_remote_read(sftp,'cutover.preparation.json')
    guard=_remote_read(sftp,'cutover.active')
    if current!=state or guard!=state['epoch'] or current.get('phase')!='FROZEN':
        raise core.CutoverError('FROZEN_PREPARATION_NO_LONGER_ACTIVE')


def _resume_proof(s3,plan,result,directory):
    report=dict(owner=result,worker_full_union_verified=False)
    if result.get('phase')!='COMMITTED':return report
    # Recover the immutable *hot image*, never recapture a partially transferred
    # or already writable production DB. This proof is off-host only.
    hot=directory/'hot.sqlite3'
    end=time.monotonic()+1800
    transport=SignedImageTransport(_private_transport(s3,plan),deadline=end)
    with hot.open('xb') as target:
        os.fchmod(target.fileno(),0o600)
        with transport.open_image('hot') as stream:
            core._consume(stream,plan['hot'],end,write=target.write)
        target.flush();os.fsync(target.fileno())
    core._verify_database(hot,plan['hot'],end,manifest=plan['hot_manifest'])
    proof=dict(plan['hot'],immutable_sqlite_read=True,full_restore_verified=True,
               verified_local_file=_identity(hot),storage_manifest=plan['hot_manifest'],
               storage_manifest_sha256=manifest_digest(plan['hot_manifest']))
    hydrated=hydrate_current_snapshot(hot,directory/'reconstituted.sqlite3',source_receipt=proof,
                                       catalog_bytes=_catalog_bytes(s3),client=s3)
    core._write(directory/'full-union-proof.json',hydrated)
    report.update(worker_full_union_verified=True,images=plan['artifact_receipts'])
    return report


def run(*,expected_sha,run_id,directory,resume=False):
    _validate_epoch(run_id,expected_sha)
    directory=Path(directory)
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    if directory.resolve().is_relative_to(Path('/opt/seiltanzer')):
        raise core.CutoverError('OFFHOST_WORKER_REQUIRED')
    client=_connect(os.environ['SSH_PASSWORD'],attempts=1)
    s3=yandex_image_client()
    try:
        _verify_sha(client,expected_sha)
        sftp=client.open_sftp();sftp.get_channel().settimeout(30)
        try:
            previous=_remote_read(sftp,'cutover.result.json')
            existing=_remote_read(sftp,'cutover.preparation.json')
            if resume and existing is None:
                raise core.CutoverError('RESUME_REQUIRES_EXISTING_NATIVE_EPOCH')
            if existing is not None and (existing.get('run_id')!=run_id
                    or existing.get('application_sha')!=expected_sha):
                raise core.CutoverError('EXISTING_NATIVE_EPOCH_REQUIRES_SAME_RUN')
            if existing is None:
                _probe_api(client)
                size=sftp.stat(REMOTE_DATA+'trades.db').st_size
                # Covers original + compacted image + full-union proof or one
                # worst-case gzip. Refuse before stopping the VPS service.
                if shutil.disk_usage(directory).free < 3*size+2*1024**3:
                    raise core.CutoverError('CUTOVER_WORKER_CAPACITY_REFUSED')
                _exec(client,shlex.join(launch_command(run_id=run_id,application_sha=expected_sha)),timeout=20)
            else:
                accepted=_remote_read(sftp,'cutover.plan.json')
                if accepted is not None:
                    _remote_write(sftp,'cutover.transport.json',_private_transport(s3,accepted),replace=True)
                # Restart only the same finite local owner, never the trading
                # service or a new epoch. Its durable clocks/pins stay binding.
                unit='seiltanzer-history-'+run_id+'.service'
                status=_exec(client,shlex.join(['systemctl','show',unit,'-p','ActiveState','--value']),timeout=15)
                if status.strip()!='active':
                    _exec(client,shlex.join(['systemctl','start','--no-block',unit]),timeout=20)

            end=time.monotonic()+10800
            frozen=None
            while time.monotonic()<end:
                result=_completed_result(sftp,run_id,previous)
                if result is not None:
                    return _resume_proof(s3,_remote_read(sftp,'cutover.plan.json'),result,directory)
                frozen=_remote_read(sftp,'cutover.preparation.json')
                if frozen is not None and frozen.get('phase')=='FROZEN':break
                time.sleep(5)
            else:raise core.CutoverError('NATIVE_PREPARATION_NOT_OBSERVED')
            if (frozen.get('run_id')!=run_id or frozen.get('application_sha')!=expected_sha
                    or type(frozen.get('source')) is not dict):
                raise core.CutoverError('NATIVE_FROZEN_PROOF_MISMATCH')
            if _remote_read(sftp,'cutover.plan.json') is not None:
                # A resumed accepted transfer must not recapture a partial or
                # already live DB. Await that same native owner only.
                while time.monotonic()<end:
                    result=_completed_result(sftp,run_id,previous)
                    if result is not None:
                        return _resume_proof(s3,_remote_read(sftp,'cutover.plan.json'),result,directory)
                    time.sleep(5)
                raise core.CutoverError('NATIVE_RESULT_TIMEOUT')

            original=directory/'original.sqlite3';hot=directory/'hot.sqlite3'
            proof=capture_frozen(sftp,frozen['source']['identity'],original,deadline=time.monotonic()+1800)
            _assert_remote_frozen(sftp,frozen)
            # The original catalog is pinned, reused and fully verified by the
            # existing current-source builder; no new catalog/export sweep.
            catalog=_catalog_bytes(s3)
            built=build_current_hot_cutover_snapshot(original,hot,source_receipt=proof,
                catalog_bytes=catalog,expected_catalog_sha256=pins.CATALOG_SHA256,client=s3)
            core._write(directory/'hot-build-proof.json',built)
            _assert_remote_frozen(sftp,frozen)
            publish_end=time.monotonic()+3600
            identities=dict(original=frozen['source']['identity'],hot={
                'database_sha256':built['hot_database_sha256'],
                'database_size_bytes':hot.stat().st_size})
            receipts={kind:publish_image(path,identities[kind],run_id=run_id,kind=kind,
                                         client=s3,deadline=publish_end)
                      for kind,path in [('original',original),('hot',hot)]}
            manifest=built['hot_restore_proof']['storage_manifest']
            plan=dict(contract=pins.CONTRACT,mode='FENCED_PRODUCTION_ONLY',run_id=run_id,
                application_sha=expected_sha,**identities,hot_manifest=manifest,artifact_receipts=receipts)
            pins.validate_plan(plan,run_id=run_id,application_sha=expected_sha,
                               frozen_identity=frozen['source']['identity'])
            core._write(directory/'cutover-plan.json',plan)
            _assert_remote_frozen(sftp,frozen)
            # Transport first, immutable public request last. Neither file can
            # cause a partial JSON read or expose URLs through workflow output.
            _remote_write(sftp,'cutover.transport.json',_private_transport(s3,plan),replace=True)
            _remote_write(sftp,'cutover.request.json',plan)
            while time.monotonic()<end:
                result=_completed_result(sftp,run_id,previous)
                if result is not None:break
                time.sleep(5)
            else:raise core.CutoverError('NATIVE_RESULT_TIMEOUT')
            report=dict(owner=result,worker_full_union_verified=False,images=receipts,
                        hot_build=built,production_retirement_observed=result.get('phase')=='COMMITTED')
            # Persist measured native result before the slower independent
            # post-cutover proof; the terminal is already running here.
            core._write(directory/'result.json',report)
            if result.get('phase')=='COMMITTED':
                full=directory/'reconstituted.sqlite3'
                hydrated=hydrate_current_snapshot(hot,full,source_receipt=built['hot_restore_proof'],
                                                   catalog_bytes=catalog,client=s3)
                report['worker_full_union_verified']=True
                core._write(directory/'full-union-proof.json',hydrated)
            return report
        finally:sftp.close()
    finally:client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    try:
        report=run(expected_sha=args.expected_sha,run_id=args.run_id,directory=args.directory,resume=args.resume)
        core._write(args.directory/'result.json',report)
        print(json.dumps(dict(run_id=args.run_id,phase=report['owner'].get('phase'),
                              worker_full_union_verified=report['worker_full_union_verified'])))
        return 0 if report['owner'].get('phase')=='COMMITTED' and report['worker_full_union_verified'] else 1
    except Exception:
        print('PRODUCTION_HISTORY_CUTOVER_FAILED_CHECK_PRIVATE_DURABLE_STATE')
        return 1


if __name__=='__main__':raise SystemExit(main())
