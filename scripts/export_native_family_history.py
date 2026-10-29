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
from seiltanzer import sqlite_json_payloads
from seiltanzer import history_live_read_guard

# Embed the exact shared stdlib implementation: no installed server package,
# model dependencies or duplicate projection/guard rules are required.
_SUPPORT = inspect.getsource(real_input_integrity) + '\n' + inspect.getsource(
    native_family_capture_projection).replace('from __future__ import annotations\n','').replace(
        'from .real_input_integrity import FLAGS, simulated_evidence\n','')
_SUPPORT += '\n_payload_storage_ns={"__name__":"_native_export_payloads"}\nexec(' + repr(
    inspect.getsource(sqlite_json_payloads)) + ',_payload_storage_ns)\n'
REMOTE_EXPORT = _SUPPORT + '\n' + r"""
import sqlite3, time, json, hashlib, gzip, base64
now=time.time()
started=time.monotonic()
c=sqlite3.connect('file:/opt/seiltanzer/data/trades.db?mode=ro',uri=True,timeout=3)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
c.set_progress_handler(lambda: int(time.monotonic()-started>25),10000)
_payload_storage_ns['install_reader'](c)
rows=[]
tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('g1s_observations','passive_market_observations')")}
reason='NATIVE_TABLES_UNAVAILABLE'
limited=[]
cursors={}
incomplete=[]
seen={}
sizes={}
sampling={}
if len(tables)==2:
 try:
  passive_columns={r[1] for r in c.execute('PRAGMA table_info(passive_market_observations)')}
  age_column='p.option_age_sec' if 'option_age_sec' in passive_columns else 'NULL'
  query='''SELECT g.instrument,g.captured_ts,g.frozen_features_json,g.features_sha256,
        g.option_source,g.option_quality,p.created_ts AS original_created_ts,storage_json_text(p.features_json) AS original_json
        ,'''+age_column+''' AS option_age_sec
        FROM g1s_observations g JOIN passive_market_observations p ON p.observation_id=g.source_observation_id
        WHERE g.instrument=? AND g.horizon_minutes=15 AND g.captured_ts>=? AND g.captured_ts<=?
        AND g.training_eligible=1 AND p.evidence_eligible=1 AND p.instrument=g.instrument AND p.captured_ts=g.captured_ts
        ORDER BY g.captured_ts DESC LIMIT 1'''
  class SampledCaptures:
   def __init__(self,code):
    self.code=code;self.before=now;self.count=0
    # Two indexed boundary seeks measure the actual available span, not the
    # age of an inactive feed. Sparse15m histories retain every observation.
    bounds='SELECT g.captured_ts FROM '+query.split('FROM ',1)[1]
    first=c.execute(bounds.replace('DESC LIMIT 1','ASC LIMIT 1'),(code,now-90*86400,now)).fetchone()
    last=c.execute(bounds,(code,now-90*86400,now)).fetchone()
    span=float(last[0])-float(first[0]) if first is not None and last is not None else 0.
    self.step=max(900,int((span+1500*900)//(1500*900))*900)
    sampling[code]=self.step
   def fetchone(self):
    if self.count>=1500 or self.before<now-90*86400:return None
    cursor=c.execute(query,(self.code,now-90*86400,self.before))
    try:value=cursor.fetchone()
    finally:cursor.close()
    if value is not None:
     self.count+=1
     self.before=int(float(value['captured_ts'])//self.step)*self.step-0.000001
    return value
   def close(self):pass
  for code in __CODES__:
   if time.monotonic()-started>=24:
    incomplete=[name for name in __CODES__ if name not in seen]
    reason='NATIVE_READ_BUDGET_EXHAUSTED';break
   cursors[code]=SampledCaptures(code)
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
            sampling_interval_seconds_by_instrument=sampling,history_scope='ADAPTIVE_UTC_BUCKETS_AT_LEAST_15_MINUTES',
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
_read_connect, _read_body = REMOTE_EXPORT[_read_start:_read_end].split('\n',1)
_read_body = _read_body.removesuffix('c.close()\n')
REMOTE_EXPORT = (REMOTE_EXPORT[:_read_start]+'\n'+inspect.getsource(history_live_read_guard)+
    "\nwith guarded_live_read(Path('/opt/seiltanzer/data/trades.db')):\n"+
    textwrap.indent(_read_connect+'\ntry:\n'+textwrap.indent(_read_body,'    ')+
                    'finally:\n    c.close()\n','    ')+REMOTE_EXPORT[_read_end:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--password',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    client=_connect(args.password)
    try:
        # Each indexed instrument gets the SAME 25s/1500-row guarded budget.
        # This fixes starvation without extending any live read or copying DBs.
        batches=[];total_bytes=0
        for code in ALL_INSTRUMENTS:
            program=REMOTE_EXPORT.replace(repr(tuple(ALL_INSTRUMENTS)),repr((code,)))
            _,stdout,stderr=client.exec_command('python3 -c '+shlex.quote(program),timeout=45)
            payload=stdout.read(40_000_001)
            if stdout.channel.recv_exit_status() or len(payload)>40_000_000:
                raise RuntimeError('bounded private native history export failed')
            compressed=base64.b64decode(payload.strip(),validate=True)
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
                raw=reader.read(8_000_001)
            total_bytes+=len(raw)
            if len(raw)>8_000_000 or total_bytes>96_000_000:
                raise ValueError('private native export exceeds bound')
            batch=json.loads(raw)
            if batch.get('contract_version')!='native-family-capture-export-v1' or batch.get('read_only') is not True:
                raise ValueError('invalid native export contract')
            if any(row.get('instrument')!=code for row in batch['rows']):
                raise ValueError('native batch instrument mismatch')
            batches.append(batch)
            print('NATIVE_FAMILY_BATCH',json.dumps(dict(instrument=code,reason=batch['reason'],
                source_rows=len(batch['rows']),history_complete=batch['history_complete'])),flush=True)
        report=dict(rows=[row for batch in batches for row in batch['rows']],
            exported_ts=max(batch['exported_ts'] for batch in batches),
            reason='NATIVE_OBSERVATIONS_EXPORTED' if all(batch['history_complete'] for batch in batches) else 'NATIVE_BOUNDED_PARTIAL_HISTORY',
            limited_instruments=sorted({code for batch in batches for code in batch['limited_instruments']}),
            incomplete_instruments=sorted({code for batch in batches for code in batch['incomplete_instruments']}),
            history_complete=all(batch['history_complete'] for batch in batches),
            source_rows_examined={code:count for batch in batches for code,count in batch['source_rows_examined'].items()})
        report['sampling_interval_seconds_by_instrument']={code:step for batch in batches
            for code,step in batch.get('sampling_interval_seconds_by_instrument',{}).items()}
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
            'sampling_interval_seconds_by_instrument':report['sampling_interval_seconds_by_instrument'],
            'history_scope':'ADAPTIVE_UTC_BUCKETS_AT_LEAST_15_MINUTES',
            'source_rows_examined':report['source_rows_examined']},sort_keys=True))
    finally:
        client.close()


if __name__=='__main__':main()
