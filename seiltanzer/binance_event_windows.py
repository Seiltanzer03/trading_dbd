"""Exact older target bars selected by official event times, never outcomes."""
from __future__ import annotations

from datetime import datetime,timedelta,timezone
import hashlib
import re
import time

import httpx

from .binance_public_archive import BASE,MAX_ZIP_BYTES,_download,_period,parse_archive
from .config import ALL_INSTRUMENTS
from .mathematical_edge import fingerprint,number
from .real_input_integrity import simulated_evidence

CONTRACT='binance-official-event-target-windows-v1'
POLICY='ALL_VERIFIED_OFFICIAL_RELEASE_WINDOWS_NOT_PRICE_OUTCOMES'
MAX_FILES=32
MAX_RELEASES=128


def _release_times(values,captured):
    if (not isinstance(values,(tuple,list)) or not 1<=len(values)<=MAX_RELEASES
            or any(number(value) is None or not captured-365*86400<=value<=captured for value in values)):
        raise ValueError('EVENT_ARCHIVE_RELEASE_TIMES_INVALID')
    return sorted(set(values))


def in_event_window(stamp,releases):
    # One full5m interval after release; include up to2h future outcome bars.
    return any(at+300<=stamp<=at+6*3600 for at in releases)


def collect_event_windows(code,captured,client,budget,*,release_times,calendar_sha256):
    configured=ALL_INSTRUMENTS.get(code)
    if (configured is None or configured.asset_class!='crypto' or number(captured) is None
            or captured>time.time()+1 or not isinstance(calendar_sha256,str)
            or re.fullmatch('[0-9a-f]{64}',calendar_sha256) is None):
        raise ValueError('EVENT_ARCHIVE_REQUEST_INVALID')
    releases=_release_times(release_times,captured)
    current=datetime.fromtimestamp(captured,timezone.utc).strftime('%Y-%m')
    days=set()
    for at in releases:
        day=datetime.fromtimestamp(at+300,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        last=datetime.fromtimestamp(min(captured,at+6*3600),timezone.utc)
        while day<=last:
            days.add(day.strftime('%Y-%m-%d'));day+=timedelta(days=1)
    monthly={day[:7] for day in days if day[:7]!=current}
    pending=sorted([('monthly',month) for month in monthly]+[('daily',day) for day in days if day[:7]==current],
        key=lambda item:item[1],reverse=True)
    bars={};receipts=[];errors=[];files=0;symbol=configured.binance_symbol
    while pending:
        kind,period=pending.pop(0)
        if files>=MAX_FILES:
            errors.append('EVENT_ARCHIVE_FILE_BOUND_EXCEEDED');break
        files+=1
        filename=f'{symbol}-5m-{period}.zip'
        url=f'{BASE}/{kind}/klines/{symbol}/5m/{filename}'
        try:
            checksum=_download(client,url+'.CHECKSUM',budget,512)
            body=_download(client,url,budget,MAX_ZIP_BYTES)
            parsed,_=parse_archive(body,checksum,filename=filename,symbol=symbol,cutoff=captured)
        except (RuntimeError,ValueError,OSError,httpx.HTTPError) as exc:
            reason=str(exc)[:160]
            if reason=='BINANCE_ARCHIVE_HTTP_404' and kind=='monthly':
                pending=[('daily',day) for day in sorted(days,reverse=True) if day.startswith(period)]+pending
                continue
            errors.append(reason)
            if any(token in reason for token in ('HTTP_403','HTTP_451','HTTP_429','TIME_BUDGET')):
                if not receipts:raise
                break
            continue
        for row in parsed:
            at=row['bar_end_ts']
            if not in_event_window(at,releases):continue
            if at in bars and bars[at]!=row:
                raise ValueError('EVENT_ARCHIVE_CONFLICTING_TARGET_BAR')
            bars[at]=row
        receipts.append(dict(url=url,filename=filename,sha256=hashlib.sha256(body).hexdigest(),
            checksum_sha256=hashlib.sha256(checksum).hexdigest(),received_ts=time.time(),rows=len(parsed)))
    ordered=[bars[at] for at in sorted(bars)]
    if not ordered or len(ordered)>20000:
        raise ValueError('EVENT_ARCHIVE_TARGET_EMPTY_OR_OVER_BOUND')
    return dict(event_window_bars=ordered,event_window_archive=dict(contract_version=CONTRACT,
        provider='Binance',symbol=symbol,instrument=code,interval='5m',bars_sha256=fingerprint(ordered),
        calendar_bundle_sha256=calendar_sha256,calendar_cutoff_ts=captured,
        calendar_release_times=releases,selection_policy=POLICY,
        historical_first_receipt_known=False,files=receipts,partial_errors=errors,
        maximum_source_files=MAX_FILES,raw_target_rows=len(ordered)))


def event_archive_valid(proof,code,calendar_sha256,cutoff):
    if (not isinstance(proof,dict) or simulated_evidence(proof) or proof.get('contract_version')!=CONTRACT
            or proof.get('provider')!='Binance' or proof.get('symbol')!=ALL_INSTRUMENTS[code].binance_symbol
            or proof.get('instrument')!=code or proof.get('interval')!='5m'
            or proof.get('calendar_bundle_sha256')!=calendar_sha256 or proof.get('selection_policy')!=POLICY
            or proof.get('historical_first_receipt_known') is not False
            or not isinstance(proof.get('bars_sha256'),str)
            or re.fullmatch('[0-9a-f]{64}',proof['bars_sha256']) is None):
        return False
    try:
        calendar_cutoff=number(proof.get('calendar_cutoff_ts'))
        if calendar_cutoff is None or not 0<calendar_cutoff<=cutoff:return False
        _release_times(proof.get('calendar_release_times'),calendar_cutoff)
        files=proof.get('files')
        if not isinstance(files,list) or not 1<=len(files)<=MAX_FILES:return False
        for row in files:
            filename=row['filename'];_period(filename,proof['symbol'])
            kind='daily' if len(filename.removesuffix('.zip').split('-'))==5 else 'monthly'
            if (row.get('url')!=f'{BASE}/{kind}/klines/{proof["symbol"]}/5m/{filename}'
                    or any(not isinstance(row.get(key),str) or re.fullmatch('[0-9a-f]{64}',row[key]) is None
                        for key in ('sha256','checksum_sha256'))
                    or number(row.get('received_ts')) is None or not 0<row['received_ts']<=cutoff):return False
    except (ValueError,KeyError,TypeError):return False
    return True


def event_target_bars(source,bars,archive,captured):
    """Keep the unconditional source intact and reject overlapping revisions."""
    from .edge_family_market import _bars
    code=source['instrument'];proof=source.get('event_window_archive');raw=source.get('event_window_bars')
    # Actual retrieval can follow the historical label cutoff, never backdate it.
    receipt_cutoff=time.time()
    if (not event_archive_valid(proof,code,archive.bundle_sha256,receipt_cutoff)
            or proof.get('calendar_release_times')!=archive.published_release_times()
            or proof.get('calendar_cutoff_ts')!=archive.captured_ts
            or not isinstance(raw,list) or proof['bars_sha256']!=fingerprint(raw)):
        raise ValueError('EVENT_WINDOW_SOURCE_PROVENANCE_OR_HASH_INVALID')
    extra=_bars(dict(bars=raw,interval='5m',source_sha256=proof['bars_sha256']),captured)
    releases=proof['calendar_release_times']
    if any(not in_event_window(row['bar_end_ts'],releases) for row in extra):
        raise ValueError('EVENT_WINDOW_TARGET_OUTSIDE_CALENDAR')
    merged={row['bar_end_ts']:row for row in bars if in_event_window(row['bar_end_ts'],releases)}
    for row in extra:
        at=row['bar_end_ts']
        if at in merged and merged[at]!=row:raise ValueError('EVENT_WINDOW_TARGET_REVISION_CONFLICT')
        merged[at]=row
    return [merged[at] for at in sorted(merged)]
