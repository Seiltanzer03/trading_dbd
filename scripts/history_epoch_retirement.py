"""Archive only safely terminal original epochs under the caller's EX lock.

No database bytes are changed. A durable move intent makes metadata retirement
resumable; active, committed and fence-lost epochs can never receive new clocks.
"""
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat

from scripts import history_frozen_preparation as prep
from scripts import sqlite_history_transaction as core
from scripts.history_storage_loader import history_state

FILES=('cutover.preparation.json','cutover.plan.json','cutover.request.json',
       'cutover.transport.json','cutover.journal.json','cutover.result.json',
       'cutover.capacity.json','cutover.abort.json')
CONTRACT='sqlite-history-terminal-epoch-retirement-v1'


def _private_directory(path):
    if not os.path.lexists(path):path.mkdir(mode=0o700)
    info=path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
            or stat.S_IMODE(info.st_mode)!=0o700):
        raise core.CutoverError('UNSAFE_EPOCH_ARCHIVE_DIRECTORY')


def begin_epoch(root,*,run_id,application_sha):
    root=Path(root)
    if (not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',run_id)
            or not re.fullmatch('[0-9a-f]{40}',application_sha)):
        raise core.CutoverError('INVALID_NEW_EPOCH')
    intent=core._read(root/'cutover.reset.json')
    old=core._read(root/'cutover.preparation.json')
    if intent is None:
        if old is None or old.get('run_id')==run_id:return
        if os.path.lexists(root/'cutover.active'):
            raise core.CutoverError('UNFINISHED_EPOCH_CANNOT_BE_RETIRED')
        old=prep._load(root/'cutover.preparation.json',old.get('run_id'),old.get('application_sha'))
        journal=core._read(root/'cutover.journal.json')
        terminal=old['phase']=='ABORTED_ORIGINAL' and old['service_started'] is True
        if journal is not None:
            plan=core._read(root/'cutover.plan.json')
            terminal=(type(plan) is dict and journal==dict(contract=core.CONTRACT,
                run_id=old['run_id'],plan_sha256=hashlib.sha256(core._canonical(plan)).hexdigest(),
                phase='ORIGINAL_RESTORED',service_started=True))
        if not terminal:raise core.CutoverError('UNFINISHED_EPOCH_CANNOT_BE_RETIRED')
        conn=sqlite3.connect((root/'trades.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        try:
            conn.execute('PRAGMA query_only=ON')
            if conn.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(history_state.TABLE,)).fetchone():
                raise core.CutoverError('MARKED_EPOCH_CANNOT_BE_RETIRED')
        finally:conn.close()
        files={}
        for name in FILES:
            value=core._read(root/name)
            if value is not None:files[name]=hashlib.sha256(core._canonical(value)).hexdigest()
        destination=root/'cutover-attempts'/old['run_id']
        if os.path.lexists(destination):raise core.CutoverError('EPOCH_ARCHIVE_ALREADY_EXISTS')
        intent=dict(contract=CONTRACT,old_run_id=old['run_id'],new_run_id=run_id,
                    application_sha=application_sha,files=files)
        core._write(root/'cutover.reset.json',intent)
    if (set(intent)!={'contract','old_run_id','new_run_id','application_sha','files'}
            or intent['contract']!=CONTRACT or intent['new_run_id']!=run_id
            or intent['application_sha']!=application_sha or intent['old_run_id']==run_id
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',intent['old_run_id'])
            or type(intent['files']) is not dict or not intent['files']
            or any(name not in FILES or not re.fullmatch('[0-9a-f]{64}',pin)
                   for name,pin in intent['files'].items())
            or os.path.lexists(root/'cutover.active')):
        raise core.CutoverError('INVALID_EPOCH_RETIREMENT_INTENT')
    archive=root/'cutover-attempts'
    _private_directory(archive)
    archive=archive/intent['old_run_id'];_private_directory(archive)
    for name,pin in intent['files'].items():
        source,target=root/name,archive/name
        current=core._read(source);saved=core._read(target)
        if (current is None)==(saved is None):
            raise core.CutoverError('AMBIGUOUS_EPOCH_ARCHIVE_MOVE')
        value=current if current is not None else saved
        if hashlib.sha256(core._canonical(value)).hexdigest()!=pin:
            raise core.CutoverError('EPOCH_METADATA_CHANGED_DURING_RETIREMENT')
        if current is not None:
            os.replace(source,target)
            core._fsync_directory(root);core._fsync_directory(archive)
    os.replace(root/'cutover.reset.json',archive/'retirement.json')
    core._fsync_directory(root);core._fsync_directory(archive)
