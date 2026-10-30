"""Finite local history owner; caller must hold the validated host EX lock.

This module has no public root override or production launcher. The canonical
systemd control owns entry, service exclusion and the whole lifetime bound.
"""
import hashlib
import os
from pathlib import Path
import time

from scripts import history_frozen_preparation as prep
from scripts import history_production_plan as pins
from scripts import sqlite_history_transaction as core
from scripts.history_image_transport import SignedImageTransport
from scripts.sqlite_history_partition import _identity

HARD_SECONDS = 10800
START_SECONDS = 900
POLL_SECONDS = 5
_HANDOFF_FAILURE_CODES = frozenset({
    'TRANSACTION_DEADLINE','IMAGE_TRANSFER_DEADLINE','IMAGE_OPEN_FAILED',
    'IMAGE_NETWORK_READ_FAILED','IMAGE_HTTP_CONTRACT_MISMATCH','IMAGE_IDENTITY_MISMATCH',
    'SQLITE_QUICK_CHECK_FAILED','HOT_FOREIGN_KEY_CHECK_FAILED','HOT_HISTORY_STATE_REQUIRED',
    'IMAGE_SIZE_MISMATCH','IMAGE_CHANGED_DURING_VERIFICATION','PREDICTED_RESERVE_REFUSED',
    'PRIVATE_IMAGE_PLAN_MISMATCH','COMPRESSED_IMAGE_SIZE_MISMATCH','RAW_IMAGE_SIZE_MISMATCH',
    'INVALID_IMAGE_GZIP_STREAM','IMAGE_REDIRECT_REFUSED',
})


def _handoff_failure_code(error):
    code=error.args[0] if len(error.args)==1 else None
    return code if type(code) is str and code in _HANDOFF_FAILURE_CODES else 'UNCLASSIFIED_VERIFICATION_FAILURE'


def _abort(root,state,control,reason,*,database=None):
    prep._verify_source(root,state,control,time.monotonic()+core.MAX_SECONDS,database=database)
    state['phase'] = 'ABORTED_ORIGINAL'
    core._write(root/'cutover.preparation.json',state)
    result = prep._finish_abort(root,state,control,database=database)
    result['abort_reason'] = reason
    return result


def _bound_transport(private,plan):
    if type(private) is not dict or set(private) != {'original','hot'}:
        raise core.CutoverError('INVALID_PRIVATE_IMAGE_INPUT')
    for kind,item in private.items():
        if (type(item) is not dict or set(item) != {'url','receipt'}
                or item['receipt'] != {key:value for key,value in plan['artifact_receipts'][kind].items()
                                      if key != 'full_get_verified'}):
            raise core.CutoverError('PRIVATE_IMAGE_PLAN_MISMATCH')


def _run_owned_epoch(root, *, run_id, application_sha, control,
                     transport_factory=SignedImageTransport, wait=time.sleep,
                     database=None,allow_marked_source=False,plan_validator=None):
    """Run one frozen epoch without renewing time or hashing on idle polls."""
    root = Path(root)
    database = Path(database) if database is not None else root/'trades.db'
    validate = plan_validator if plan_validator is not None else pins.validate_plan
    from scripts.history_epoch_retirement import begin_epoch
    begin_epoch(root,run_id=run_id,application_sha=application_sha)
    state = prep._load(root/'cutover.preparation.json',run_id,application_sha)
    accepted = core._read(root/'cutover.plan.json')
    journal = core._read(root/'cutover.journal.json')
    if accepted is not None:
        if state is None or state['source'] is None or state['boot_id'] != prep._boot_id():
            raise core.FencingError('ACCEPTED_PLAN_FROZEN_EPOCH_REQUIRED')
        raw = validate(accepted,run_id=run_id,application_sha=application_sha,
                                 frozen_identity=state['source']['identity'])
    else:
        # No accepted plan means no destructive transfer was authorized.
        if journal is not None:
            raise core.CutoverError('JOURNAL_WITHOUT_ACCEPTED_PLAN')
        public = prep.prepare_epoch(root,run_id=run_id,application_sha=application_sha,control=control,
                                    database=database,allow_marked_source=allow_marked_source)
        if public['phase'] == 'ABORTED_ORIGINAL':
            return public
        state = prep._load(root/'cutover.preparation.json',run_id,application_sha)
        end = state['started_monotonic']+prep.PREPARATION_SECONDS
        while True:
            prep._fence(root,state,control)
            if _identity(database) != state['source']['file_identity']:
                prep._lost(root,state)
            if time.monotonic() >= end:
                return _abort(root,state,control,'PREPARATION_EXPIRED',database=database)
            abort=core._read(root/'cutover.abort.json')
            if abort is not None:
                if abort!=dict(contract='sqlite-history-preparation-abort-v1',run_id=run_id,
                               application_sha=application_sha,epoch=state['epoch']):
                    raise core.CutoverError('ABORT_REQUEST_EPOCH_MISMATCH')
                return _abort(root,state,control,'WORKER_REQUESTED_SAFE_ABORT',database=database)
            if (os.path.lexists(root/'cutover.request.json')
                    and os.path.lexists(root/'cutover.transport.json')):
                try:
                    accepted = core._read(root/'cutover.request.json')
                    raw = validate(accepted,run_id=run_id,application_sha=application_sha,
                                             frozen_identity=state['source']['identity'])
                    private = core._read(root/'cutover.transport.json')
                    _bound_transport(private,accepted)
                    transport = transport_factory(private,deadline=end)
                    # Independent complete GETs. Public full_get_verified flags
                    # never stand in for these actual reads on the VPS.
                    for kind in ('original','hot'):
                        prep._fence(root,state,control)
                        with transport.open_image(kind) as stream:
                            core._consume(stream,accepted[kind],end)
                    prep._fence(root,state,control)
                    core._check_deadline(end)
                    if not pins.capacity_before_transfer(database,
                            accepted['hot']['database_size_bytes'])['admitted']:
                        raise core.CutoverError('PREDICTED_RESERVE_REFUSED')
                    if control.prepare_transfer() is not True:
                        raise core.FencingError('TRANSFER_READERS_NOT_CLOSED')
                except core.FencingError:
                    prep._lost(root,state)
                except Exception as error:
                    result=_abort(root,state,control,'HANDOFF_VERIFICATION_REFUSED',database=database)
                    result['handoff_failure_code']=_handoff_failure_code(error)
                    return result
                # Immutable accepted plan is durable before changing the epoch
                # guard. Either side of this boundary can resume safely.
                core._write(root/'cutover.plan.json',accepted)
                break
            wait(min(POLL_SECONDS,max(0,end-time.monotonic())))

    terminal = journal is not None and journal.get('phase') in ('COMMITTED','ORIGINAL_RESTORED')
    transport = None
    hard_end = state['started_monotonic']+HARD_SECONDS-START_SECONDS
    if not terminal:
        core._check_deadline(hard_end)
        if state['phase'] != 'FROZEN':
            raise core.FencingError('FROZEN_SOURCE_PHASE_REQUIRED')
        epoch = dict(contract=core.CONTRACT,run_id=run_id,
                     plan_sha256=hashlib.sha256(raw).hexdigest())
        guard = core._read(root/'cutover.active')
        if journal is None:
            if guard not in (state['epoch'],epoch):
                prep._lost(root,state)
            if (_identity(database) != state['source']['file_identity']
                    or control.assert_fenced() is not True):
                prep._lost(root,state)
        # A fresh control defaults to frozen-capture mode. Recovery is a
        # destructive transfer too: every pending entry requires zero readers.
        if control.prepare_transfer() is not True:
            raise core.FencingError('TRANSFER_READERS_NOT_CLOSED')
        if journal is None:
            if not pins.capacity_before_transfer(database,
                    accepted['hot']['database_size_bytes'])['admitted']:
                # No journal means no destructive write. Either pre-journal
                # guard is ours and the unchanged original can safely launch.
                core._write(root/'cutover.active',state['epoch'])
                return _abort(root,state,control,'PREDICTED_RESERVE_REFUSED',database=database)
            core._write(root/'cutover.active',epoch)
        private = core._read(root/'cutover.transport.json')
        _bound_transport(private,accepted)
        transport = transport_factory(private,deadline=hard_end)

    def precommit():
        capacity=pins.capacity_after_transfer(database)
        core._write(root/'cutover.capacity.json',capacity)
        return (capacity['admitted']
                and control.verify_hot_runtime(accepted['hot_manifest']) is True)

    return core._execute_validated_transfer(root,raw,transport=transport,control=control,
                                           precommit=precommit,hard_deadline=hard_end,database=database)
