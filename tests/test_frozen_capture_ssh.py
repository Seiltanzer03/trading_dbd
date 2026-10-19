"""Real compressed SSH capture preserves raw pins and uses no SFTP source reads."""
from contextlib import contextmanager
import gzip
import os
import socket
import threading
import time

import pytest
paramiko=pytest.importorskip('paramiko',reason='real SSH proof dependency installed by mandatory CI')
from test_production_history_cutover import frozen_source


@contextmanager
def capture_endpoint(source,mode='normal'):
    stats={'commands':[],'source_sftp_opens':0,'window_bytes':None}
    producers=[]
    release=threading.Event()
    safety=threading.Timer(.7,release.set) if mode in ('no_ack','trickle') else None
    stats['release']=release.set
    class Auth(paramiko.ServerInterface):
        def check_auth_none(self,username):return paramiko.AUTH_SUCCESSFUL
        def check_channel_request(self,kind,chanid):
            return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        def check_channel_exec_request(self,channel,command):
            stats['commands'].append(command)
            stats['window_bytes']=channel.out_window_size
            if command!=b'/bin/gzip -1 -c -- /opt/seiltanzer/data/trades.db':return False
            if mode=='no_ack':release.wait()
            def produce():
                if mode=='empty_members':
                    try:
                        block=gzip.compress(b'',mtime=0)*1000
                        while not channel.closed:
                            channel.sendall(block);time.sleep(.005)
                    except (EOFError,OSError,paramiko.SSHException):pass
                    finally:channel.close()
                    return
                raw=source.read_bytes()+(b'extra' if mode=='extra' else b'')
                wire=gzip.compress(raw,compresslevel=1,mtime=0)
                if mode=='crc':wire=wire[:-8]+bytes([wire[-8]^1])+wire[-7:]
                if mode=='truncated':wire=wire[:-6]
                try:
                    if mode=='trickle':
                        channel.sendall(wire[:10])
                        for byte in wire[10:]:
                            if release.is_set():break
                            channel.sendall(bytes([byte]));time.sleep(.015)
                    else:channel.sendall(wire)
                    channel.send_exit_status(17 if mode=='nonzero' else 0)
                    channel.shutdown_write()
                except (EOFError,OSError,paramiko.SSHException):pass
                finally:channel.close()
            thread=threading.Thread(target=produce,daemon=True);producers.append(thread);thread.start()
            return True
    class Files(paramiko.SFTPServerInterface):
        def open(self,path,flags,attr):
            if path!='/opt/seiltanzer/data/trades.db' or flags!=os.O_RDONLY:return paramiko.SFTP_PERMISSION_DENIED
            stats['source_sftp_opens']+=1
            handle=paramiko.SFTPHandle(flags);handle.readfile=source.open('rb');return handle
    left,right=socket.socketpair()
    server=paramiko.Transport(right);server.add_server_key(paramiko.RSAKey.generate(2048))
    server.set_subsystem_handler('sftp',paramiko.SFTPServer,Files)
    errors=[]
    def serve():
        try:server.start_server(server=Auth())
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    client=paramiko.Transport(left)
    try:
        client.start_client(timeout=5);client.auth_none('disposable-fixture')
        sftp=paramiko.SFTPClient.from_transport(client);sftp.get_channel().settimeout(5)
        if safety:safety.start()
        try:yield sftp,stats
        finally:sftp.close()
    finally:
        release.set()
        if safety:safety.cancel();safety.join()
        client.close();server.close();thread.join(timeout=5)
        for producer in producers:producer.join(timeout=5)
    assert not errors
    assert not any(t.is_alive() for t in producers)


@pytest.mark.parametrize('mode',['normal','crc','truncated','extra','nonzero','empty_members'])
def test_real_ssh_capture_verifies_compressed_stream_and_command(frozen_source,tmp_path,mode):
    from scripts.production_history_cutover import capture_frozen
    import hashlib,sqlite3
    source,_,_=frozen_source
    with sqlite3.connect(source) as conn:
        conn.execute('INSERT INTO actual VALUES(20,?)',(b'latency-bound-source'*400000,))
    raw=source.read_bytes()
    identity={'database_sha256':hashlib.sha256(raw).hexdigest(),'database_size_bytes':len(raw)}
    output=tmp_path/'captured.sqlite3'
    with capture_endpoint(source,mode) as (sftp,stats):
        if mode=='normal':
            receipt=capture_frozen(sftp,identity,output,deadline=time.monotonic()+15)
            assert output.read_bytes()==raw
            assert receipt['database_sha256']==identity['database_sha256']
        else:
            with pytest.raises((ValueError,OSError,EOFError)) as exc:
                capture_frozen(sftp,identity,output,deadline=time.monotonic()+(.5 if mode=='empty_members' else 15))
            if mode=='empty_members':assert str(exc.value)=='TRANSACTION_DEADLINE'
            assert not output.exists()
        # The control SFTP session must survive capture success or rejection.
        assert sftp.normalize('.')=='/'
    assert stats['commands']==[b'/bin/gzip -1 -c -- /opt/seiltanzer/data/trades.db']
    assert stats['source_sftp_opens']==0
    assert stats['window_bytes']==16*1024**2


@pytest.mark.parametrize('mode',['no_ack','trickle'])
def test_absolute_deadline_interrupts_blocked_ssh_without_losing_control(frozen_source,tmp_path,mode):
    from scripts.production_history_cutover import capture_frozen
    import hashlib,sqlite3
    source,raw,identity=frozen_source
    with sqlite3.connect(source) as conn:
        conn.execute('INSERT INTO actual VALUES(20,?)',(os.urandom(100000),))
    raw=source.read_bytes()
    identity={'database_sha256':hashlib.sha256(raw).hexdigest(),'database_size_bytes':len(raw)}
    output=tmp_path/'deadline.sqlite3'
    before=set(threading.enumerate())
    with capture_endpoint(source,mode) as (sftp,stats):
        started=time.monotonic()
        try:
            with pytest.raises(ValueError,match='TRANSACTION_DEADLINE'):
                capture_frozen(sftp,identity,output,deadline=started+.1)
            assert time.monotonic()-started<.5
            assert not output.exists()
        finally:
            # Release the test server's deliberately blocked transport callback.
            # The capture itself must already have returned before this signal.
            # Its independent safety timer bounds failures of the regression.
            stats['release']()
        assert sftp.normalize('.')=='/'
    assert source.read_bytes()==raw
    assert not any(t.name.startswith('frozen-capture-deadline') and t not in before
                   for t in threading.enumerate())
