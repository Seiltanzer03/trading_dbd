"""Read the already-installed official archive over the existing SSH channel."""
import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import shlex

MAX_BYTES=16_000_000
REMOTE_EXPORT=r'''
import base64,gzip,json
from pathlib import Path
p=Path('/opt/seiltanzer/data/research/official_macro_historical_offhost_latest.json')
try:
 if p.stat().st_size>16000000:raise ValueError('OFFICIAL_MACRO_ARCHIVE_INPUT_OVER_BOUND')
 with p.open('rb') as f:raw=f.read(16000001)
 if len(raw)>16000000:raise ValueError('OFFICIAL_MACRO_ARCHIVE_INPUT_OVER_BOUND')
except FileNotFoundError:
 raw=json.dumps({'archive_export_error':'OFFICIAL_MACRO_ARCHIVE_NOT_FOUND','available':False,'read_only':True}).encode()
except (OSError,ValueError) as exc:
 reason=str(exc) if isinstance(exc,ValueError) else 'OFFICIAL_MACRO_ARCHIVE_READ_FAILED'
 raw=json.dumps({'archive_export_error':reason,'available':False,'read_only':True}).encode()
print(base64.b64encode(gzip.compress(raw)).decode())
'''


def read_archive(client,output):
    try:
        _,stdout,stderr=client.exec_command('python3 -c '+shlex.quote(REMOTE_EXPORT),timeout=25)
        encoded=stdout.read(12_000_001)
        if stdout.channel.recv_exit_status() or len(encoded)>12_000_000:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_READ_FAILED')
        compressed=base64.b64decode(encoded.strip(),validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
            raw=reader.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_INPUT_OVER_BOUND')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('OFFICIAL_MACRO_ARCHIVE_READ_FAILED')
    except Exception:
        # This boundary handles optional external SSH/channel/decoding I/O;
        # never log exception text that could contain connection credentials.
        value={'archive_export_error':'OFFICIAL_MACRO_ARCHIVE_READ_FAILED','available':False,'read_only':True}
        raw=json.dumps(value).encode()
    Path(output).write_bytes(raw)
    return {'read_only':True,'new_observations':False,'bytes':len(raw),
        'source_validated':False,'validation_required_on_runner':True,
        'available':'archive_export_error' not in value,
        'reason':value.get('archive_export_error'), 'original_source_sha':value.get('expected_sha')}


def main():
    from scripts.production_ede_offload import _connect
    parser=argparse.ArgumentParser();parser.add_argument('--password',required=True)
    parser.add_argument('--output',required=True);args=parser.parse_args()
    client=None
    try:
        client=_connect(args.password,attempts=1)
    except Exception:
        # A macro-only connection failure cannot suppress other experts.
        value={'archive_export_error':'OFFICIAL_MACRO_ARCHIVE_READ_FAILED','available':False,'read_only':True}
        Path(args.output).write_text(json.dumps(value))
        print('OFFICIAL_MACRO_ARCHIVE_READ',json.dumps(value,sort_keys=True))
        return
    try:
        print('OFFICIAL_MACRO_ARCHIVE_READ',json.dumps(read_archive(client,args.output),sort_keys=True))
    finally:
        client.close()


if __name__=='__main__':main()
