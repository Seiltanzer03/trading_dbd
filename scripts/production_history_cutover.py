"""Off-host driver for the canonical finite VPS history owner.

Reads credentials from the existing workflow environment only. The running
local owner, not this SSH connection, owns exclusion, timeout and rollback.
"""
import argparse
import gzip
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
import threading
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
from scripts.sqlite_history_cutover import _parse_catalog
from scripts import sqlite_history_partition as archive
from scripts.sqlite_history_partition import _identity
from scripts.sqlite_history_union import MAX_CATALOG_BYTES,hydrate_current_snapshot

REMOTE_DATA='/opt/seiltanzer/data/'
_SQLITE_ERROR_BASE_NAMES=(
    'SQLITE_ERROR','SQLITE_INTERNAL','SQLITE_PERM','SQLITE_ABORT','SQLITE_BUSY',
    'SQLITE_LOCKED','SQLITE_NOMEM','SQLITE_READONLY','SQLITE_INTERRUPT','SQLITE_IOERR',
    'SQLITE_CORRUPT','SQLITE_NOTFOUND','SQLITE_FULL','SQLITE_CANTOPEN','SQLITE_PROTOCOL',
    'SQLITE_EMPTY','SQLITE_SCHEMA','SQLITE_TOOBIG','SQLITE_CONSTRAINT','SQLITE_MISMATCH',
    'SQLITE_MISUSE','SQLITE_NOLFS','SQLITE_AUTH','SQLITE_FORMAT','SQLITE_RANGE',
    'SQLITE_NOTADB','SQLITE_NOTICE','SQLITE_WARNING',
)


def _safe_error_chain(exc):
    """Bounded structural evidence only: never messages, SQL, locals or URLs."""
    root=Path(__file__).resolve().parent.parent
    owned={root/'scripts',root/'seiltanzer'}
    chain=[];seen=set()
    while exc is not None and id(exc) not in seen and len(chain)<4:
        seen.add(id(exc))
        name=type(exc).__name__
        record=dict(exception_type=name if name.isascii() and name.isidentifier() and len(name)<=80 else 'Exception',
                    sqlite_errorcode=None,sqlite_errorname=None,frames=[])
        if isinstance(exc,sqlite3.Error):
            code=getattr(exc,'sqlite_errorcode',None)
            label=getattr(exc,'sqlite_errorname',None)
            base=next((name for name in _SQLITE_ERROR_BASE_NAMES
                       if type(code) is int and 0<=code<=65535
                       and sqlite3.__dict__.get(name)==(code & 255)),None)
            if base is not None:record['sqlite_errorcode']=code
            if (base is not None and isinstance(label,str) and len(label)<=80
                    and (label==base or label.startswith(base+'_'))
                    and sqlite3.__dict__.get(label)==code):
                record['sqlite_errorname']=label
        tb=exc.__traceback__;visited=0
        while tb is not None and visited<128:
            visited+=1
            path=Path(tb.tb_frame.f_code.co_filename).resolve()
            if path.parent in owned and path.suffix=='.py' and path.stem.isascii() and path.stem.isidentifier():
                function=tb.tb_frame.f_code.co_name
                record['frames'].append(dict(file=path.name,line=tb.tb_lineno,
                    function=function if function.isascii() and function.isidentifier() and len(function)<=128 else '<expression>'))
                record['frames']=record['frames'][-12:]
            tb=tb.tb_next
        chain.append(record)
        exc=exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return chain


class _DeadlineWire:
    """Bound compressed input, including members that yield no output."""
    def __init__(self,source,deadline,limit):
        self.source=source;self.deadline=deadline;self.limit=limit;self.total=0
    def read(self,size=-1):
        core._check_deadline(self.deadline)
        data=self.source.read(core.CHUNK_BYTES if size<0 else min(size,core.CHUNK_BYTES))
        core._check_deadline(self.deadline)
        if not isinstance(data,bytes):raise core.CutoverError('INVALID_TRANSFER_CHUNK')
        self.total+=len(data)
        if self.total>self.limit:raise core.CutoverError('COMPRESSED_INPUT_SIZE_MISMATCH')
        return data


def require_union_capacity(directory,catalog):
    required=archive.RESERVE_BYTES+2*catalog['maximum_hydrated_bytes']+3*archive.MAX_BYTES
    if shutil.disk_usage(directory).free<required:
        raise core.CutoverError('UNION_WORKER_CAPACITY_REFUSED')
    return required


def release_owned_capture(path,proof):
    path=Path(path)
    if _identity(path)!=proof['verified_local_file']:
        raise core.CutoverError('OWNED_CAPTURE_CHANGED_BEFORE_RELEASE')
    path.unlink()


def capture_frozen(sftp,identity,output,*,deadline):
    """One compressed SSH read; verify the exact raw owner digest and SQLite."""
    core._valid_identity(identity)
    output=Path(output)
    if os.path.lexists(output):raise FileExistsError(output)
    if shutil.disk_usage(output.parent).free < identity['database_size_bytes']+2*1024**3:
        raise core.CutoverError('FROZEN_CAPTURE_WORKER_CAPACITY_REFUSED')
    owned=False
    try:
        with output.open('xb') as target:
            owned=True;os.fchmod(target.fileno(),0o600)
            core._check_deadline(deadline)
            channel=sftp.get_channel().get_transport().open_session(
                window_size=16*1024**2,timeout=min(15,deadline-time.monotonic()))
            written=0;reported=0;started=time.monotonic()
            def expire():
                # Paramiko exec acknowledgement and partial-read loops do not
                # obey the socket timeout as an absolute operation deadline.
                try:channel.close()
                except Exception:pass
            watchdog=threading.Timer(max(0,deadline-time.monotonic()),expire)
            watchdog.name='frozen-capture-deadline';watchdog.daemon=True
            watchdog.start()
            try:
                channel.settimeout(min(30,max(.001,deadline-time.monotonic())))
                # Fixed read-only command: no interpolated input or VPS staging file.
                channel.exec_command('/bin/gzip -1 -c -- /opt/seiltanzer/data/trades.db')
                with channel.makefile('rb') as wire:
                    bounded=_DeadlineWire(wire,deadline,
                        identity['database_size_bytes']*101//100+65536)
                    def write(data):
                        nonlocal written,reported
                        target.write(data);written+=len(data)
                        if written-reported>=256*1024**2:
                            print(json.dumps({'frozen_capture_bytes':written,
                                'expected_bytes':identity['database_size_bytes'],
                                'wire_bytes':bounded.total,
                                'elapsed_sec':round(time.monotonic()-started,3)}),flush=True)
                            reported=written
                    with gzip.GzipFile(fileobj=bounded,mode='rb') as source:
                        core._consume(source,identity,deadline,write=write)
                while not channel.exit_status_ready():
                    core._check_deadline(deadline)
                    time.sleep(min(.05,max(0,deadline-time.monotonic())))
                core._check_deadline(deadline)
                if channel.recv_exit_status()!=0:
                    raise core.CutoverError('FROZEN_CAPTURE_COMMAND_FAILED')
            except Exception:
                core._check_deadline(deadline)
                raise
            finally:
                watchdog.cancel()
                try:channel.close()
                finally:watchdog.join(timeout=1)
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
    try:
        info=sftp.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid!=0
                or not 0 < info.st_size <= core.MAX_STATE_BYTES):
            raise core.CutoverError('UNSAFE_REMOTE_OWNER_STATE')
        # The native owner atomically archives terminal metadata while opening
        # a new epoch. Absence at either stat or open is a pending observation.
        with sftp.open(path,'rb') as stream:raw=stream.read(core.MAX_STATE_BYTES+1)
    except FileNotFoundError:return None
    except OSError as exc:
        if getattr(exc,'errno',None)==2:return None
        raise core.CutoverError('REMOTE_OWNER_STATE_UNAVAILABLE') from None
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
    if result.get('run_id')!=run_id:
        if result==previous:return None
        raise core.CutoverError('NATIVE_RESULT_EPOCH_MISMATCH')
    if result.get('phase')=='OWNER_RUNNING':return None
    if result==previous and not (result.get('phase')=='COMMITTED' and result.get('service_started') is True):
        return None
    return result


def _owner_properties(client,run_id):
    _validate_epoch(run_id,'a'*40)
    unit='seiltanzer-history-'+run_id+'.service'
    output=_exec(client,shlex.join(['systemctl','show',unit,'-p','LoadState',
                                  '-p','ActiveState','-p','Result']),timeout=15)
    props=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    if set(props)!={'LoadState','ActiveState','Result'}:
        raise core.CutoverError('NATIVE_OWNER_STATUS_UNAVAILABLE')
    return props


def assert_owner_running(client,run_id):
    props=_owner_properties(client,run_id)
    if props['LoadState']=='not-found' or props['ActiveState'] not in ('active','activating'):
        raise core.CutoverError('NATIVE_OWNER_NOT_RUNNING_CHECK_DURABLE_STATE')


def ensure_owner_running(client,*,run_id,expected_sha):
    props=_owner_properties(client,run_id)
    unit='seiltanzer-history-'+run_id+'.service'
    if props['LoadState']=='not-found':
        _exec(client,shlex.join(launch_command(run_id=run_id,application_sha=expected_sha)),timeout=20)
    elif props['ActiveState'] not in ('active','activating'):
        _exec(client,shlex.join(['systemctl','start','--no-block',unit]),timeout=20)


def _catalog_bytes(s3):
    key=pins.CATALOG_PINS['cold_history_catalog_object_key']
    with s3.get_object(Bucket=pins.CATALOG_PINS['cold_history_catalog_bucket'],Key=key)['Body'] as body:
        catalog=body.read(MAX_CATALOG_BYTES+1)
    if len(catalog)>MAX_CATALOG_BYTES or hashlib.sha256(catalog).hexdigest()!=pins.CATALOG_SHA256:
        raise core.CutoverError('PINNED_COLD_CATALOG_MISMATCH')
    return catalog


def _assert_remote_frozen(sftp,state,client):
    assert_owner_running(client,state['run_id'])
    current=_remote_read(sftp,'cutover.preparation.json')
    guard=_remote_read(sftp,'cutover.active')
    if current!=state or guard!=state['epoch'] or current.get('phase')!='FROZEN':
        raise core.CutoverError('FROZEN_PREPARATION_NO_LONGER_ACTIVE')


def _restore_diagnostic_original(sftp,client,frozen,deadline):
    """Request the ordinary native abort; never start or unfence the service here."""
    _assert_remote_frozen(sftp,frozen,client)
    for name in ('cutover.plan.json','cutover.request.json','cutover.journal.json','cutover.transport.json'):
        if _remote_read(sftp,name) is not None:
            raise core.CutoverError('DIAGNOSTIC_REQUIRES_UNHANDED_PREPARATION')
    _remote_write(sftp,'cutover.abort.json',dict(contract='sqlite-history-preparation-abort-v1',
        run_id=frozen['run_id'],application_sha=frozen['application_sha'],epoch=frozen['epoch']))
    while time.monotonic()<deadline:
        result=_completed_result(sftp,frozen['run_id'],None)
        if result is not None:
            if (result.get('phase')!='ABORTED_ORIGINAL' or result.get('service_started') is not True
                    or result.get('application_sha')!=frozen['application_sha']
                    or result.get('epoch')!=frozen['epoch']
                    or result.get('source')!=frozen['source']['identity']
                    or _remote_read(sftp,'cutover.active') is not None):
                raise core.CutoverError('DIAGNOSTIC_ORIGINAL_RETURN_NOT_VERIFIED')
            _verify_sha(client,frozen['application_sha'])
            _probe_api(client)
            return result
        time.sleep(5)
    raise core.CutoverError('DIAGNOSTIC_ORIGINAL_RETURN_TIMEOUT')


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


def run(*,expected_sha,run_id,directory,resume=False,diagnostic=False):
    _validate_epoch(run_id,expected_sha)
    if diagnostic and resume:
        raise core.CutoverError('DIAGNOSTIC_REQUIRES_FRESH_EPOCH')
    directory=Path(directory)
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    if directory.resolve().is_relative_to(Path('/opt/seiltanzer')):
        raise core.CutoverError('OFFHOST_WORKER_REQUIRED')
    client=_connect(os.environ['SSH_PASSWORD'],attempts=1)
    s3=yandex_image_client()
    started=time.monotonic()
    stage='remote_preflight'
    abort_owned=not diagnostic
    def enter(name):
        nonlocal stage
        stage=name
        print(json.dumps({'history_cutover_stage':stage}),flush=True)
    try:
        _verify_sha(client,expected_sha)
        enter('catalog_preflight')
        catalog=_catalog_bytes(s3)
        catalog_value,_=_parse_catalog(catalog,pins.CATALOG_SHA256)
        sftp=client.open_sftp();sftp.get_channel().settimeout(30)
        try:
            previous=_remote_read(sftp,'cutover.result.json')
            existing=_remote_read(sftp,'cutover.preparation.json')
            if resume and existing is None:
                raise core.CutoverError('RESUME_REQUIRES_EXISTING_NATIVE_EPOCH')
            if existing is not None and (existing.get('run_id')!=run_id
                    or existing.get('application_sha')!=expected_sha):
                journal=_remote_read(sftp,'cutover.journal.json')
                terminal=((journal is None and existing.get('phase')=='ABORTED_ORIGINAL'
                           and existing.get('service_started') is True)
                          or (journal is not None and journal.get('run_id')==existing.get('run_id')
                              and journal.get('phase')=='ORIGINAL_RESTORED'
                              and journal.get('service_started') is True))
                if resume or not terminal or _remote_read(sftp,'cutover.active') is not None:
                    raise core.CutoverError('EXISTING_NATIVE_EPOCH_REQUIRES_SAME_RUN')
                # Only a fresh workflow may request a new clock after a safely
                # terminal original. The native EX owner validates and archives
                # old metadata before creating that new epoch.
                existing=None
            if diagnostic and existing is not None:
                raise core.CutoverError('DIAGNOSTIC_REQUIRES_FRESH_EPOCH')
            if previous is not None and previous.get('run_id')==run_id and previous.get('phase')=='COMMITTED' and previous.get('service_started') is True:
                return _resume_proof(s3,_remote_read(sftp,'cutover.plan.json'),previous,directory)
            if existing is None:
                _probe_api(client)
                size=sftp.stat(REMOTE_DATA+'trades.db').st_size
                # Covers original + compacted image + full-union proof or one
                # worst-case gzip. Refuse before stopping the VPS service.
                union_required=archive.RESERVE_BYTES+2*catalog_value['maximum_hydrated_bytes']+3*archive.MAX_BYTES
                if shutil.disk_usage(directory).free < max(3*size+2*1024**3,union_required+4096):
                    raise core.CutoverError('CUTOVER_WORKER_CAPACITY_REFUSED')
                _exec(client,shlex.join(launch_command(run_id=run_id,application_sha=expected_sha)),timeout=20)
                abort_owned=True
            else:
                accepted=_remote_read(sftp,'cutover.plan.json')
                if accepted is not None:
                    _remote_write(sftp,'cutover.transport.json',_private_transport(s3,accepted),replace=True)
                # Restart only the same finite local owner, never the trading
                # service or a new epoch. Its durable clocks/pins stay binding.
                ensure_owner_running(client,run_id=run_id,expected_sha=expected_sha)

            enter('native_preparation')
            end=time.monotonic()+10800
            frozen=None
            while time.monotonic()<end:
                result=_completed_result(sftp,run_id,previous)
                if result is not None:
                    return _resume_proof(s3,_remote_read(sftp,'cutover.plan.json'),result,directory)
                frozen=_remote_read(sftp,'cutover.preparation.json')
                if (frozen is not None and frozen.get('phase')=='FROZEN'
                        and frozen.get('run_id')==run_id):break
                assert_owner_running(client,run_id)
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
                    assert_owner_running(client,run_id)
                    time.sleep(5)
                raise core.CutoverError('NATIVE_RESULT_TIMEOUT')

            original=directory/'original.sqlite3';hot=directory/'hot.sqlite3'
            enter('frozen_capture')
            proof=capture_frozen(sftp,frozen['source']['identity'],original,deadline=time.monotonic()+1800)
            _assert_remote_frozen(sftp,frozen,client)
            if diagnostic:
                # Capture is now immutable and verified. Return the untouched
                # original before cloud compression/upload or any builder work.
                enter('verify_original_service')
                owner=_restore_diagnostic_original(sftp,client,frozen,end)
                report=dict(contract='sqlite-history-current-diagnostic-v1',run_id=run_id,
                    application_sha=expected_sha,owner=owner,original_service_verified=True,
                    production_retirement_observed=False,worker_full_union_verified=False,
                    diagnostic_build_verified=False,source=proof['storage_manifest'],image=None)
                core._write(directory/'capture-proof.json',report)
                enter('publish_original')
                report['image']=publish_image(original,frozen['source']['identity'],run_id=run_id,
                    kind='original',client=s3,deadline=time.monotonic()+3600)
                # Durable cloud byte pins survive a later builder failure and
                # permit another off-host investigation without another outage.
                core._write(directory/'capture-proof.json',report)
                enter('build_hot')
                built=build_current_hot_cutover_snapshot(original,hot,source_receipt=proof,
                    catalog_bytes=catalog,expected_catalog_sha256=pins.CATALOG_SHA256,client=s3)
                core._write(directory/'hot-build-proof.json',built)
                report.update(diagnostic_build_verified=True,hot_build=built)
                return report
            # The original catalog is pinned, reused and fully verified by the
            # existing current-source builder; no new catalog/export sweep.
            enter('build_hot')
            built=build_current_hot_cutover_snapshot(original,hot,source_receipt=proof,
                catalog_bytes=catalog,expected_catalog_sha256=pins.CATALOG_SHA256,client=s3)
            core._write(directory/'hot-build-proof.json',built)
            _assert_remote_frozen(sftp,frozen,client)
            publish_end=time.monotonic()+3600
            identities=dict(original=frozen['source']['identity'],hot={
                'database_sha256':built['hot_database_sha256'],
                'database_size_bytes':hot.stat().st_size})
            receipts={}
            for kind,path in [('original',original),('hot',hot)]:
                enter('publish_'+kind)
                receipts[kind]=publish_image(path,identities[kind],run_id=run_id,kind=kind,
                                             client=s3,deadline=publish_end)
            # Both exact images now have complete cloud readback receipts.
            # Retire only our worker copy; the untouched VPS original and its
            # pinned rollback object remain available. Keep hydrate's gate.
            release_owned_capture(original,proof)
            require_union_capacity(directory,catalog_value)
            manifest=built['hot_restore_proof']['storage_manifest']
            plan=dict(contract=pins.CONTRACT,mode='FENCED_PRODUCTION_ONLY',run_id=run_id,
                application_sha=expected_sha,**identities,hot_manifest=manifest,artifact_receipts=receipts)
            pins.validate_plan(plan,run_id=run_id,application_sha=expected_sha,
                               frozen_identity=frozen['source']['identity'])
            core._write(directory/'cutover-plan.json',plan)
            _assert_remote_frozen(sftp,frozen,client)
            # Transport first, immutable public request last. Neither file can
            # cause a partial JSON read or expose URLs through workflow output.
            enter('handoff')
            _remote_write(sftp,'cutover.transport.json',_private_transport(s3,plan),replace=True)
            _remote_write(sftp,'cutover.request.json',plan)
            enter('native_transfer')
            while time.monotonic()<end:
                result=_completed_result(sftp,run_id,previous)
                if result is not None:break
                assert_owner_running(client,run_id)
                time.sleep(5)
            else:raise core.CutoverError('NATIVE_RESULT_TIMEOUT')
            report=dict(owner=result,worker_full_union_verified=False,images=receipts,
                        hot_build=built,production_retirement_observed=result.get('phase')=='COMMITTED')
            # Persist measured native result before the slower independent
            # post-cutover proof; the terminal is already running here.
            core._write(directory/'result.json',report)
            if result.get('phase')=='COMMITTED':
                enter('full_union')
                full=directory/'reconstituted.sqlite3'
                hydrated=hydrate_current_snapshot(hot,full,source_receipt=built['hot_restore_proof'],
                                                   catalog_bytes=catalog,client=s3)
                report['worker_full_union_verified']=True
                core._write(directory/'full-union-proof.json',hydrated)
            return report
        except Exception as exc:
            # Deliberately allow only fixed public reasons. Provider exceptions
            # can contain signed URLs/credentials and must never enter logs.
            allowed={'TRANSACTION_DEADLINE','IMAGE_SIZE_MISMATCH','IMAGE_IDENTITY_MISMATCH',
                     'INVALID_TRANSFER_CHUNK','NATIVE_OWNER_NOT_RUNNING_CHECK_DURABLE_STATE',
                     'FROZEN_PREPARATION_NO_LONGER_ACTIVE','CUTOVER_WORKER_CAPACITY_REFUSED',
                     'UNION_WORKER_CAPACITY_REFUSED','FROZEN_CAPTURE_COMMAND_FAILED',
                     'COMPRESSED_INPUT_SIZE_MISMATCH'}
            code=str(exc) if isinstance(exc,core.CutoverError) and str(exc) in allowed else 'HISTORY_STEP_FAILED'
            failure=dict(contract='sqlite-history-worker-failure-v1',run_id=run_id,
                         application_sha=expected_sha,stage=stage,code=code,
                         exception_type=type(exc).__name__,elapsed_sec=round(time.monotonic()-started,3))
            try:
                try:failure['error_chain']=_safe_error_chain(exc)
                except Exception:pass  # Evidence failure must not affect abort.
                core._write(directory/'failure.json',failure)
                print(json.dumps(failure),flush=True)
            except Exception:pass  # Diagnostics must never suppress the safe abort.
            # Prompt ordinary preparation failures to restore the verified
            # original; runner death still has the independent native timeout.
            try:
                state=_remote_read(sftp,'cutover.preparation.json')
                if (abort_owned and state is not None and state.get('run_id')==run_id
                        and state.get('application_sha')==expected_sha and state.get('phase')=='FROZEN'
                        and _remote_read(sftp,'cutover.active')==state.get('epoch')
                        and _remote_read(sftp,'cutover.request.json') is None
                        and _remote_read(sftp,'cutover.plan.json') is None):
                    _remote_write(sftp,'cutover.abort.json',dict(contract='sqlite-history-preparation-abort-v1',
                        run_id=run_id,application_sha=expected_sha,epoch=state['epoch']))
            except Exception:pass
            raise
        finally:sftp.close()
    finally:client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--diagnostic',action='store_true',
        help='Fresh capture only on VPS; restore original before off-host publication/build. No cutover handoff.')
    args=parser.parse_args()
    try:
        report=run(expected_sha=args.expected_sha,run_id=args.run_id,directory=args.directory,
                   resume=args.resume,diagnostic=args.diagnostic)
        core._write(args.directory/'result.json',report)
        print(json.dumps(dict(run_id=args.run_id,phase=report['owner'].get('phase'),
                              worker_full_union_verified=report['worker_full_union_verified'])))
        success=(report.get('diagnostic_build_verified') is True and report.get('original_service_verified') is True
                 if args.diagnostic else report['owner'].get('phase')=='COMMITTED' and report['worker_full_union_verified'])
        return 0 if success else 1
    except Exception:
        print('PRODUCTION_HISTORY_CUTOVER_FAILED_CHECK_PRIVATE_DURABLE_STATE')
        return 1


if __name__=='__main__':raise SystemExit(main())
