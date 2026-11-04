"""Current-source off-host JSON rebuild with the separate finite native owner.

Credentials and signed URLs are private workflow inputs. Public artifacts bind
only actual byte identities, preserved history pins and measured outcomes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import time

from scripts import json_history_owner as native
from scripts import sqlite_history_transaction as core
from scripts import production_history_cutover as previous
from scripts.production_ede_offload import _connect,_exec,_verify_sha,_probe_api
from scripts.publish_history_image import publish_image,yandex_image_client
from scripts.sqlite_history_partition import _identity
from scripts.sqlite_json_rebuild import rebuild_snapshot
from scripts.history_storage_loader import history_state


def _publication_staging_bytes(sizes):
    # Two immutable raw files already exist. Reserve both complete worst-case
    # gzip outputs together, even when the packers allocate concurrently.
    return sum(size*101//100+65536 for size in sizes)+2*1024**3


def make_plan(original,hot,built,*,source_proof,run_id,application_sha,receipts):
    if (_identity(original)!=source_proof.get('verified_local_file')
            or _identity(hot)!=built.get('verified_local_file')
            or built.get('source_database_sha256')!=source_proof.get('database_sha256')
            or built.get('all_typed_table_roots_verified') is not True
            or built.get('all_rows_preserved') is not True):
        raise core.CutoverError('JSON_WORKER_IMAGE_PROOF_MISMATCH')
    fields=history_state.snapshot_history_manifest(original)
    original_identity={key:source_proof[key] for key in core._IDENTITY_FIELDS}
    hot_identity={key:built[key] for key in core._IDENTITY_FIELDS}
    plan=dict(contract=native.CONTRACT,mode='FENCED_PRODUCTION_ONLY',run_id=run_id,
        application_sha=application_sha,original=original_identity,hot=hot_identity,
        original_manifest=dict(original_identity,**fields),
        hot_manifest={key:built[key] for key in core._IDENTITY_FIELDS|set(fields)|{history_state.PAYLOAD_PIN_FIELD}},
        artifact_receipts=receipts)
    native.validate_plan(plan,run_id=run_id,application_sha=application_sha,
                         frozen_identity=original_identity)
    return plan


def _capacity(client):
    # Fixed paths and bounded scalar metadata only, no SQL or user payload.
    code=("import os,json; p='/opt/seiltanzer/data/trades.db'; v=os.statvfs(os.path.dirname(p)); s=os.stat(p); "
          "print(json.dumps(dict(free_bytes=v.f_bavail*v.f_frsize,database_size_bytes=s.st_size,"
          "database_allocated_bytes=s.st_blocks*512)))")
    value=json.loads(_exec(client,shlex.join(['/usr/bin/python3','-c',code]),timeout=15))
    if (set(value)!={'free_bytes','database_size_bytes','database_allocated_bytes'}
            or any(type(v) is not int or v<0 for v in value.values())):
        raise core.CutoverError('INVALID_JSON_HOST_CAPACITY')
    return value


def _properties(client,run_id):
    command=['systemctl','show','seiltanzer-json-history-'+run_id+'.service',
             '-p','LoadState','-p','ActiveState','-p','Result']
    value=_exec(client,shlex.join(command),timeout=15)
    return dict(line.split('=',1) for line in value.splitlines() if '=' in line)


def _terminal_result(read,run_id,application_sha):
    current=read('cutover.result.json')
    if current is None:return None
    if current.get('run_id')!=run_id:
        raise core.CutoverError('JSON_RESULT_EPOCH_MISMATCH')
    if (current.get('phase') not in ('COMMITTED','ORIGINAL_RESTORED','ABORTED_ORIGINAL')
            or current.get('service_started') is not True):return None
    state=read('cutover.preparation.json')
    if (state is None or state.get('run_id')!=run_id
            or state.get('application_sha')!=application_sha):
        raise core.CutoverError('JSON_TERMINAL_PREPARATION_MISMATCH')
    if current['phase']=='ABORTED_ORIGINAL':
        if (state.get('phase')!='ABORTED_ORIGINAL' or state.get('service_started') is not True
                or current.get('epoch')!=state.get('epoch')):
            raise core.CutoverError('JSON_TERMINAL_ABORT_MISMATCH')
    else:
        plan=read('cutover.plan.json')
        native.validate_plan(plan,run_id=run_id,application_sha=application_sha,
                             frozen_identity=state['source']['identity'])
        expected=dict(contract=core.CONTRACT,run_id=run_id,phase=current['phase'],
            plan_sha256=hashlib.sha256(core._canonical(plan)).hexdigest(),service_started=True)
        if read('cutover.journal.json')!=expected:
            raise core.CutoverError('JSON_TERMINAL_JOURNAL_MISMATCH')
    return current


def run(*,expected_sha,run_id,directory,resume=False):
    native.production._validate_epoch(run_id,expected_sha)
    directory=Path(directory).absolute()
    if directory!=directory.resolve() or directory.is_relative_to('/opt/seiltanzer'):
        raise core.CutoverError('OFFHOST_JSON_WORKER_REQUIRED')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    remote_data='/opt/seiltanzer/data/json-rebuild-'+run_id+'/'
    client=_connect(os.environ['SSH_PASSWORD'],attempts=1)
    sftp=None;stage='preflight';handed=False;frozen=None
    def enter(value):
        nonlocal stage
        stage=value;print(json.dumps({'json_history_stage':value}),flush=True)
    try:
        _verify_sha(client,expected_sha)
        sftp=client.open_sftp();sftp.get_channel().settimeout(30)
        def read(name):return previous._remote_read(sftp,name,remote_data=remote_data)
        def write(name,value,**kwargs):
            return previous._remote_write(sftp,name,value,remote_data=remote_data,**kwargs)
        state=read('cutover.preparation.json')
        if resume and state is None:
            link=previous._remote_read(sftp,'json-history.epoch.json')
            cold=read('cold-epoch.json')
            if (link!=dict(contract='sqlite-json-history-epoch-link-v1',run_id=run_id,
                           application_sha=expected_sha) or cold is None
                    or cold.get('run_id')!=run_id or cold.get('application_sha')!=expected_sha
                    or cold.get('contract')!='sqlite-json-cold-epoch-link-v1'):
                raise core.CutoverError('JSON_RESUME_REQUIRES_EXISTING_EPOCH')
        if state is not None and (state.get('run_id')!=run_id or state.get('application_sha')!=expected_sha):
            raise core.CutoverError('JSON_EXISTING_EPOCH_MISMATCH')
        if state is not None and not resume:
            raise core.CutoverError('JSON_EXISTING_EPOCH_REQUIRES_RESUME')
        before=_capacity(client)
        if state is None:
            if not resume:_probe_api(client)
            size=before['database_size_bytes']
            if shutil.disk_usage(directory).free<2*size+_publication_staging_bytes((size,size)):
                raise core.CutoverError('JSON_WORKER_CAPACITY_REFUSED')
        accepted=read('cutover.plan.json')
        handed=accepted is not None
        completed=_terminal_result(read,run_id,expected_sha)
        s3=yandex_image_client() if completed is None else None
        if accepted is not None and completed is None:
            write('cutover.transport.json',previous._private_transport(s3,accepted),replace=True)
            handed=True
        properties=_properties(client,run_id)
        if completed is None and properties.get('ActiveState')!='active':
            if properties.get('LoadState')=='loaded':
                _exec(client,shlex.join(['systemctl','reset-failed',
                    'seiltanzer-json-history-'+run_id+'.service']),timeout=15)
            _exec(client,shlex.join(native.launch_command(run_id=run_id,application_sha=expected_sha)),timeout=20)
            start_end=time.monotonic()+30
            while time.monotonic()<start_end:
                completed=_terminal_result(read,run_id,expected_sha)
                if completed is not None:break
                properties=_properties(client,run_id)
                if properties.get('ActiveState')=='active':break
                if properties.get('ActiveState')=='failed':
                    raise core.CutoverError('JSON_OWNER_START_FAILED')
                time.sleep(.25)
            else:raise core.CutoverError('JSON_OWNER_START_TIMEOUT')
        end=time.monotonic()+native.owner.HARD_SECONDS
        def result():
            current=_terminal_result(read,run_id,expected_sha)
            if current is not None:return current
            if _properties(client,run_id).get('ActiveState')!='active':
                # A result is written immediately before the unit exits.
                current=_terminal_result(read,run_id,expected_sha)
                if current is not None:return current
                raise core.CutoverError('JSON_OWNER_NOT_RUNNING_CHECK_DURABLE_STATE')
            return None
        if not handed and completed is None:
            enter('freeze_current_source')
            while time.monotonic()<end:
                frozen=read('cutover.preparation.json')
                if frozen is not None and frozen.get('phase')=='FROZEN':break
                current=result()
                if current is not None:
                    core._write(directory/'result.json',dict(owner=current));return current
                time.sleep(5)
            else:raise core.CutoverError('JSON_FROZEN_SOURCE_TIMEOUT')
            if (frozen.get('run_id')!=run_id or frozen.get('application_sha')!=expected_sha
                    or type(frozen.get('source')) is not dict):
                raise core.CutoverError('JSON_FROZEN_SOURCE_MISMATCH')
            def assert_frozen():
                current=read('cutover.preparation.json')
                guard=previous._remote_read(sftp,'cutover.active')
                if current!=frozen or guard!=frozen['epoch']:
                    raise core.FencingError('JSON_FROZEN_EPOCH_CHANGED')
                if _properties(client,run_id).get('ActiveState')!='active':
                    raise core.CutoverError('JSON_OWNER_NOT_RUNNING_CHECK_DURABLE_STATE')
            original=directory/'original.sqlite3';hot=directory/'coded.sqlite3'
            enter('capture_current_frozen_bytes')
            proof=previous.capture_frozen(sftp,frozen['source']['identity'],original,
                                        deadline=time.monotonic()+core.MAX_SECONDS)
            assert_frozen()
            enter('lossless_current_json_rebuild')
            built=rebuild_snapshot(original,hot,source_receipt=proof,mode='compress')
            core._write(directory/'hot-build-proof.json',built)
            assert_frozen()
            identities=dict(original=frozen['source']['identity'],
                            hot={key:built[key] for key in core._IDENTITY_FIELDS})
            receipts={};deadline=time.monotonic()+3600
            if shutil.disk_usage(directory).free<_publication_staging_bytes(
                    identities[kind]['database_size_bytes'] for kind in ('original','hot')):
                raise core.CutoverError('JSON_PARALLEL_IMAGE_STAGING_RESERVE_REQUIRED')
            enter('publish_and_readback_both')
            # Separate clients are constructed on this thread. Publisher jobs
            # own only local files and cloud I/O; SFTP stays on the parent.
            other_client=yandex_image_client()
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending={pool.submit(publish_image,path,identities[kind],
                        run_id=run_id,kind=kind,client=s3 if kind=='original' else other_client,
                        deadline=deadline):kind for kind,path in [('original',original),('hot',hot)]}
                    for future in as_completed(pending):
                        receipts[pending[future]]=future.result()
                        assert_frozen()
                # The executor joins both jobs on failure too, before the
                # exception path can request a safe original restart.
            finally:
                with suppress(Exception):other_client.close()
            plan=make_plan(original,hot,built,source_proof=proof,run_id=run_id,
                           application_sha=expected_sha,receipts=receipts)
            core._write(directory/'cutover-plan.json',plan)
            # Only our verified worker input is released after both complete
            # cloud readbacks. The VPS original has not been touched.
            previous.release_owned_capture(original,proof)
            assert_frozen()
            enter('handoff_to_independent_native_owner')
            write('cutover.transport.json',previous._private_transport(s3,plan),replace=True)
            write('cutover.request.json',plan)
            handed=True
        enter('native_verified_transfer')
        current=completed
        if current is None:
            while time.monotonic()<end:
                current=result()
                if current is not None:break
                time.sleep(5)
            else:raise core.CutoverError('JSON_NATIVE_RESULT_TIMEOUT')
        _verify_sha(client,expected_sha)
        report=dict(contract='sqlite-json-current-operation-receipt-v1',run_id=run_id,
            application_sha=expected_sha,owner=current,capacity_before=before,
            capacity_after=_capacity(client),production_authority=False)
        core._write(directory/'result.json',report)
        if current.get('phase')=='COMMITTED' and current.get('service_started') is True:
            _probe_api(client)
            if not resume:
                enter('verify_expanded_complete_research_history')
                from scripts.prepare_research_history import prepare_research_snapshot
                manifest=directory/'coded.manifest.json'
                core._write(manifest,dict(plan['hot_manifest'],git_commit=expected_sha,
                    source='VERIFIED_FROZEN_JSON_REBUILD',production_authority=False))
                prepared=prepare_research_snapshot(hot,manifest,directory/'research.sqlite3',client=s3)
                report['research_history_verified']=prepared
                core._write(directory/'result.json',report)
        return report
    except Exception as exc:
        core._write(directory/'failure.json',dict(stage=stage,
            exception_type=type(exc).__name__,handoff_submitted=handed,
            production_authority=False))
        if sftp is not None and not handed and frozen is not None:
            try:
                if (read('cutover.plan.json') is None and
                        read('cutover.preparation.json')==frozen):
                    write('cutover.abort.json',dict(contract='sqlite-history-preparation-abort-v1',
                        run_id=run_id,application_sha=expected_sha,epoch=frozen['epoch']))
            except Exception:pass
        raise core.CutoverError('JSON_OPERATION_FAILED_CHECK_PUBLIC_RECEIPT') from None
    finally:
        if sftp is not None:sftp.close()
        client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    try:
        report=run(expected_sha=args.expected_sha,run_id=args.run_id,directory=args.directory,resume=args.resume)
        return 0 if report.get('owner',report).get('phase')=='COMMITTED' else 1
    except Exception:
        return 1


if __name__=='__main__':raise SystemExit(main())
