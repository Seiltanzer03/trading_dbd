"""Real SSH/SFTP capture must pipeline latency-bound reads without changing pins."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import socket
import threading
import time

import pytest
paramiko=pytest.importorskip('paramiko',reason='real SFTP proof dependency installed by mandatory CI')
from paramiko.sftp import CMD_READ
from test_production_history_cutover import frozen_source


@contextmanager
def latency_sftp(source):
    stats={'outstanding':0,'peak':0}
    lock=threading.Lock()
    class Auth(paramiko.ServerInterface):
        def check_auth_none(self,username):return paramiko.AUTH_SUCCESSFUL
        def check_channel_request(self,kind,chanid):
            return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    class Files(paramiko.SFTPServerInterface):
        def open(self,path,flags,attr):
            if path!='/opt/seiltanzer/data/trades.db' or flags!=os.O_RDONLY:
                return paramiko.SFTP_PERMISSION_DENIED
            handle=paramiko.SFTPHandle(flags)
            handle.readfile=source.open('rb')
            return handle
    class DelayedReplies(paramiko.SFTPServer):
        def __init__(self,*args,**kwargs):
            self.pool=ThreadPoolExecutor(max_workers=64)
            self.reply_lock=threading.RLock()
            super().__init__(*args,**kwargs)
        def _send_packet(self,*args,**kwargs):
            with self.reply_lock:return super()._send_packet(*args,**kwargs)
        def _process(self,kind,number,message):
            if kind!=CMD_READ:return super()._process(kind,number,message)
            with lock:
                stats['outstanding']+=1
                stats['peak']=max(stats['peak'],stats['outstanding'])
            def reply():
                # Model network/server response latency; requests may arrive
                # together while replies wait. No timing assertion is used.
                time.sleep(.02)
                # SFTPHandle seek/read state must not race in this fixture.
                with self.reply_lock,lock:
                    try:super(DelayedReplies,self)._process(kind,number,message)
                    finally:stats['outstanding']-=1
            self.pool.submit(reply)
        def finish_subsystem(self):
            self.pool.shutdown(wait=True)
            super().finish_subsystem()
    left,right=socket.socketpair()
    server=paramiko.Transport(right)
    server.add_server_key(paramiko.RSAKey.generate(2048))
    server.set_subsystem_handler('sftp',DelayedReplies,Files)
    errors=[]
    def serve():
        try:server.start_server(server=Auth())
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    client=paramiko.Transport(left)
    try:
        client.start_client(timeout=5);client.auth_none('disposable-fixture')
        sftp=paramiko.SFTPClient.from_transport(client)
        sftp.get_channel().settimeout(5)
        try:yield sftp,stats
        finally:sftp.close()
    finally:
        client.close();server.close();thread.join(timeout=5)
    assert not errors


def test_real_sftp_frozen_capture_uses_bounded_parallel_reads(frozen_source,tmp_path):
    from scripts.production_history_cutover import capture_frozen
    import hashlib,sqlite3
    source,_,_=frozen_source
    with sqlite3.connect(source) as conn:
        conn.execute('INSERT INTO actual VALUES(20,?)',(b'latency-bound-source'*400000,))
    raw=source.read_bytes()
    identity={'database_sha256':hashlib.sha256(raw).hexdigest(),'database_size_bytes':len(raw)}
    output=tmp_path/'captured.sqlite3'
    with latency_sftp(source) as (sftp,stats):
        receipt=capture_frozen(sftp,identity,output,deadline=time.monotonic()+15)
    assert output.read_bytes()==raw
    assert receipt['database_sha256']==identity['database_sha256']
    assert 2<=stats['peak']<=32,'capture serializes each 32KiB SFTP read behind roundtrip latency'
