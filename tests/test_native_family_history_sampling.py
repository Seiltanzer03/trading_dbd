"""Keep actual sparse historical captures rather than only a dense latest hour."""
import base64
import contextlib
import gzip
import hashlib
import io
import json
import sqlite3
import time


def test_native_export_selects_actual_time_buckets_and_valid_original_receipts(tmp_path):
    from scripts.export_native_family_history import REMOTE_EXPORT
    db=tmp_path/'sample.db';c=sqlite3.connect(db)
    c.execute('CREATE TABLE g1s_observations(source_observation_id TEXT,instrument TEXT,horizon_minutes INT,captured_ts REAL,frozen_features_json TEXT,features_sha256 TEXT,training_eligible INT,option_source TEXT,option_quality REAL)')
    c.execute('CREATE INDEX ix_capture ON g1s_observations(instrument,horizon_minutes,captured_ts DESC)')
    c.execute('CREATE TABLE passive_market_observations(observation_id TEXT PRIMARY KEY,instrument TEXT,captured_ts REAL,created_ts REAL,features_json TEXT,evidence_eligible INT,option_age_sec REAL)')
    now=int(time.time()//3600)*3600
    raw='{}';digest=hashlib.sha256(raw.encode()).hexdigest()
    actual=[]
    for day in range(3):
        for minute in range(30):
            at=now-day*86400-3600+minute*60
            identity=f'{day}:{minute}';actual.append(at)
            c.execute('INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?,?)',
                (identity,'NAS100',15,at,raw,digest,1,'yfinance QQQ options',.5))
            c.execute('INSERT INTO passive_market_observations VALUES(?,?,?,?,?,?,?)',
                (identity,'NAS100',at,at+1,raw,1,10.))
    c.commit();c.close();before=db.read_bytes()
    script=REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db',str(db))
    output=io.StringIO()
    with contextlib.redirect_stdout(output):exec(script,{})
    report=json.loads(gzip.decompress(base64.b64decode(output.getvalue())))
    selected=sorted(row['captured_ts'] for row in report['rows'])
    assert selected==sorted([now-day*86400-3600+minute*60 for day in range(3) for minute in (14,29)]), 'dense newest hour must not crowd out older real history'
    assert set(selected)<=set(actual)
    assert all(row['original_created_ts']==row['captured_ts']+1 for row in report['rows'])
    assert report['sampling_interval_seconds_by_instrument']['NAS100']==900
    assert db.read_bytes()==before
