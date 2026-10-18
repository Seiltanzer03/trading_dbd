#!/usr/bin/env python3
"""Export bounded market bars read-only; fit models only on the runner."""
import argparse
import base64
import gzip
import json
import shlex
from pathlib import Path
from seiltanzer.config import ALL_INSTRUMENTS
from scripts.production_ede_offload import _connect
from scripts.history_embedded_read_guard import guard_program

# Stdlib-only program executes on the existing server, including before deploy.
# Indexed per-instrument ranges, no database backup, no writes, no training.
REMOTE_EXPORT = r'''
import sqlite3,gzip,json,hashlib,time,base64,math,io,zlib
from pathlib import Path
codes=__CONFIGURED_INSTRUMENT_CODES__
crypto_symbols=__CONFIGURED_CRYPTO_SYMBOLS__
c=sqlite3.connect('file:/opt/seiltanzer/data/trades.db?mode=ro',uri=True,timeout=3)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
evidence_flags=('synthetic','demo','is_demo','synthetic_demo','contract_fixture','synthetic_data_used','synthetic_price_history')
def simulated(value):
 pending=[value];remaining=16384
 while pending:
  remaining-=1
  if remaining<0:return True
  item=pending.pop()
  if isinstance(item,dict):
   if any(item.get(flag) for flag in evidence_flags):return True
   pending.extend(item.values())
  elif isinstance(item,(list,tuple)):pending.extend(item)
 return False
started=time.monotonic()
c.set_progress_handler(lambda: int(time.monotonic()-started>25),10000)
sources=[];diagnostic_sources=[];errors={};now=time.time()
def snapshot_bars(row):
 if len(row['bars_gzip'])>8000000:raise ValueError('HISTORICAL_SNAPSHOT_BYTES_OVER_BOUND')
 with gzip.GzipFile(fileobj=io.BytesIO(row['bars_gzip'])) as reader:raw=reader.read(8000001)
 if len(raw)>8000000 or hashlib.sha256(raw).hexdigest()!=row['source_sha256']:raise ValueError('HISTORICAL_SNAPSHOT_HASH_INVALID')
 values=json.loads(raw)
 if not isinstance(values,list) or not 0<len(values)<=20000:raise ValueError('HISTORICAL_SNAPSHOT_ROW_BOUND_INVALID')
 observed=float(row['created_ts']);fetched=float(row['source_fetched_ts'])
 if not all(math.isfinite(t) and 0<t<=now for t in (observed,fetched)):raise ValueError('HISTORICAL_SNAPSHOT_CLOCK_INVALID')
 observed=min(observed,fetched)
 bars={};partial=0
 if simulated(json.loads(row['source_semantics_json'])):raise ValueError('HISTORICAL_SNAPSHOT_SIMULATED_SOURCE')
 for b in values:
  if simulated(b):raise ValueError('HISTORICAL_SNAPSHOT_SIMULATED_ROW')
  end=float(b['bar_end_ts']);prices=[float(b[k]) for k in ('open','high','low','close')]
  if (not all(math.isfinite(v) for v in [end,*prices]) or end<=0 or abs(end%300)>1
      or min(prices)<=0 or prices[2]>min(prices[0],prices[3]) or prices[1]<max(prices[0],prices[3])
      or end in bars or ('bar_start_ts' in b and float(b['bar_start_ts'])!=end-300)):
   raise ValueError('HISTORICAL_SNAPSHOT_BAR_INVALID')
  if end>observed:partial+=1;continue
  bars[end]=dict(bar_end_ts=end,open=prices[0],high=prices[1],low=prices[2],close=prices[3])
 return bars,partial
has_fetched=any(r['name']=='fetched_ts' for r in c.execute('PRAGMA table_info(g1s_historical_sources)'))
snapshot_columns='source_id,source_sha256,bars_gzip,ticker,provider,interval,source_semantics_json,created_ts,'+('coalesce(fetched_ts,created_ts)' if has_fetched else 'created_ts')+' AS source_fetched_ts'
for code in codes:
 cursor=c.execute('SELECT '+snapshot_columns+' FROM g1s_historical_sources WHERE instrument=? AND contract_version=? ORDER BY created_ts DESC,source_id ASC LIMIT 8',(code,'g1s-historical-wf-real-bars-v1'))
 row=cursor.fetchone();bars=[]
 snapshot_history=dict(accepted_sources=[],excluded_sources=[],snapshots_examined=0,
  maximum_snapshots=8,maximum_days=120,maximum_bars=20000,history_complete=True,
  reason=None,gaps_filled=False,partial_rows_excluded=0)
 if row:
  merged,partial=snapshot_bars(row)
  snapshot_history['partial_rows_excluded']+=partial
  snapshot_history['snapshots_examined']=1
  snapshot_history['accepted_sources'].append(dict(source_id=row['source_id'],source_sha256=row['source_sha256'],created_ts=row['created_ts'],fetched_ts=row['source_fetched_ts'] if has_fetched else None,added_bars=len(merged)))
  original_semantics=json.loads(row['source_semantics_json'])
  if code not in crypto_symbols:
   while True:
    if time.monotonic()-started>=8:
     snapshot_history.update(history_complete=False,reason='HISTORICAL_SNAPSHOT_READ_BUDGET');break
    try:older=cursor.fetchone()
    except sqlite3.OperationalError:
     snapshot_history.update(history_complete=False,reason='HISTORICAL_SNAPSHOT_READ_BUDGET');break
    if older is None:break
    snapshot_history['snapshots_examined']+=1
    try:
     if any(older[k]!=row[k] for k in ('ticker','provider','interval')) or json.loads(older['source_semantics_json'])!=original_semantics:
      raise ValueError('HISTORICAL_SNAPSHOT_SERIES_MISMATCH')
     old,partial=snapshot_bars(older)
     if any(end in merged and merged[end]!=b for end,b in old.items()):raise ValueError('HISTORICAL_SNAPSHOT_OVERLAP_REVISION_CONFLICT')
     additions={end:b for end,b in old.items() if now-120*86400<end<=now and end not in merged}
     merged.update(additions);snapshot_history['partial_rows_excluded']+=partial
     snapshot_history['accepted_sources'].append(dict(source_id=older['source_id'],source_sha256=older['source_sha256'],created_ts=older['created_ts'],fetched_ts=older['source_fetched_ts'] if has_fetched else None,added_bars=len(additions)))
    except (ValueError,TypeError,KeyError,OverflowError,EOFError,OSError,zlib.error) as exc:
     snapshot_history['history_complete']=False
     snapshot_history['excluded_sources'].append(dict(source_id=older['source_id'],reason=str(exc) if isinstance(exc,ValueError) else 'HISTORICAL_SNAPSHOT_INVALID'))
   if snapshot_history['snapshots_examined']==8 and snapshot_history['history_complete']:
    snapshot_history.update(history_complete=False,reason='HISTORICAL_SNAPSHOT_READ_BOUND')
  else:
   snapshot_history.update(history_complete=False,reason='CRYPTO_HISTORY_NOT_MERGED')
  ends=sorted(end for end in merged if now-120*86400<end<=now)
  snapshot_history['row_cap_applied']=len(ends)>20000
  bars=[merged[end] for end in ends[-20000:]]

 recent=c.execute('SELECT bar_start_ts,bar_end_ts,open,high,low,close,source,kind,created_ts FROM passive_market_bars WHERE instrument=? AND bar_start_ts>=? ORDER BY bar_start_ts DESC LIMIT 6000',(code,now-14*86400)).fetchall()
 groups={};kinds={};providers={};excluded_derived=0;excluded_partial=0
 for r in recent:
  kinds[r['kind']]=kinds.get(r['kind'],0)+1;providers[r['source'] or 'UNAVAILABLE']=providers.get(r['source'] or 'UNAVAILABLE',0)+1
  if r['kind']!='direct':excluded_derived+=1;continue
  if r['created_ts'] is None or r['created_ts']<r['bar_end_ts']:excluded_partial+=1;continue
  if r['bar_end_ts']>now or abs(r['bar_end_ts']-r['bar_start_ts']-60)>1:continue
  start=math.floor(r['bar_start_ts']/300)*300
  groups.setdefault(r['source'] or 'UNAVAILABLE',{}).setdefault(start,{})[int(r['bar_start_ts'])]=dict(r)
 retained={}
 for provider,provider_groups in groups.items():
  completed=[]
  for start,g in sorted(provider_groups.items()):
   if set(g)!={int(start+k*60) for k in range(5)}:continue
   v=[g[int(start+k*60)] for k in range(5)]
   completed.append(dict(bar_end_ts=start+300,open=v[0]['open'],high=max(x['high'] for x in v),low=min(x['low'] for x in v),close=v[-1]['close']))
  if completed:retained[provider]=completed
 # Historical and retained feeds have different series semantics: never splice them.
 selected_provider=row['provider'] if row else None
 selected_ticker=row['ticker'] if row else code
 selected_kind='SINGLE_HISTORICAL_PROVIDER_COMPLETED_5M'
 semantics=json.loads(row['source_semantics_json']) if row else {}
 exact_label='binance_1m_direct:'+crypto_symbols[code] if code in crypto_symbols else None
 if exact_label and exact_label in retained:
  # Never reinterpret legacy yahoo-labeled rows as Binance, even for crypto.
  if bars:
   original_digest=hashlib.sha256(json.dumps(bars,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
   diagnostic_sources.append(dict(instrument=code,bars=bars,source_id=row['source_id'],source_sha256=original_digest,provider=row['provider'],ticker=row['ticker'],interval=row['interval'],source_kind='SINGLE_HISTORICAL_PROVIDER_COMPLETED_5M',source_semantics=semantics,diagnostic_only=True))
  selected_provider='Binance';selected_ticker=crypto_symbols[code]
  bars=retained[exact_label];selected_kind='SINGLE_RETAINED_PROVIDER_COMPLETED_5M'
  semantics=dict(provider='Binance',ticker=selected_ticker,provider_quote_currency='USDT',configured_quote_currency='USDT',currency_basis_mismatch=False,proxy_for_configured_series=False,bar_timestamp_semantics='five consecutive completed direct Binance one-minute bars',gaps_filled=False,synthetic_price_history=False,exact_live_broker_series=False)
 if not bars and retained:
  selected_provider=max(retained,key=lambda p:(len(retained[p]),retained[p][-1]['bar_end_ts']))
  bars=retained[selected_provider];selected_kind='SINGLE_RETAINED_PROVIDER_COMPLETED_5M'
  semantics=dict(provider=selected_provider,bar_timestamp_semantics='five consecutive completed direct one-minute bars',gaps_filled=False,synthetic_price_history=False,exact_live_broker_series=False)
 if not bars:errors[code]='SOURCE_BARS_UNAVAILABLE';continue
 bars=sorted(bars,key=lambda x:x['bar_end_ts'])
 digest=hashlib.sha256(json.dumps(bars,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 sources.append(dict(instrument=code,bars=bars,source_id='math-export-'+code+'-'+digest[:24],source_sha256=digest,cached_source_id=row['source_id'] if row else None,cached_sha256=row['source_sha256'] if row else None,recent_completed_5m_n=sum(len(v) for v in retained.values()),retained_provider_completed_counts={p:len(v) for p,v in retained.items()},source_kind=selected_kind,provider=selected_provider,ticker=selected_ticker,interval='5m',source_semantics=semantics,mixed_provider_splicing=False,historical_snapshot_history=snapshot_history,recent_kind_counts=kinds,recent_provider_counts=providers,excluded_derived_minute_n=excluded_derived,excluded_partial_minute_n=excluded_partial,cached_ticker=row['ticker'] if row else None,cached_provider=row['provider'] if row else None,cached_interval=row['interval'] if row else None,cached_semantics=json.loads(row['source_semantics_json']) if row else None,not_broker_execution_bars=True,receipt_observed_ts=now))
c.close()
previous_model=None;previous_model_reason=None
try:
 path=Path('/opt/seiltanzer/data/research/mathematical_edge_latest.json')
 if path.stat().st_size>8000000:raise ValueError('PREVIOUS_MODEL_PUBLICATION_OVER_BOUND')
 with path.open('rb') as reader:previous_raw=reader.read(8000001)
 if len(previous_raw)>8000000:raise ValueError('PREVIOUS_MODEL_PUBLICATION_OVER_BOUND')
 def invalid_constant(value):raise ValueError('PREVIOUS_MODEL_PUBLICATION_INVALID')
 previous=json.loads(previous_raw,parse_constant=invalid_constant)
 if not isinstance(previous,dict) or any(previous.get(flag) for flag in evidence_flags):raise ValueError('PREVIOUS_MODEL_PUBLICATION_INVALID')
 keys=('contract_version','created_ts','production_authority','automatic_execution','family_market_models','published_for_sha','publication_contract_version','publication_run_id')
 previous_model={k:previous[k] for k in keys if k in previous}
except FileNotFoundError:previous_model_reason='PREVIOUS_MODEL_PUBLICATION_NOT_FOUND'
except (ValueError,TypeError,OSError,RecursionError):previous_model_reason='PREVIOUS_MODEL_PUBLICATION_INVALID'
raw=json.dumps(dict(sources=sources,diagnostic_sources=diagnostic_sources,errors=errors,exported_ts=now,read_only=True,
 previous_family_model_report=previous_model,previous_family_model_reason=previous_model_reason),allow_nan=False).encode()
if len(raw)>64000000:raise ValueError('export exceeds bound')
print(base64.b64encode(gzip.compress(raw)).decode())
'''.replace('__CONFIGURED_INSTRUMENT_CODES__', repr(tuple(ALL_INSTRUMENTS))).replace(
    '__CONFIGURED_CRYPTO_SYMBOLS__', repr({code: item.binance_symbol for code, item in ALL_INSTRUMENTS.items() if item.asset_class == 'crypto'}))

REMOTE_EXPORT = guard_program(REMOTE_EXPORT)


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
