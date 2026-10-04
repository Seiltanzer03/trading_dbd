"""Bounded, immutable source archive; never manufactures observations or fills."""
from __future__ import annotations

import hashlib
import json
import math
import time

CONTRACT_VERSION = 'edge-family-archive-v1'
MAX_ARCHIVE_BYTES = 96_000_000
MAX_EXPORT_BYTES = 32_000_000
MAX_INPUT_BYTES = 96_000_000
MAX_EPISODES = 512
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_PATH_POINTS = 6000
MAX_DIAGNOSTICS = 1024
DIAGNOSTIC_LIMIT = 'ARCHIVE_DIAGNOSTIC_LIMIT'


class _Invalid(ValueError):
    pass


def _encoded(value, limit):
    """Stop serialization at its byte ceiling, rejecting cycles/nonfinite data."""
    pieces, size = [], 0
    try:
        for piece in json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False).iterencode(value):
            chunk = piece.encode('utf-8')
            size += len(chunk)
            if size > limit:
                raise _Invalid('PAYLOAD_TOO_LARGE')
            pieces.append(chunk)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, _Invalid):
            raise
        raise _Invalid('INVALID_JSON') from exc
    return b''.join(pieces)


def _clock(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise _Invalid('INVALID_CLOCK')
    return value


def _loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _Invalid('INVALID_JSON')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(_Invalid('INVALID_JSON')))
    except (ValueError, TypeError, RecursionError) as exc:
        raise _Invalid('INVALID_JSON') from exc


def _synthetic(value):
    return any(value.get(key) for key in ('synthetic', 'demo', 'is_demo', 'synthetic_demo'))


def _diagnostics(value, evictions=False):
    if not isinstance(value, list):
        raise _Invalid('INVALID_PREVIOUS')
    if len(value) > MAX_DIAGNOSTICS:
        raise _Invalid(DIAGNOSTIC_LIMIT)
    for item in value:
        if (not isinstance(item, dict) or set(item) - {'reason', 'review_id'}
                or not isinstance(item.get('reason'), str)
                or not 0 < len(item['reason']) <= 128):
            raise _Invalid('INVALID_PREVIOUS')
        identity = item.get('review_id')
        if ('review_id' in item or evictions or item['reason'] == 'IDENTITY_CONFLICT') and (
                not isinstance(identity, str) or not 0 < len(identity) <= 256):
            raise _Invalid('INVALID_PREVIOUS')
    return value


def _blocked_archive():
    # Never forget individual tombstones and later resurrect their identities.
    # Overflow quarantines the complete history until an explicit fresh rebuild.
    manifest = {'contract_version': CONTRACT_VERSION, 'episodes': [],
                'dataset_sha256': hashlib.sha256(b'[]').hexdigest(),
                'exclusions': [{'reason': DIAGNOSTIC_LIMIT}], 'evictions': []}
    _encoded(manifest, MAX_ARCHIVE_BYTES)
    return manifest


def _record(record, exported):
    if not isinstance(record, dict):
        raise _Invalid('INVALID_RECORD')
    if not isinstance(record.get('review_id'), str) or not 0 < len(record['review_id']) <= 256:
        raise _Invalid('INVALID_IDENTITY')
    captured = _clock(record.get('captured_ts'))
    if captured > exported:
        raise _Invalid('CAPTURE_AFTER_EXPORT')
    if _synthetic(record):
        raise _Invalid('SYNTHETIC_RECORD')
    raw = record.get('snapshot_json')
    if not isinstance(raw, str):
        raise _Invalid('SNAPSHOT_MISSING')
    if len(raw) > MAX_SNAPSHOT_BYTES or len(raw.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        raise _Invalid('SNAPSHOT_TOO_LARGE')
    if hashlib.sha256(raw.encode('utf-8')).hexdigest() != record.get('snapshot_sha256'):
        raise _Invalid('SNAPSHOT_HASH_MISMATCH')
    snapshot = _loads(raw)
    if not isinstance(snapshot, dict):
        raise _Invalid('INVALID_SNAPSHOT')
    _encoded(snapshot, MAX_SNAPSHOT_BYTES)
    if _synthetic(snapshot):
        raise _Invalid('SYNTHETIC_RECORD')
    if (snapshot.get('captured_ts') != captured or snapshot.get('trade_id') != record.get('trade_id')
            or snapshot.get('trade_id') is None):
        raise _Invalid('SNAPSHOT_IDENTITY_MISMATCH')
    strategy = snapshot.get('strategy') or {}
    if not isinstance(strategy, dict):
        raise _Invalid('INVALID_SNAPSHOT')
    instrument = strategy.get('instrument') or snapshot.get('instrument')
    if record.get('instrument') is not None and instrument != record['instrument']:
        raise _Invalid('SNAPSHOT_IDENTITY_MISMATCH')
    points = record.get('path_points')
    if not isinstance(points, list):
        raise _Invalid('INVALID_PATH')
    if len(points) > MAX_PATH_POINTS:
        raise _Invalid('PATH_TOO_LARGE')
    last = None
    for point in points:
        if not isinstance(point, dict):
            raise _Invalid('INVALID_PATH')
        try:
            _clock(point.get('r'))
            _clock(point.get('price'))
        except _Invalid as exc:
            raise _Invalid('INVALID_PATH') from exc
        ts = _clock(point.get('ts'))
        if ts > exported:
            raise _Invalid('OUTCOME_AFTER_EXPORT')
        if ts < captured or (last is not None and ts <= last):
            raise _Invalid('INVALID_PATH_CHRONOLOGY')
        last = ts
    replay = record.get('stored_replay')
    if replay is not None:
        if not isinstance(replay, dict):
            raise _Invalid('INVALID_REPLAY')
        ts = _clock(replay.get('resolved_ts'))
        if ts > exported:
            raise _Invalid('OUTCOME_AFTER_EXPORT')
        if ts < captured:
            raise _Invalid('INVALID_REPLAY_CHRONOLOGY')
    encoded = _encoded(record, MAX_EXPORT_BYTES)
    return _loads(encoded), encoded


def _merge(first, second):
    """Only actual append-only observations and consistent terminal records merge."""
    mutable = {'path_points', 'path_truncated', 'stored_replay'}
    if (_encoded({k: v for k, v in first.items() if k not in mutable}, MAX_EXPORT_BYTES)
            != _encoded({k: v for k, v in second.items() if k not in mutable}, MAX_EXPORT_BYTES)):
        raise _Invalid('IDENTITY_CONFLICT')
    left, right = first['path_points'], second['path_points']
    prefix = min(len(left), len(right))
    if (_encoded(left[:prefix], MAX_EXPORT_BYTES)
            != _encoded(right[:prefix], MAX_EXPORT_BYTES)):
        raise _Invalid('IDENTITY_CONFLICT')
    a, b = first.get('stored_replay'), second.get('stored_replay')
    if (a is not None and b is not None
            and _encoded(a, MAX_EXPORT_BYTES) != _encoded(b, MAX_EXPORT_BYTES)):
        raise _Invalid('IDENTITY_CONFLICT')
    # A shorter prefix cannot claim a terminal event absent from the longer one.
    if (len(left) < len(right) and a is not None and b is None
            or len(right) < len(left) and b is not None and a is None):
        raise _Invalid('IDENTITY_CONFLICT')
    if len(left) != len(right):
        return dict(second if len(right) > len(left) else first)
    # Equal paths may have an explicit terminal completion; retain that actual
    # export rather than manufacture a mixture of two exports' mutable fields.
    if (a is None) != (b is None):
        return dict(second if a is None else first)
    return dict(min((first, second), key=lambda row: (
        bool(row.get('path_truncated')), _encoded(row, MAX_EXPORT_BYTES))))


def assemble_archive(exports: list[dict], previous: dict | None = None) -> dict:
    """Validate, merge and retain newest whole episodes within hard bounds.

    Identity conflicts persist as exclusion tombstones. The hash pins canonical
    original episode records, including exact snapshot bytes and observed paths.
    """
    admitted, exclusions, evictions, conflicts = {}, [], [], set()
    diagnostic_keys, diagnostic_overflow = set(), False
    now = time.time()

    def exclude(reason, review_id=None):
        nonlocal diagnostic_overflow
        item = {'reason': reason}
        if isinstance(review_id, str) and 0 < len(review_id) <= 256:
            item['review_id'] = review_id
        key = tuple(sorted(item.items()))
        if reason == DIAGNOSTIC_LIMIT or (key not in diagnostic_keys
                                         and len(diagnostic_keys) >= MAX_DIAGNOSTICS):
            diagnostic_overflow = True
        elif key not in diagnostic_keys:
            diagnostic_keys.add(key)
            exclusions.append(item)

    def add(record, exported):
        if diagnostic_overflow:
            return
        identity = record.get('review_id') if isinstance(record, dict) else None
        try:
            clean, encoded = _record(record, exported)
            if identity in conflicts:
                return
            if identity in admitted:
                clean = _merge(admitted[identity][0], clean)
                encoded = _encoded(clean, MAX_EXPORT_BYTES)
            admitted[identity] = (clean, encoded)
        except _Invalid as exc:
            exclude(str(exc), identity)
            if str(exc) in {'IDENTITY_CONFLICT', 'SNAPSHOT_HASH_MISMATCH'} and isinstance(identity, str):
                exclude('IDENTITY_CONFLICT', identity)
                admitted.pop(identity, None)
                conflicts.add(identity)

    if previous is not None:
        try:
            encoded = _encoded(previous, MAX_ARCHIVE_BYTES)
            prior = _loads(encoded)
            if not isinstance(prior, dict):
                raise _Invalid('INVALID_PREVIOUS')
            if prior.get('contract_version') != CONTRACT_VERSION:
                raise _Invalid('PREVIOUS_VERSION_MISMATCH')
            records = prior.get('episodes')
            if not isinstance(records, list) or len(records) > MAX_EPISODES:
                raise _Invalid('PREVIOUS_EPISODE_LIMIT')
            if hashlib.sha256(_encoded(records, MAX_ARCHIVE_BYTES)).hexdigest() != prior.get('dataset_sha256'):
                raise _Invalid('PREVIOUS_HASH_MISMATCH')
            prior_exclusions = _diagnostics(prior.get('exclusions'))
            prior_evictions = _diagnostics(prior.get('evictions'), evictions=True)
            if len(prior_exclusions) + len(prior_evictions) > MAX_DIAGNOSTICS:
                raise _Invalid(DIAGNOSTIC_LIMIT)
            for item in prior_exclusions:
                exclude(item['reason'], item.get('review_id'))
                if item['reason'] == 'IDENTITY_CONFLICT':
                    conflicts.add(item['review_id'])
            for record in records:
                add(record, now)
        except (AttributeError, _Invalid) as exc:
            exclude(str(exc) if isinstance(exc, _Invalid) else 'INVALID_PREVIOUS')
    if diagnostic_overflow:
        return _blocked_archive()
    if not isinstance(exports, list):
        exclude('INVALID_EXPORTS')
        exports = []
    if len(exports) > MAX_EPISODES:
        exclude('TOO_MANY_EXPORTS')
        exports = []
    bounded_exports, input_bytes = [], 0
    for envelope in exports:
        try:
            input_bytes += len(_encoded(envelope, MAX_EXPORT_BYTES))
            bounded_exports.append(envelope)
        except _Invalid as exc:
            exclude(str(exc))
    if input_bytes > MAX_INPUT_BYTES:
        exclude('INPUT_TOO_LARGE')
        bounded_exports = []
    for envelope in bounded_exports:
        try:
            if not isinstance(envelope, dict):
                raise _Invalid('INVALID_EXPORT')
            if envelope.get('read_only') is not True:
                raise _Invalid('EXPORT_NOT_READ_ONLY')
            exported = _clock(envelope.get('exported_ts'))
            if exported > now:
                raise _Invalid('FUTURE_EXPORT')
            records = envelope.get('reviews')
            if not isinstance(records, list):
                raise _Invalid('INVALID_EXPORT')
            if len(records) > 32:
                raise _Invalid('EXPORT_TOO_MANY_REVIEWS')
            _encoded(envelope, MAX_EXPORT_BYTES)
            if _synthetic(envelope):
                raise _Invalid('SYNTHETIC_RECORD')
            for record in records:
                add(record, exported)
        except _Invalid as exc:
            exclude(str(exc))
    if diagnostic_overflow:
        return _blocked_archive()
    ordered = sorted(admitted.values(), key=lambda pair: (pair[0]['captured_ts'], pair[0]['review_id']))
    while len(ordered) > MAX_EPISODES:
        row, _ = ordered.pop(0)
        evictions.append({'review_id': row['review_id'], 'reason': 'ARCHIVE_EPISODE_LIMIT'})
        if len(exclusions) + len(evictions) > MAX_DIAGNOSTICS:
            return _blocked_archive()
    exclusions = [dict(items) for items in sorted({tuple(sorted(item.items())) for item in exclusions})]
    episode_size = 2 + sum(len(raw) for _, raw in ordered) + max(0, len(ordered) - 1)
    manifest = {'contract_version': CONTRACT_VERSION, 'episodes': [],
                'dataset_sha256': '0' * 64, 'exclusions': exclusions, 'evictions': evictions}
    def fits():
        try:
            return episode_size - 2 + len(_encoded(manifest, MAX_ARCHIVE_BYTES)) <= MAX_ARCHIVE_BYTES
        except _Invalid:
            return False

    while ordered and not fits():
        row, raw = ordered.pop(0)
        episode_size -= len(raw) + (1 if ordered else 0)
        evictions.append({'review_id': row['review_id'], 'reason': 'ARCHIVE_BYTE_LIMIT'})
        if len(exclusions) + len(evictions) > MAX_DIAGNOSTICS:
            return _blocked_archive()
    if not fits():
        return _blocked_archive()
    episodes = [row for row, _ in ordered]
    manifest['episodes'] = episodes
    manifest['dataset_sha256'] = hashlib.sha256(_encoded(episodes, MAX_ARCHIVE_BYTES)).hexdigest()
    _encoded(manifest, MAX_ARCHIVE_BYTES)
    return manifest
