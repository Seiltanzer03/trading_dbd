"""Separate finite JSON-storage epoch; never retires the committed cold epoch.

The canonical production control owns the EX host lock and exact installed
code. Only this new operation's private journal reaches the transfer core.
"""
import copy
import hashlib
import os
from pathlib import Path
import stat
import time

from scripts import history_frozen_preparation as prep
from scripts import history_production_owner as owner
from scripts import history_production_plan as pins
from scripts import history_production_control as production
from scripts import history_service_fence as fence
from scripts import sqlite_history_transaction as core
from scripts.history_image_transport import SignedImageTransport
from scripts.history_storage_loader import history_state

CONTRACT='sqlite-history-json-rebuild-plan-v1'


def validate_plan(plan,*,run_id,application_sha,frozen_identity):
    """Reuse image/capacity contracts without weakening initial cold admission."""
    if not isinstance(plan,dict) or plan.get('contract')!=CONTRACT:
        raise core.CutoverError('INVALID_JSON_REBUILD_PLAN')
    translated=copy.deepcopy(plan)
    original=translated.pop('original_manifest',None)
    hot=translated.get('hot_manifest')
    if (type(hot) is not dict or history_state.PAYLOAD_PIN_FIELD not in hot
            or type(original) is not dict):
        raise core.CutoverError('JSON_REBUILD_STORAGE_PINS_REQUIRED')
    payload_pin=hot.pop(history_state.PAYLOAD_PIN_FIELD)
    codec=history_state._payload_codec()
    expected_pin=hashlib.sha256(codec._canonical(codec._definition()).encode()).hexdigest()
    expected_original={key:value for key,value in hot.items() if key not in core._IDENTITY_FIELDS}
    expected_original.update(frozen_identity)
    if payload_pin!=expected_pin or original!=expected_original:
        raise core.CutoverError('JSON_REBUILD_ORIGINAL_HISTORY_PINS_DIFFER')
    translated['contract']=pins.CONTRACT
    pins.validate_plan(translated,run_id=run_id,application_sha=application_sha,
                       frozen_identity=frozen_identity)
    return core._canonical(plan)


def _metadata(data,run_id,application_sha,*,create=True):
    production._validate_epoch(run_id,application_sha)
    data=Path(data)
    if data.absolute()!=data.resolve():raise core.FencingError('UNSAFE_JSON_DATA_PATH')
    path=data/('json-rebuild-'+run_id)
    if create and not os.path.lexists(path):path.mkdir(mode=0o700)
    info=path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
            or stat.S_IMODE(info.st_mode)!=0o700):
        raise core.FencingError('UNSAFE_JSON_EPOCH_DIRECTORY')
    for name in production.STATE_FILES:
        item=path/name
        if os.path.lexists(item):
            info=core._regular(item)
            if info.st_uid!=os.geteuid() or info.st_mode&0o077:
                raise core.FencingError('UNSAFE_JSON_EPOCH_FILE')
    return path


def _replace_safe_original_link(data,metadata,previous,replacement,cold_sha):
    if (type(previous) is not dict or set(previous)!={'contract','run_id','application_sha'}
            or previous.get('contract')!='sqlite-json-history-epoch-link-v1'
            or previous.get('run_id')==replacement['run_id']
            or os.path.lexists(data/'cutover.active')
            or fence._json_epoch_reason(data/'json-history.epoch.json') is not None):
        raise core.FencingError('ANOTHER_JSON_EPOCH_LINK_EXISTS')
    prior=_metadata(data,previous['run_id'],previous['application_sha'],create=False)
    state=prep._load(prior/'cutover.preparation.json',previous['run_id'],previous['application_sha'])
    journal=fence._journal(prior/'cutover.journal.json')
    safe=(state is not None and state['phase']=='ABORTED_ORIGINAL' and state['service_started'] is True)
    if journal is not None:
        safe=journal['phase']=='ORIGINAL_RESTORED' and journal['service_started'] is True
    if not safe:raise core.FencingError('ONLY_SAFE_ORIGINAL_JSON_EPOCH_CAN_ADVANCE')
    intent=dict(contract='sqlite-json-original-epoch-transition-v1',previous=previous,
                replacement=replacement,cold_journal_sha256=cold_sha)
    existing=core._read(metadata/'previous-json-epoch.json')
    if existing is not None and existing!=intent:
        raise core.FencingError('JSON_EPOCH_TRANSITION_CHANGED')
    # Old metadata is retained byte-for-byte. The transition intent precedes
    # the atomic, fsynced link update; current data is freshly captured later.
    if existing is None:core._write(metadata/'previous-json-epoch.json',intent)
    core._write(data/'json-history.epoch.json',replacement)


class _Control:
    def __init__(self,data,metadata,run_id,application_sha,real):
        self.data,self.root,self.run_id,self.sha,self.real=data,metadata,run_id,application_sha,real
        old=fence._journal(data/'cutover.journal.json')
        if old is None or old['phase']!='COMMITTED' or not old['service_started']:
            raise core.FencingError('COMMITTED_COLD_EPOCH_REQUIRED')
        self.cold_sha=hashlib.sha256((data/'cutover.journal.json').read_bytes()).hexdigest()
        pin=dict(contract='sqlite-json-cold-epoch-link-v1',run_id=run_id,
                 application_sha=application_sha,cold_journal_sha256=self.cold_sha)
        existing=core._read(metadata/'cold-epoch.json')
        if existing is not None and existing!=pin:
            raise core.FencingError('COLD_EPOCH_LINK_CHANGED')
        state=prep._load(metadata/'cutover.preparation.json',run_id,application_sha)
        if state is None and os.path.lexists(data/'cutover.active'):
            raise core.FencingError('ANOTHER_CANONICAL_GUARD_EXISTS')
        if existing is None:core._write(metadata/'cold-epoch.json',pin)
        link=dict(contract='sqlite-json-history-epoch-link-v1',run_id=run_id,
                  application_sha=application_sha)
        active=core._read(data/'json-history.epoch.json')
        if active is not None and active!=link:
            _replace_safe_original_link(data,metadata,active,link,self.cold_sha)
        if active is None:core._write(data/'json-history.epoch.json',link)

    def _epoch(self):
        if _metadata(self.data,self.run_id,self.sha)!=self.root:
            raise core.FencingError('JSON_EPOCH_DIRECTORY_CHANGED')
        if hashlib.sha256((self.data/'cutover.journal.json').read_bytes()).hexdigest()!=self.cold_sha:
            raise core.FencingError('COMMITTED_COLD_EPOCH_CHANGED')
        state=prep._load(self.root/'cutover.preparation.json',self.run_id,self.sha)
        if state is None:raise core.FencingError('JSON_PREPARATION_REQUIRED')
        return state

    def stop_writers(self):
        epoch=self._epoch()['epoch']
        current=core._read(self.data/'cutover.active')
        if current is not None and current!=epoch:
            raise core.FencingError('CANONICAL_GUARD_OWNERSHIP_LOST')
        if current is None:core._write(self.data/'cutover.active',epoch)
        return self.real.stop_writers()

    def assert_fenced(self):
        return (core._read(self.data/'cutover.active')==self._epoch()['epoch']
                and self.real.assert_fenced() is True)

    def assert_writers_closed(self):return self.real.assert_writers_closed()
    def prepare_transfer(self):return self.assert_fenced() and self.real.prepare_transfer()
    def verify_hot_runtime(self,manifest):return self.real.verify_hot_runtime(manifest)

    def start_verified_database(self):
        state=self._epoch()
        if os.path.lexists(self.root/'cutover.active'):
            raise core.FencingError('JSON_LOCAL_GUARD_STILL_ACTIVE')
        journal=fence._journal(self.root/'cutover.journal.json')
        if journal is None:
            if state['phase']!='ABORTED_ORIGINAL':
                raise core.FencingError('JSON_TERMINAL_STATE_REQUIRED')
        else:
            accepted=core._read(self.root/'cutover.plan.json')
            if (journal['phase'] not in ('COMMITTED','ORIGINAL_RESTORED')
                    or journal['run_id']!=self.run_id or accepted is None
                    or journal['plan_sha256']!=hashlib.sha256(core._canonical(accepted)).hexdigest()):
                raise core.FencingError('JSON_TERMINAL_PLAN_MISMATCH')
        current=core._read(self.data/'cutover.active')
        if current is not None:
            if current!=state['epoch']:raise core.FencingError('CANONICAL_GUARD_OWNERSHIP_LOST')
            (self.data/'cutover.active').unlink();core._fsync_directory(self.data)
        # Once the canonical guard is released, only forward service recovery
        # is permitted. The shared core never rolls back a committed image.
        return self.real.start_verified_database()


def _run_json_epoch(data,metadata,*,run_id,application_sha,control,
                    transport_factory=SignedImageTransport,wait=time.sleep):
    data=Path(data)
    if Path(metadata)!=_metadata(data,run_id,application_sha):
        raise core.FencingError('CANONICAL_JSON_METADATA_REQUIRED')
    metadata=Path(metadata)
    wrapped=_Control(data,metadata,run_id,application_sha,control)
    def validate(plan,**kwargs):
        raw=validate_plan(plan,**kwargs)
        if core._read(metadata/'cutover.plan.json') is None:
            state=wrapped._epoch()
            core._verify_database(data/'trades.db',kwargs['frozen_identity'],
                min(state['started_monotonic']+prep.PREPARATION_SECONDS,
                    time.monotonic()+core.MAX_SECONDS),manifest=plan['original_manifest'])
        return raw
    return owner._run_owned_epoch(metadata,run_id=run_id,application_sha=application_sha,
        control=wrapped,database=data/'trades.db',allow_marked_source=True,
        plan_validator=validate,transport_factory=transport_factory,wait=wait)


def launch_command(*,run_id,application_sha):
    command=production.launch_command(run_id=run_id,application_sha=application_sha)
    return ['--unit=seiltanzer-json-history-'+run_id+'.service' if arg.startswith('--unit=')
            else 'scripts.json_history_owner' if arg=='scripts.history_production_control' else arg
            for arg in command]


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--application-sha',required=True)
    args=parser.parse_args()
    production._validate_epoch(args.run_id,args.application_sha)
    try:
        with production.production_control(production.ROOT,application_sha=args.application_sha) as control:
            data=production.ROOT/'data';metadata=_metadata(data,args.run_id,args.application_sha)
            result=_run_json_epoch(data,metadata,run_id=args.run_id,
                application_sha=args.application_sha,control=control)
            core._write(metadata/'cutover.result.json',result)
        return 0 if result['phase']=='COMMITTED' else 1
    except Exception:
        return 1  # No private request, provider message or URL enters journald.


if __name__=='__main__':raise SystemExit(main())
