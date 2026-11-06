"""Lossless, bounded transport for retained frozen option metric facts."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import zlib

CONTRACT = 'frozen-option-metric-transport-v1'
MAX_RAW_BYTES = 32_000
MAX_ENCODED_BYTES = 45_000


def is_encoded_option_state(value):
    return isinstance(value, dict) and ('option_metrics_encoded' in value
                                       or value.get('transport_contract') == CONTRACT)


def _invalid():
    return {'available': False, 'metrics': {}, 'reason': 'INVALID_OPTION_METRIC_TRANSPORT'}


def _nonfinite(value):
    raise ValueError('NONFINITE_OPTION_METRIC')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _nonfinite(value)
    return number


def unpack_option_state(value):
    if not is_encoded_option_state(value):
        return value if isinstance(value, dict) else {}
    try:
        encoded = value.get('option_metrics_encoded')
        if (value.get('transport_contract') != CONTRACT or value.get('encoding') != 'json-zlib-v1'
                or not isinstance(encoded, str) or not 0 < len(encoded) <= MAX_ENCODED_BYTES):
            return _invalid()
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(encoded, validate=True), MAX_RAW_BYTES + 1)
        if (len(raw) > MAX_RAW_BYTES or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail or hashlib.sha256(raw).hexdigest() != value.get('raw_sha256')):
            return _invalid()
        result = json.loads(raw, parse_constant=_nonfinite, parse_float=_finite_float)
        if not isinstance(result, dict) or is_encoded_option_state(result):
            return _invalid()
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, zlib.error):
        return _invalid()


def pack_option_state(value):
    if not isinstance(value, dict) or is_encoded_option_state(value):
        return value
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_RAW_BYTES:
            return value
        result = {'transport_contract': CONTRACT, 'encoding': 'json-zlib-v1',
                  'raw_sha256': hashlib.sha256(raw).hexdigest(),
                  'option_metrics_encoded': base64.b64encode(zlib.compress(raw)).decode()}
        return result if len(json.dumps(result, separators=(',', ':')).encode()) < len(raw) else value
    except (ValueError, TypeError, RecursionError):
        return value
