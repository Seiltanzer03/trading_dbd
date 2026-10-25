"""Exact configured spot history from Binance's documented public data product.

This is a historical download, not a live quote or a recorded first receipt.
No Binance API retries, alternate hosts, inferred bars or gap filling.
https://github.com/binance/binance-public-data
"""
from __future__ import annotations

import calendar
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import re
import time
import zipfile

from .config import ALL_INSTRUMENTS
from .mathematical_edge import fingerprint, number

BASE = 'https://data.binance.vision/data/spot'
MAX_ZIP_BYTES = 4_000_000
MAX_CSV_BYTES = 12_000_000
MAX_FILES = 64
MAX_ROWS = 31*288


def _period(filename, symbol):
    match = re.fullmatch(re.escape(symbol)+r'-5m-(\d{4})-(\d{2})(?:-(\d{2}))?\.zip', filename)
    if match is None:
        raise ValueError('BINANCE_ARCHIVE_FILENAME_INVALID')
    year, month = int(match[1]), int(match[2])
    start = datetime(year, month, int(match[3] or 1), tzinfo=timezone.utc)
    end = start+timedelta(days=1 if match[3] else calendar.monthrange(year, month)[1])
    return start, end


def parse_archive(body, checksum, *, filename, symbol, cutoff):
    """Validate every row, including excluded future rows, before returning any."""
    start, end = _period(filename, symbol)
    if len(body) > MAX_ZIP_BYTES or len(checksum) > 512:
        raise ValueError('BINANCE_ARCHIVE_SIZE_EXCEEDS_BOUND')
    expected = hashlib.sha256(body).hexdigest()
    try:
        parts = checksum.decode('ascii').strip().split()
    except UnicodeError as exc:
        raise ValueError('BINANCE_ARCHIVE_CHECKSUM_INVALID') from exc
    if parts != [expected, filename]:
        raise ValueError('BINANCE_ARCHIVE_CHECKSUM_MISMATCH')
    bars, flow, seen = [], [], set()
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            if (len(members) != 1 or members[0].filename != filename[:-4]+'.csv'
                    or members[0].is_dir() or members[0].flag_bits & 1):
                raise ValueError('BINANCE_ARCHIVE_MEMBER_INVALID')
            if members[0].file_size > MAX_CSV_BYTES:
                raise ValueError('BINANCE_ARCHIVE_SIZE_EXCEEDS_BOUND')
            with archive.open(members[0]) as stream:
                raw = stream.read(MAX_CSV_BYTES+1)
            if len(raw) > MAX_CSV_BYTES:
                raise ValueError('BINANCE_ARCHIVE_SIZE_EXCEEDS_BOUND')
            reader = csv.reader(io.StringIO(raw.decode('ascii')))
            # Spot archives changed from milliseconds to microseconds in 2025.
            unit = 1_000_000 if start.year >= 2025 else 1000
            for index, row in enumerate(reader):
                if index >= MAX_ROWS or len(row) != 12:
                    raise ValueError('BINANCE_ARCHIVE_ROW_SCHEMA_INVALID')
                if not row[0].isdigit() or not row[6].isdigit():
                    raise ValueError('BINANCE_ARCHIVE_TIMESTAMP_INVALID')
                opened, closed = int(row[0]), int(row[6])
                at = opened/unit
                if opened % (300*unit) or closed != opened+300*unit-1:
                    raise ValueError('BINANCE_ARCHIVE_TIMESTAMP_INVALID')
                if not start.timestamp() <= at < end.timestamp():
                    raise ValueError('BINANCE_ARCHIVE_PERIOD_MISMATCH')
                if opened in seen:
                    raise ValueError('BINANCE_ARCHIVE_DUPLICATE_ROW')
                seen.add(opened)
                values = [number(value) for value in row[1:6]+row[7:11]]
                if any(value is None for value in values):
                    raise ValueError('BINANCE_ARCHIVE_NUMERIC_VALUE_INVALID')
                o, h, l, c, volume, quote, trades, buy, buy_quote = values
                if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
                    raise ValueError('BINANCE_ARCHIVE_OHLC_INVALID')
                if (min(volume, quote, trades, buy, buy_quote) < 0 or buy > volume or buy_quote > quote
                        or trades != int(trades) or (volume == 0 and (quote != 0 or trades != 0))):
                    raise ValueError('BINANCE_ARCHIVE_VOLUME_INVALID')
                if at+300 <= cutoff:
                    bars.append(dict(bar_end_ts=at+300, open=o, high=h, low=l, close=c))
                    flow.append(dict(bar_end_ts=at+300, volume=volume, quote_asset_volume=quote,
                        trades=int(trades), taker_buy_base_volume=buy, taker_buy_quote_volume=buy_quote))
    except (zipfile.BadZipFile, UnicodeError, csv.Error, RuntimeError) as exc:
        raise ValueError('BINANCE_ARCHIVE_ZIP_OR_CSV_INVALID') from exc
    if not seen:
        raise ValueError('BINANCE_ARCHIVE_EMPTY')
    return bars, flow


def _download(client, url, budget, limit):
    budget.acquire()
    # httpx streaming enforces the bound during transfer. Simple protocol
    # fixtures may expose get only; production always uses the streaming path.
    if hasattr(client, 'stream'):
        with client.stream('GET', url, timeout=min(12., budget.remaining())) as response:
            if response.status_code != 200:
                raise RuntimeError(f'BINANCE_ARCHIVE_HTTP_{response.status_code}')
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError('BINANCE_ARCHIVE_SIZE_EXCEEDS_BOUND')
            return bytes(body)
    response = client.get(url, timeout=min(12., budget.remaining()))
    if response.status_code != 200:
        raise RuntimeError(f'BINANCE_ARCHIVE_HTTP_{response.status_code}')
    if len(response.content) > limit:
        raise ValueError('BINANCE_ARCHIVE_SIZE_EXCEEDS_BOUND')
    return response.content


def fetch_public_archive(code, captured, client, budget, *, days=60):
    """At most 64 official files and 128 requests under the shared deadline."""
    from .mathematical_edge_sources import _make_source
    configured = ALL_INSTRUMENTS.get(code)
    if (configured is None or configured.asset_class != 'crypto' or not configured.binance_symbol
            or number(captured) is None or not 1 <= days <= 60 or captured > time.time()+1):
        raise ValueError('BINANCE_ARCHIVE_REQUEST_INVALID')
    symbol = configured.binance_symbol
    stop = datetime.fromtimestamp(captured, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    lower = captured-days*86400
    day = datetime.fromtimestamp(lower, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    bars, flow, receipts, missing = {}, {}, [], []
    files = 0
    unavailable_months = set()
    while day < stop:
        if files >= MAX_FILES:
            raise ValueError('BINANCE_ARCHIVE_FILE_BOUND_EXCEEDED')
        # Only past complete months; the current partial month uses daily files.
        month_end = day.replace(day=1)+timedelta(days=calendar.monthrange(day.year, day.month)[1])
        monthly = day.day == 1 and month_end <= stop and day.strftime('%Y-%m') not in unavailable_months
        period = day.strftime('%Y-%m' if monthly else '%Y-%m-%d')
        filename = f'{symbol}-5m-{period}.zip'
        url = f'{BASE}/{"monthly" if monthly else "daily"}/klines/{symbol}/5m/{filename}'
        files += 1
        try:
            checksum = _download(client, url+'.CHECKSUM', budget, 512)
            body = _download(client, url, budget, MAX_ZIP_BYTES)
        except RuntimeError as exc:
            if str(exc) != 'BINANCE_ARCHIVE_HTTP_404':
                raise
            # Not-yet-published monthly archives are replaced by their distinct
            # documented daily files, never another host or a denied endpoint.
            if monthly:
                unavailable_months.add(day.strftime('%Y-%m'))
                continue
            missing.append(filename)
            day = month_end if monthly else day+timedelta(days=1)
            continue
        parsed, trades = parse_archive(body, checksum, filename=filename, symbol=symbol, cutoff=captured)
        received = time.time()
        for bar, trade in zip(parsed, trades):
            at = bar['bar_end_ts']
            if lower < at <= captured:
                if at in bars:
                    raise ValueError('BINANCE_ARCHIVE_OVERLAPPING_PERIODS')
                bars[at], flow[at] = bar, trade
        receipts.append(dict(url=url, filename=filename, sha256=hashlib.sha256(body).hexdigest(),
            checksum_sha256=hashlib.sha256(checksum).hexdigest(), received_ts=received, rows=len(parsed)))
        day = month_end if monthly else day+timedelta(days=1)
    ordered = sorted(bars)[-20000:]
    output = _make_source(code, 'Binance', symbol, [bars[at] for at in ordered], captured,
        dict(contract_version='binance-public-archive-v1', requested_days=days, files=receipts,
            missing_files=missing, maximum_files=MAX_FILES, historical_first_receipt_known=False,
            historical_availability='COMPLETED_EXCHANGE_BAR_NOT_RECORDED_FIRST_RECEIPT',
            public_archive_documentation='https://github.com/binance/binance-public-data',
            history_request_completed=not missing, gaps_filled=False))
    output['trade_flow_history'] = [flow[at] for at in ordered]
    output['trade_flow_sha256'] = fingerprint(output['trade_flow_history'])
    return output
