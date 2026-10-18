"""Bounded indexed read of immutable native captures; private runner input only."""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import inspect
import json
import shlex
import textwrap
from pathlib import Path

from scripts.production_ede_offload import _connect
from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.edge_family_native_history import native_history
from seiltanzer import native_family_capture_projection, real_input_integrity
from seiltanzer import history_live_read_guard

# Embed the exact shared stdlib implementation: no installed server package,
# model dependencies or duplicate projection/guard rules are required.
_SUPPORT = inspect.getsource(real_input_integrity) + '\n' + inspect.getsource(
    native_family_capture_projection).replace('from __future__ import annotations\n','').replace(
        'from .real_input_integrity import FLAGS, simulated_evidence\n','')
REMOTE_EXPORT = _SUPPORT + '\n' + r"""
import sqlite3, time, json, hashlib, gzip, base64
now=time.time()
started=time.monotonic()
c=sqlite3.connect('file:/opt/seiltanzer/data/trades.db?mode=ro',uri=True,timeout=3)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
c.set_progress_handler(lambda: int(time.monotonic()-started>25),10000)
rows=[]
tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('g1s_observations','passive_market_observations')")}
reason='NATIVE_TABLES_UNAVAILABLE'
limited=[]
cursors={}
incomplete=[]
seen={}
sizes={}
if len(tables)==2:
 try:
  for code in __CODES__:
   if time.monotonic()-started>=24:
    incomplete=[name for name in __CODES__ if name not in seen]
    reason='NATIVE_READ_BUDGET_EXHAUSTED';break
   cursors[code]=c.execute('''SELECT g.instrument,g.captured_ts,g.frozen_features_json,g.features_sha256,
        g.option_source,g.option_quality,p.created_ts AS original_created_ts,p.features_json AS original_json
        FROM g1s_observations g JOIN passive_market_observations p ON p.observation_id=g.source_observation_id
        WHERE g.instrument=? AND g.horizon_minutes=15 AND g.captured_ts>=? AND g.captured_ts<=?
        AND g.training_eligible=1 AND p.evidence_eligible=1 AND p.instrument=g.instrument AND p.captured_ts=g.captured_ts
        ORDER BY g.captured_ts DESC LIMIT 1500''',(code,now-90*86400,now))
   seen[code]=0;sizes[code]=0
  # Fair bounded passes prevent one large instrument consuming the full read.
  while cursors and reason!='NATIVE_READ_BUDGET_EXHAUSTED':
   for code,cursor in list(cursors.items()):
    for _ in range(32):
     if time.monotonic()-started>=24:
      reason='NATIVE_READ_BUDGET_EXHAUSTED';break
     value=cursor.fetchone()
     if value is None:
      if seen[code]>=1500:limited.append(code)
      cursor.close();del cursors[code];break
     seen[code]+=1
     r=dict(value)
     raw=r['frozen_features_json']
     original=r.pop('original_json')
     if (not isinstance(raw,str) or not isinstance(original,str) or len(raw.encode())>256000
         or hashlib.sha256(raw.encode()).hexdigest()!=r['features_sha256']
         or hashlib.sha256(original.encode()).hexdigest()!=r['features_sha256']):continue
     projected=project_capture(raw,r['features_sha256'])
     if projected is None:continue
     sizes[code]+=len(projected['frozen_features_json'].encode())
     if sizes[code]>6000000:
      limited.append(code);cursor.close();del cursors[code];break
     r['source_verified']=True
     r.update(projected)
     rows.append(r)
    if reason=='NATIVE_READ_BUDGET_EXHAUSTED':break
  if reason!='NATIVE_READ_BUDGET_EXHAUSTED':
   reason='NATIVE_OBSERVATIONS_EXPORTED' if rows else 'NATIVE_ELIGIBLE_HISTORY_EMPTY'
 except sqlite3.Error as exc:
  if getattr(exc,'sqlite_errorcode',None)==sqlite3.SQLITE_INTERRUPT:
   reason='NATIVE_READ_BUDGET_EXHAUSTED'
  else:
   rows=[];reason='NATIVE_READ_FAILED'
 except (ValueError,TypeError):
  rows=[];reason='NATIVE_READ_FAILED'
 if reason=='NATIVE_READ_BUDGET_EXHAUSTED':
  incomplete=list(set(incomplete)|(set(__CODES__)-set(seen)))
 incomplete=sorted(set(incomplete)|set(cursors))
 for cursor in cursors.values():cursor.close()
c.close()
report=dict(rows=rows,exported_ts=now,read_only=True,reason=reason,
            contract_version='native-family-capture-export-v1',bounded_history=True,limited_instruments=limited,
            history_complete=reason in ('NATIVE_OBSERVATIONS_EXPORTED','NATIVE_ELIGIBLE_HISTORY_EMPTY') and not incomplete and not limited,
            incomplete_instruments=incomplete,source_rows_examined=seen)
raw=json.dumps(report,allow_nan=False,separators=(',',':')).encode()
if len(raw)>96000000:raise ValueError('private native export exceeds bound')
print(base64.b64encode(gzip.compress(raw)).decode())
""".replace('__CODES__',repr(tuple(ALL_INSTRUMENTS)))

# Guard only the SQLite lifetime, releasing before report compression/SSH I/O.
_read_start = REMOTE_EXPORT.index("c=sqlite3.connect(")
_read_end = REMOTE_EXPORT.index('c.close()\n',_read_start)+len('c.close()\n')
_connect, _read_body = REMOTE_EXPORT[_read_start:_read_end].split('\n',1)
_read_body = _read_body.removesuffix('c.close()\n')
REMOTE_EXPORT = (REMOTE_EXPORT[:_read_start]+'\n'+inspect.getsource(history_live_read_guard)+
    "\nwith guarded_live_read(Path('/opt/seiltanzer/data/trades.db')):\n"+
    textwrap.indent(_connect+'\ntry:\n'+textwrap.indent(_read_body,'    ')+
                    'finally:\n    c.close()\n','    ')+REMOTE_EXPORT[_read_end:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--password',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    client=_connect(args.password)
    try:
        _,stdout,stderr=client.exec_command('python3 -c '+shlex.quote(REMOTE_EXPORT),timeout=45)
        payload=stdout.read(40_000_001)
        if stdout.channel.recv_exit_status() or len(payload)>40_000_000:
            raise RuntimeError('bounded private native history export failed')
        compressed=base64.b64decode(payload.strip(),validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
            raw=reader.read(96_000_001)
        if len(raw)>96_000_000:raise ValueError('private native history exceeds bound')
        report=json.loads(raw)
        if report.get('contract_version')!='native-family-capture-export-v1' or report.get('read_only') is not True:
            raise ValueError('invalid native export contract')
        history=native_history(report['rows'],captured_ts=report['exported_ts'])
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(history,allow_nan=False,separators=(',',':')))
        args.output.chmod(0o600)
        counts={family:sum(len(fs.get(family,[])) for fs in history.values()) for family in
                ('macro','event','order_flow','intermarket','positioning','value_carry','option','session')}
        print('NATIVE_FAMILY_HISTORY_READ',json.dumps({'read_only':True,'raw_publication':False,
            'reason':report['reason'],'source_rows':len(report['rows']),'family_counts':counts,
            'limited_instruments':report['limited_instruments'],
            'history_complete':report['history_complete'],
            'incomplete_instruments':report['incomplete_instruments'],
            'source_rows_examined':report['source_rows_examined']},sort_keys=True))
    finally:
        client.close()


if __name__=='__main__':main()
