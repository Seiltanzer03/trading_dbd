"""Canonical VPS service/lock control for one finite history owner."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
from urllib import request

from scripts import history_service_fence
from scripts import sqlite_history_transaction as core

ROOT = Path('/opt/seiltanzer')
DATABASE = ROOT/'data/trades.db'
SERVICE = 'seiltanzer.service'
EXTERNAL_GUARD = Path('/usr/local/lib/seiltanzer-history-start-guard.py')
OWNER_UID = 0
STATE_FILES = ('cutover.active','cutover.preparation.json','cutover.plan.json',
               'cutover.request.json','cutover.transport.json','cutover.journal.json',
               'cutover.result.json','cutover.capacity.json','cutover.reset.json','cutover.abort.json')


def validate_owned_data(data):
    """Production-only ownership boundary, repeated before fenced operations."""
    data=Path(data)
    info=data.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=OWNER_UID
            or stat.S_IMODE(info.st_mode)!=0o700):
        raise core.FencingError('UNSAFE_PRODUCTION_DATA_DIRECTORY')
    for name in ('trades.db','trades.db-wal','trades.db-shm','trades.db-journal',*STATE_FILES):
        path=data/name
        if not os.path.lexists(path):continue
        info=core._regular(path)
        mask=0o077 if name in STATE_FILES else 0o022
        if info.st_uid!=OWNER_UID or info.st_mode & mask:
            raise core.FencingError('PRODUCTION_INPUT_OWNER_OR_MODE_CHANGED')


def _run(*args,timeout=15):
    try:
        return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout.strip()
    except Exception:
        raise core.CutoverError('HISTORY_HOST_CONTROL_FAILED') from None


def _validate_epoch(run_id,application_sha):
    if (type(run_id) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',run_id)
            or type(application_sha) is not str or not re.fullmatch('[0-9a-f]{40}',application_sha)):
        raise core.CutoverError('INVALID_NATIVE_OWNER_EPOCH')


def launch_command(*,run_id,application_sha):
    _validate_epoch(run_id,application_sha)
    return ['/usr/bin/systemd-run','--no-block',
        '--unit=seiltanzer-history-'+run_id+'.service',
        '--property=Type=exec','--property=RuntimeMaxSec=10800',
        '--property=TimeoutStopSec=15','--property=KillMode=control-group',
        '--property=Restart=no','--property=UMask=0077',
        '--property=StandardOutput=null','--property=StandardError=null',
        '--working-directory='+str(ROOT),'/usr/bin/python3','-m',
        'scripts.history_production_control','--run-id',run_id,'--application-sha',application_sha]


def database_handles(database,*,exclude_pid):
    """Supplementary actual FD proof; all known launchers also obey EX/guard."""
    database=Path(database)
    if int(Path('/proc/self/stat').read_text().split(' ',1)[0]) != os.getpid():
        raise core.FencingError('NATIVE_PROC_NAMESPACE_REQUIRED')
    wanted={str(database)+suffix for suffix in ('','-wal','-shm','-journal')}
    found=[]
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name)==exclude_pid:continue
        try:
            descriptors=list((process/'fd').iterdir())
        except (FileNotFoundError,ProcessLookupError):continue
        except PermissionError:
            raise core.FencingError('DATABASE_HANDLE_INVENTORY_INCOMPLETE') from None
        for descriptor in descriptors:
            try:
                target=os.readlink(descriptor)
                if target.removesuffix(' (deleted)') not in wanted:continue
                fields=dict(line.split(':',1) for line in
                            (process/'fdinfo'/descriptor.name).read_text().splitlines() if ':' in line)
                flags=int(fields['flags'].strip(),8)
            except (FileNotFoundError,ProcessLookupError):continue
            except (PermissionError,ValueError,KeyError):
                raise core.FencingError('DATABASE_HANDLE_IDENTITY_UNAVAILABLE') from None
            found.append(dict(pid=int(process.name),fd=int(descriptor.name),
                              writable=(flags & os.O_ACCMODE) != os.O_RDONLY,path=target))
    return found


class ProductionControl:
    def __init__(self,lock_fd,application_sha):
        self.lock_fd=lock_fd
        self.application_sha=application_sha
        self.lock_identity=os.fstat(lock_fd)
        self.transfer=False
        self.cgroup=Path('/sys/fs/cgroup/system.slice/seiltanzer.service')
        self._verify_installation()

    def _verify_installation(self):
        validate_owned_data(ROOT/'data')
        if _run('git','-C',str(ROOT),'rev-parse','HEAD') != self.application_sha:
            raise core.CutoverError('PRODUCTION_APPLICATION_SHA_MISMATCH')
        for path in (ROOT/'scripts/history_service_fence.py',EXTERNAL_GUARD):
            info=core._regular(path)
            if info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 65536:
                raise core.CutoverError('UNSAFE_INSTALLED_HISTORY_GUARD')
        if EXTERNAL_GUARD.read_bytes() != (ROOT/'scripts/history_service_fence.py').read_bytes():
            raise core.CutoverError('INSTALLED_HISTORY_GUARD_MISMATCH')
        properties=_run('systemctl','show',SERVICE,'-p','ExecCondition','-p','KillMode')
        if (str(EXTERNAL_GUARD) not in properties or 'KillMode=control-group' not in properties):
            raise core.CutoverError('LOADED_SERVICE_FENCE_REQUIRED')

    def stop_writers(self):
        _run('systemctl','stop',SERVICE,timeout=120)
        status=_run('systemctl','show',SERVICE,'-p','ActiveState','-p','MainPID')
        return ('MainPID=0' in status and any(value in status for value in
                ('ActiveState=inactive','ActiveState=failed')) and self.assert_fenced())

    def assert_fenced(self):
        validate_owned_data(ROOT/'data')
        info=os.fstat(self.lock_fd)
        current=(ROOT/'.deployment-git-maintenance.lock').lstat()
        if ((info.st_ino,info.st_dev)!=(current.st_ino,current.st_dev)
                or (info.st_ino,info.st_dev)!=(self.lock_identity.st_ino,self.lock_identity.st_dev)
                or not os.path.lexists(ROOT/'data/cutover.active')):
            return False
        tasks=self.cgroup/'cgroup.procs'
        return not tasks.exists() or not tasks.read_text().strip()

    def assert_writers_closed(self):
        handles=database_handles(DATABASE,exclude_pid=os.getpid())
        # During frozen raw SFTP capture only read descriptors are allowed.
        # The native transfer phase requires every external descriptor closed.
        return not handles if self.transfer else not any(item['writable'] for item in handles)

    def prepare_transfer(self):
        self.transfer=True
        return self.assert_fenced() and self.assert_writers_closed()

    def verify_hot_runtime(self,manifest):
        # Reuse the bounded offline reader/capability validator. These absent
        # paths skip *only* epoch gating in this call; the real guard remains
        # present and prevents systemd start throughout verification.
        absent=ROOT/'data/.cutover-offline-check-absent'
        if os.path.lexists(absent):return False
        result=history_service_fence.check_start(DATABASE,ROOT,guard=absent,journal=absent)
        return result.get('reason') == 'VERIFIED_HOT_RUNTIME_CAPABILITY'

    def start_verified_database(self):
        if os.path.lexists(ROOT/'data/cutover.active'):
            raise core.FencingError('GUARDED_DATABASE_CANNOT_START')
        if _run('git','-C',str(ROOT),'rev-parse','HEAD') != self.application_sha:
            raise core.CutoverError('PRODUCTION_APPLICATION_SHA_MISMATCH')
        if not history_service_fence.check_start(DATABASE,ROOT)['start_allowed']:
            raise core.CutoverError('DATABASE_START_CHECK_REFUSED')
        _run('systemctl','start','--no-block',SERVICE)
        end=time.monotonic()+900
        while time.monotonic()<end:
            try:
                with request.urlopen('http://127.0.0.1:8790/api/state',timeout=3) as response:
                    if response.status==200:
                        response.read(1)
                        return _run('systemctl','is-active',SERVICE)=='active'
            except Exception:pass
            time.sleep(2)
        return False


@contextmanager
def production_control(root,*,application_sha):
    root=Path(root)
    if root != ROOT or root.absolute()!=root.resolve():
        raise core.CutoverError('CANONICAL_PRODUCTION_ROOT_REQUIRED')
    if os.geteuid()!=0:
        raise core.CutoverError('PRODUCTION_ROOT_USER_REQUIRED')
    for directory in (ROOT,ROOT/'data'):
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise core.CutoverError('UNSAFE_PRODUCTION_DIRECTORY')
    path=ROOT/'.deployment-git-maintenance.lock'
    fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0 or info.st_mode & 0o022:
            raise core.CutoverError('UNSAFE_PRODUCTION_MAINTENANCE_LOCK')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise core.CutoverError('PRODUCTION_MAINTENANCE_ALREADY_OWNED') from None
        # Narrow only the already root-owned directory, under the host lock.
        # Application runs as root; no file/data is replaced or discarded.
        os.chmod(ROOT/'data',0o700)
        yield ProductionControl(fd,application_sha)
    finally:os.close(fd)


def main():
    import argparse
    from scripts.history_production_owner import _run_owned_epoch
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--application-sha',required=True)
    args=parser.parse_args()
    _validate_epoch(args.run_id,args.application_sha)
    try:
        with production_control(ROOT,application_sha=args.application_sha) as control:
            from scripts.history_epoch_retirement import begin_epoch
            begin_epoch(ROOT/'data',run_id=args.run_id,application_sha=args.application_sha)
            attempt=secrets.token_hex(16)
            core._write(ROOT/'data/cutover.result.json',dict(
                contract='sqlite-history-native-owner-attempt-v1',run_id=args.run_id,
                application_sha=args.application_sha,owner_attempt=attempt,phase='OWNER_RUNNING'))
            try:
                result=_run_owned_epoch(ROOT/'data',run_id=args.run_id,
                                        application_sha=args.application_sha,control=control)
            except Exception:
                core._write(ROOT/'data/cutover.result.json',dict(
                    contract='sqlite-history-native-owner-attempt-v1',run_id=args.run_id,
                    application_sha=args.application_sha,owner_attempt=attempt,
                    phase='OWNER_FAILED_CHECK_DURABLE_STATE'))
                return 1
            result['owner_attempt']=attempt
            core._write(ROOT/'data/cutover.result.json',result)
        return 0 if result['phase']=='COMMITTED' else 1
    except Exception:
        # Never print provider exceptions/private request data to journalctl.
        return 1


if __name__=='__main__':raise SystemExit(main())
