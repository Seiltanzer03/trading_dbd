#!/usr/bin/env python3
"""Export bounded market bars read-only; fit models only on the runner."""
import argparse
import base64
import gzip
import json
import shlex
from pathlib import Path
from scripts.production_ede_offload import _connect

# Stdlib-only program executes on the existing server, including before deploy.
# Indexed per-instrument ranges, no database backup, no writes, no training.
REMOTE_EXPORT = r'''
import sqlite3,gzip,json,hashlib,time,base64,math
codes=('NAS100','SP500','US30','GER40','UK100','JPY100','EURUSD','USDCAD','XAU','XAG')
c=sqlite3.connect('file:/opt/seiltanzer/data/trades.db?mode=ro',uri=True,timeout=3)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
started=time.monotonic()
c.set_progress_handler(lambda: int(time.monotonic()-started>25),10000)
sources=[];errors={};now=time.time()
for code in codes:
 row=c.execute('SELECT source_id,source_sha256,bars_gzip,ticker,provider,interval,source_semantics_json FROM g1s_historical_sources WHERE instrument=? AND contract_version=? ORDER BY created_ts DESC LIMIT 1',(code,'g1s-historical-wf-real-bars-v1')).fetchone()
 bars=[]
 if row:
  raw=gzip.decompress(row['bars_gzip'])
  if len(raw)>8000000 or hashlib.sha256(raw).hexdigest()!=row['source_sha256']:raise ValueError('invalid historical source')
  bars=json.loads(raw)
  if len(bars)>20000:raise ValueError('source exceeds bound')
 recent=c.execute('SELECT bar_start_ts,bar_end_ts,open,high,low,close,source,kind,created_ts FROM passive_market_bars WHERE instrument=? AND bar_start_ts>=? ORDER BY bar_start_ts DESC LIMIT 6000',(code,now-14*86400)).fetchall()
 groups={};kinds={};providers={};excluded_derived=0;excluded_partial=0
 for r in recent:
  kinds[r['kind']]=kinds.get(r['kind'],0)+1;providers[r['source'] or 'UNAVAILABLE']=providers.get(r['source'] or 'UNAVAILABLE',0)+1
  if r['kind']!='direct':excluded_derived+=1;continue
  if r['created_ts'] is None or r['created_ts']<r['bar_end_ts']:excluded_partial+=1;continue
  if r['bar_end_ts']>now or abs(r['bar_end_ts']-r['bar_start_ts']-60)>1:continue
  start=math.floor(r['bar_start_ts']/300)*300
  groups.setdefault(start,{})[int(r['bar_start_ts'])]=dict(r)
 appended=[]
 for start,g in sorted(groups.items()):
  if set(g)!={int(start+k*60) for k in range(5)}:continue
  v=[g[int(start+k*60)] for k in range(5)]
  appended.append(dict(bar_end_ts=start+300,open=v[0]['open'],high=max(x['high'] for x in v),low=min(x['low'] for x in v),close=v[-1]['close']))
 merged={b['bar_end_ts']:b for b in bars}
 merged.update({b['bar_end_ts']:b for b in appended})
 if not merged:errors[code]='SOURCE_BARS_UNAVAILABLE';continue
 sources.append(dict(instrument=code,bars=list(sorted(merged.values(),key=lambda x:x['bar_end_ts'])),source_id=row['source_id'] if row else None,cached_sha256=row['source_sha256'] if row else None,recent_completed_5m_n=len(appended),source_kind='historical_provider_bars_plus_direct_retained_only',recent_kind_counts=kinds,recent_provider_counts=providers,excluded_derived_minute_n=excluded_derived,excluded_partial_minute_n=excluded_partial,cached_ticker=row['ticker'] if row else None,cached_provider=row['provider'] if row else None,cached_interval=row['interval'] if row else None,cached_semantics=json.loads(row['source_semantics_json']) if row else None,not_broker_execution_bars=True))
c.close()
raw=json.dumps(dict(sources=sources,errors=errors,exported_ts=now,read_only=True),allow_nan=False).encode()
if len(raw)>64000000:raise ValueError('export exceeds bound')
print(base64.b64encode(gzip.compress(raw)).decode())
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--password', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    client = _connect(args.password)
    try:
        _, stdout, stderr = client.exec_command('python3 -c ' + shlex.quote(REMOTE_EXPORT), timeout=40)
        payload = stdout.read(12_000_001)
        error = stderr.read().decode('utf8', 'replace')
        if stdout.channel.recv_exit_status() or len(payload) > 12_000_000:
            raise RuntimeError('bounded read-only export failed: ' + error[:500])
        compressed = base64.b64decode(payload, validate=True) if not payload.endswith(b'\n') else base64.b64decode(payload.strip(), validate=True)
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
            raw = reader.read(64_000_001)
        if len(raw) > 64_000_000:
            raise ValueError('source export exceeds bound')
        report = json.loads(raw)
        if report.get('read_only') is not True:
            raise ValueError('invalid source export')
        Path(args.output).write_bytes(raw)
        print('Exported actual bars:', {s['instrument']: (len(s['bars']), s['recent_completed_5m_n']) for s in report['sources']})
    finally:
        client.close()


if __name__ == '__main__':
    main()
