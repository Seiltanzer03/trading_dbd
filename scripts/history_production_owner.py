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


def _abort(root,state,control,reason):
    prep._verify_source(root,state,control,time.monotonic()+core.MAX_SECONDS)
    state['phase'] = 'ABORTED_ORIGINAL'
    core._write(root/'cutover.preparation.json',state)
    result = prep._finish_abort(root,state,control)
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
                     transport_factory=SignedImageTransport, wait=time.sleep):
    """Run one frozen epoch without renewing time or hashing on idle polls."""
    root = Path(root)
    state = prep._load(root/'cutover.preparation.json',run_id,application_sha)
    accepted = core._read(root/'cutover.plan.json')
    journal = core._read(root/'cutover.journal.json')
    if accepted is not None:
        if state is None or state['source'] is None or state['boot_id'] != prep._boot_id():
            raise core.FencingError('ACCEPTED_PLAN_FROZEN_EPOCH_REQUIRED')
        raw = pins.validate_plan(accepted,run_id=run_id,application_sha=application_sha,
                                 frozen_identity=state['source']['identity'])
    else:
        # No accepted plan means no destructive transfer was authorized.
        if journal is not None:
            raise core.CutoverError('JOURNAL_WITHOUT_ACCEPTED_PLAN')
        public = prep.prepare_epoch(root,run_id=run_id,application_sha=application_sha,control=control)
        if public['phase'] == 'ABORTED_ORIGINAL':
            return public
        state = prep._load(root/'cutover.preparation.json',run_id,application_sha)
        end = state['started_monotonic']+prep.PREPARATION_SECONDS
        while True:
            prep._fence(root,state,control)
            if _identity(root/'trades.db') != state['source']['file_identity']:
                prep._lost(root,state)
            if time.monotonic() >= end:
                return _abort(root,state,control,'PREPARATION_EXPIRED')
            if (os.path.lexists(root/'cutover.request.json')
                    and os.path.lexists(root/'cutover.transport.json')):
                try:
                    accepted = core._read(root/'cutover.request.json')
                    raw = pins.validate_plan(accepted,run_id=run_id,application_sha=application_sha,
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
                    if not pins.capacity_before_transfer(root/'trades.db',
                            accepted['hot']['database_size_bytes'])['admitted']:
                        raise core.CutoverError('PREDICTED_RESERVE_REFUSED')
                    if control.prepare_transfer() is not True:
                        raise core.FencingError('TRANSFER_READERS_NOT_CLOSED')
                except core.FencingError:
                    prep._lost(root,state)
                except Exception:
                    return _abort(root,state,control,'HANDOFF_VERIFICATION_REFUSED')
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
        private = core._read(root/'cutover.transport.json')
        _bound_transport(private,accepted)
        transport = transport_factory(private,deadline=hard_end)
        epoch = dict(contract=core.CONTRACT,run_id=run_id,
                     plan_sha256=hashlib.sha256(raw).hexdigest())
        guard = core._read(root/'cutover.active')
        if journal is None:
            if guard not in (state['epoch'],epoch):
                prep._lost(root,state)
            if (_identity(root/'trades.db') != state['source']['file_identity']
                    or control.assert_fenced() is not True
                    or control.prepare_transfer() is not True):
                prep._lost(root,state)
            core._write(root/'cutover.active',epoch)

    def precommit():
        capacity=pins.capacity_after_transfer(root/'trades.db')
        core._write(root/'cutover.capacity.json',capacity)
        return (capacity['admitted']
                and control.verify_hot_runtime(accepted['hot_manifest']) is True)

    return core._execute_validated_transfer(root,raw,transport=transport,control=control,
                                           precommit=precommit,hard_deadline=hard_end)
