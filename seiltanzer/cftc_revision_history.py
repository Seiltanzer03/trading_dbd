"""Exact public CFTC row versions; revision clocks are not release receipts.

This packet preserves existing historical inputs for subsequent availability
verification. It does not authorize a historical forecast or a CFD mapping.
"""
from datetime import datetime
import hashlib
import json
import math
import re

from .edge_family_archive import _loads

CONTRACT = 'cftc-source-row-versions-v1'
DATASET = '6dca-aqww'
MAX_BODY_BYTES = 64_000
MAX_PACKET_BYTES = 128_000
MAX_ROWS = 128
MARKETS = frozenset(('088691', '084691', '099741'))


def _clock(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('CFTC_REVISION_RECEIPT_INVALID')
    try:
        valid = math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('CFTC_REVISION_RECEIPT_INVALID')
    return float(value)


def _timestamp(value, *, report=False):
    if not isinstance(value, str):
        raise ValueError('CFTC_REVISION_TIMESTAMP_INVALID')
    if report:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00(?:\.000)?', value):
            raise ValueError('CFTC_REPORT_DATE_INVALID')
        value += 'Z'
    elif not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('CFTC_REVISION_TIMESTAMP_NOT_FIXED')
    try:
        return _clock(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
    except (ValueError, OverflowError, OSError):
        raise ValueError('CFTC_REVISION_TIMESTAMP_INVALID') from None


def _count(value):
    if isinstance(value, bool):
        raise ValueError('CFTC_REVISION_COUNT_INVALID')
    try:
        count = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('CFTC_REVISION_COUNT_INVALID') from None
    if not math.isfinite(count) or not 0 <= count <= 2**53-1 or not count.is_integer():
        raise ValueError('CFTC_REVISION_COUNT_INVALID')
    return int(count)


def capture_versions(body, *, contract, received_ts, source_id):
    received = _clock(received_ts)
    if not isinstance(contract, str) or contract not in MARKETS or not isinstance(source_id, str) or not 0 < len(source_id) <= 256:
        raise ValueError('CFTC_REVISION_SOURCE_IDENTITY_INVALID')
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY_BYTES:
        raise ValueError('CFTC_REVISION_BODY_BOUND')
    try:
        raw = body.decode('utf-8')
        rows = _loads(raw)
    except (UnicodeError, ValueError):
        raise ValueError('CFTC_REVISION_BODY_INVALID') from None
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise ValueError('CFTC_REVISION_ROWS_BOUND')
    versions, identities, reports = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or row.get('cftc_contract_market_code') != contract:
            raise ValueError('CFTC_REVISION_MARKET_MISMATCH')
        identity = row.get(':id')
        if not isinstance(identity, str) or not re.fullmatch(r'row-[\w.~:-]{1,128}', identity):
            raise ValueError('CFTC_REVISION_ROW_ID_INVALID')
        report = _timestamp(row.get('report_date_as_yyyy_mm_dd'), report=True)
        created = _timestamp(row.get(':created_at'))
        updated = _timestamp(row.get(':updated_at'))
        if not 0 < report <= created <= updated <= received:
            raise ValueError('CFTC_REVISION_CLOCK_ORDER_INVALID')
        if identity in identities or report in reports:
            raise ValueError('CFTC_REVISION_IDENTITY_CONFLICT')
        identities.add(identity); reports.add(report)
        net = _count(row.get('noncomm_positions_long_all')) - _count(row.get('noncomm_positions_short_all'))
        try:
            oi = _count(row.get('open_interest_all'))
        except ValueError:
            oi = None
        versions.append({'row_id': identity, 'report_ts': report,
            'source_created_at': created, 'source_updated_at': updated,
            'net_position': net, 'open_interest': oi,
            'open_interest_reason': None if oi is not None else 'MISSING_OR_INVALID'})
    packet = {'contract_version': CONTRACT, 'dataset_id': DATASET, 'market_code': contract,
        'source_id': source_id, 'received_ts': received,
        'raw_body_utf8': raw, 'raw_body_sha256': hashlib.sha256(body).hexdigest(),
        'versions': sorted(versions, key=lambda row: row['report_ts']),
        'timestamp_semantics': 'SOURCE_ROW_REVISION_NOT_PUBLICATION_OR_FIRST_RECEIPT',
        'publication_verified': False, 'historical_forecast_admission': False,
        'production_authority': False}
    if len(json.dumps(packet, separators=(',', ':'), allow_nan=False).encode()) > MAX_PACKET_BYTES:
        raise ValueError('CFTC_REVISION_PACKET_BOUND')
    return packet


def validate_versions(packet):
    if not isinstance(packet, dict) or not isinstance(packet.get('raw_body_utf8'), str):
        raise ValueError('CFTC_REVISION_PACKET_INVALID')
    if any(packet.get(key) is not False for key in (
            'publication_verified', 'historical_forecast_admission', 'production_authority')):
        raise ValueError('CFTC_REVISION_AUTHORITY_INVALID')
    expected = capture_versions(packet['raw_body_utf8'].encode('utf-8'),
        contract=packet.get('market_code'), received_ts=packet.get('received_ts'),
        source_id=packet.get('source_id'))
    if packet != expected:
        raise ValueError('CFTC_REVISION_PACKET_HASH_OR_VALUES_MISMATCH')
    return expected
