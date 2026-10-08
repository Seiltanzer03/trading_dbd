"""Run and stop one run-scoped SQLite origin; never touch database files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import time


def identity(pid: int):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        if fields[0] == 'Z':
            return None
        executable=os.readlink(f'/proc/{pid}/exe')
        arguments=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return fields[19],executable,[a.decode() for a in arguments if a]
    except (FileNotFoundError,ProcessLookupError):
        return None


def targets(binary: Path,state: Path):
    found=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.geteuid():
                continue
        except FileNotFoundError:
            continue
        pid=int(entry.name)
        current=identity(pid)
        if current is None:
            continue
        ticks,executable,args=current
        transition=(str(binary) in args and (
            Path(executable).name in ('nice','ionice') or
            ('run' in args and str(state) in args and Path(executable).name.startswith('python'))
        ))
        if executable == str(binary) or transition:
            found.append((pid,ticks))
    return found


def run(binary: Path,state: Path,args: list[str]):
    stop=state.with_name('origin.stop')
    if '--version' not in args:
        if stop.exists():
            raise RuntimeError('origin stopped before start')
        birth=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]
        with state.open('x') as stream:
            json.dump(dict(pid=os.getpid(),start_ticks=birth),stream)
        if stop.exists():
            raise RuntimeError('origin stopped before exec')
    os.execvp('nice',['nice','-n','19','ionice','-c','3',str(binary),*args])


def cleanup(binary: Path,state: Path):
    if int(Path('/proc/self/stat').read_text().split(' ',1)[0]) != os.getpid():
        raise RuntimeError('Origin cleanup requires matching PID/proc namespace')
    state.with_name('origin.stop').write_text('stop\n')
    recorded=json.loads(state.read_text()) if state.exists() else None
    signalled={}
    deadline=time.monotonic()+7
    empty_scans=0
    while time.monotonic() < deadline:
        readers=targets(binary,state)
        if not readers:
            empty_scans+=1
            if empty_scans >= 2:
                print('ORIGIN_READER_EXIT_CONFIRMED=1',flush=True)
                return
        else:
            empty_scans=0
        for pid,ticks in readers:
            if recorded and recorded.get('pid') == pid and recorded.get('start_ticks') != ticks:
                raise RuntimeError('Origin PID identity changed; refusing signal')
            now=time.monotonic()
            key=(pid,ticks)
            sig=signal.SIGTERM if key not in signalled else signal.SIGKILL
            if key in signalled and now-signalled[key] < 3:
                continue
            # Recheck birth identity immediately before signalling a PID.
            current=identity(pid)
            if current is None:
                continue
            if current[0] != ticks:
                raise RuntimeError('Origin PID reused before signal')
            try:
                os.kill(pid,sig)
            except ProcessLookupError:
                continue
            signalled.setdefault(key,now)
        time.sleep(.1)
    raise RuntimeError('Origin reader exit could not be confirmed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['run','cleanup'])
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    options,args=parser.parse_known_args()
    binary=options.binary.absolute()
    state=options.state.absolute()
    if binary.name != 'sqlite3_rsync' or state.name != 'origin.json' or binary.parent != state.parent:
        raise ValueError('Origin executable and identity must share the run directory')
    if options.mode == 'run':
        run(binary,state,args[1:] if args[:1] == ['--'] else args)
    else:
        if args:
            raise ValueError('Unexpected cleanup arguments')
        cleanup(binary,state)


if __name__ == '__main__':
    main()
