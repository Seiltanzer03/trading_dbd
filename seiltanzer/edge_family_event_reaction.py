"""Bounded received postpublication market context, never broker-price equivalence.

No fetching, interpolation or fitting. The digest is of retained identity,
receipts and completed close samples, not a raw provider HTTP response.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from .canonical_market_context import canonical_instrument_code
from .config import get_instrument

CONTRACT = 'edge-family-event-reaction-v1'
MAX_EXTENSION_BYTES = 4096
MAX_ROOT_BYTES = 4096
MAX_NORMALIZED_META_BYTES = 8000
EXTENSION_FIELDS = frozenset(('event_reaction_contract', 'reaction_release', 'reaction_series'))
OBSERVATION_KEYS = ('source_id', 'provider', 'source_symbol', 'source_instrument', 'target_instrument',
    'base_currency', 'quote_currency', 'orientation', 'price_basis', 'source_verified', 'direct_source',
    'derived', 'proxy', 'broker_execution_bars', 'received_ts', 'available_at', 'samples')
ROLE = 'CONFIGURED_MARKET_CONTEXT_NOT_BROKER_PRICE'
FLAGS = ('synthetic', 'demo', 'is_demo', 'synthetic_demo', 'contract_fixture')
DECLARATIONS = ('context_only', 'horizon_minutes', *FLAGS)
ROOT_KEYS = ('source_id', 'source_verified', 'instrument', 'observed_ts', 'received_ts', 'available_at',
    'published_at', 'release_id', 'event_type', 'quality', *DECLARATIONS)
RELEASE_KEYS = ('source_id', 'release_id', 'event_type', 'source_url', 'published_at', 'received_ts',
    'available_at', 'body_sha256', 'source_verified', 'publication_basis', 'hash_kind',
    'historical_reconstruction', 'source_vintage_guarantee', *DECLARATIONS)
SERIES_KEYS = (*OBSERVATION_KEYS, 'authority_role', 'hash_kind', 'observation_sha256', *DECLARATIONS)
REACTION_NAME = re.compile(r'event\.[a-z][a-z0-9_]{0,31}\.reaction_(return_1m|return_5m|start_delay_seconds)\Z')


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError('REACTION_NUMBER_INVALID')
    return float(value)


def _text(value, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > limit:
        raise ValueError('REACTION_IDENTITY_UNBOUNDED_OR_MISSING')
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('REACTION_DIGEST_INVALID')
    return value


def _bounded(value, level=0, field=None, *, max_bytes=MAX_EXTENSION_BYTES):
    if level > 8:
        raise ValueError('REACTION_DEPTH_BOUND_EXCEEDED')
    if isinstance(value, dict):
        for key, item in value.items():
            _text(key)
            _bounded(item, level + 1, key)
    elif isinstance(value, list):
        for item in value:
            _bounded(item, level + 1)
    elif isinstance(value, str):
        # This normalized field is derived from the already bounded release ID,
        # so its deterministic prefix is not part of the source-ID allowance.
        limit = 2048 if field == 'source_url' else 128 + len('release:') if field == 'dependency_group' else 128
        _text(value, limit)
    if level == 0 and len(json.dumps(value, ensure_ascii=False, allow_nan=False,
            separators=(',', ':')).encode('utf-8')) > max_bytes:
        raise ValueError('REACTION_BYTE_BOUND_EXCEEDED')


def _reaction_projection(source):
    """Canonical 4096-byte extension projection at both admission boundaries."""
    projection = {key: source[key] for key in EXTENSION_FIELDS}
    _bounded(projection, max_bytes=MAX_EXTENSION_BYTES)
    return projection


def _scope(item, parent):
    if not isinstance(item, dict) or any(item.get(flag) for flag in FLAGS) or item.get('context_only'):
        raise ValueError('REACTION_SYNTHETIC_OR_CONTEXT_ONLY')
    if 'horizon_minutes' in item:
        horizon = _number(item['horizon_minutes'])
        if horizon <= 0 or ('horizon_minutes' in parent and horizon != _number(parent['horizon_minutes'])):
            raise ValueError('REACTION_APPLICABILITY_INVALID')
    return {key: deepcopy(item[key]) for key in DECLARATIONS if key in item}


def _receipt(item):
    receipt = _number(item['received_ts'])
    if _number(item['available_at']) != receipt:
        raise ValueError('REACTION_RECEIPT_MISMATCH')
    return receipt


def _configured_identity(instrument):
    configured = get_instrument(instrument)
    if configured and configured.asset_class == 'crypto' and configured.binance_symbol:
        symbol = configured.binance_symbol
        # The quote remains USDT even though the application code is BTCUSD.
        if not symbol.endswith('USDT'):
            raise ValueError('REACTION_CONFIGURED_QUOTE_UNSUPPORTED')
        return dict(provider='Binance', source_symbol=symbol, base_currency=symbol[:-4],
            quote_currency='USDT', price_basis='DIRECT_CONFIGURED_CRYPTO_SPOT_CONTEXT')
    symbol = {'EURUSD': 'EURUSD=X', 'USDCAD': 'CAD=X'}.get(instrument)
    if configured and configured.yahoo == symbol and symbol:
        return dict(provider='Yahoo', source_symbol=symbol, base_currency=instrument[:3],
            quote_currency=instrument[3:], price_basis='DIRECT_QUOTED_FX_PAIR_CONTEXT')
    raise ValueError('REACTION_CONFIGURED_DIRECT_CONTEXT_UNSUPPORTED')


def reaction_observation_sha256(series: dict) -> str:
    """SHA256 of exactly OBSERVATION_KEYS, canonical compact sorted UTF-8 JSON."""
    projection = {key: series[key] for key in OBSERVATION_KEYS}
    return hashlib.sha256(json.dumps(projection, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _validate(source, cutoff, instrument):
    _reaction_projection(source)
    if source['event_reaction_contract'] != CONTRACT:
        raise ValueError('REACTION_EXTENSION_PARTIAL_OR_UNKNOWN')
    _scope(source, source)
    cutoff = _number(cutoff)
    if instrument != canonical_instrument_code(instrument) or source['instrument'] != instrument:
        raise ValueError('REACTION_TARGET_IDENTITY_INVALID')
    _text(source['source_id']); _text(source['release_id'])
    kind = _text(source['event_type'])
    if not re.fullmatch('[a-z][a-z0-9_]{0,31}', kind):
        raise ValueError('REACTION_EVENT_TYPE_INVALID')
    release, series = source['reaction_release'], source['reaction_series']
    scopes = [_scope(item, source) for item in (source, release, series)]
    if len({item['horizon_minutes'] for item in scopes if 'horizon_minutes' in item}) > 1:
        raise ValueError('REACTION_CONSTITUENT_HORIZON_MISMATCH')
    if not 0 < _number(source.get('quality', 1.)) <= 1:
        raise ValueError('REACTION_SOURCE_QUALITY_INVALID')
    if set(release) - set(RELEASE_KEYS) or set(series) - set(SERIES_KEYS):
        raise ValueError('REACTION_UNKNOWN_PROOF_FIELD')
    for item in (release, series):
        _text(item['source_id'])
        if item.get('source_verified') is not True:
            raise ValueError('REACTION_SUPPORT_UNVERIFIED')
    if len({source['source_id'], release['source_id'], series['source_id']}) != 3:
        raise ValueError('REACTION_SOURCE_ID_COLLISION')
    for key in ('published_at', 'release_id', 'event_type'):
        if release[key] != source[key]:
            raise ValueError('REACTION_RELEASE_ROOT_MISMATCH')
    _text(release['source_url'], 2048); _digest(release['body_sha256'])
    if (release['publication_basis'] != 'VERIFIED_PUBLICATION_TIMESTAMP'
            or release['hash_kind'] != 'OFFICIAL_NORMALIZED_DOCUMENT_SHA256'):
        raise ValueError('REACTION_PUBLICATION_OR_DOCUMENT_HASH_BASIS_INVALID')
    identity = _configured_identity(instrument)
    if (any(series[key] != expected for key, expected in identity.items())
            or series['source_instrument'] != instrument or series['target_instrument'] != instrument
            or series['source_id'] != identity['provider'] + ':' + identity['source_symbol'] + ':1m'
            or series['orientation'] != 'direct' or series['authority_role'] != ROLE
            or series['direct_source'] is not True or series['derived'] is not False
            or series['proxy'] is not False or series['broker_execution_bars'] is not False):
        raise ValueError('REACTION_CONFIGURED_PRICE_IDENTITY_INVALID')
    if (series['hash_kind'] != 'FROZEN_CLOSE_OBSERVATIONS_SHA256'
            or _digest(series['observation_sha256']) != reaction_observation_sha256(series)):
        raise ValueError('REACTION_OBSERVATION_HASH_INVALID')
    publication = _number(source['published_at'])
    release_receipt, series_receipt, parent_receipt = map(_receipt, (release, series, source))
    if (source.get('source_verified') is not True or not 0 < publication <= release_receipt <= parent_receipt <= cutoff
            or not 0 < series_receipt <= parent_receipt or parent_receipt != max(release_receipt, series_receipt)):
        raise ValueError('REACTION_PARENT_OR_RELEASE_CLOCK_INVALID')
    samples = series['samples']
    if not isinstance(samples, list) or not 2 <= len(samples) <= 6:
        raise ValueError('REACTION_SAMPLE_BOUND_OR_WINDOW_MISSING')
    start = math.ceil(publication / 60) * 60
    for index, sample in enumerate(samples):
        if not isinstance(sample, list) or len(sample) != 4:
            raise ValueError('REACTION_SAMPLE_INVALID')
        bar_start, end, close, receipt = map(_number, sample)
        if (bar_start != start + index*60 or end != bar_start+60 or close <= 0
                or not end <= receipt == series_receipt <= parent_receipt):
            raise ValueError('REACTION_GRID_PRICE_OR_BATCH_RECEIPT_INVALID')
    if _number(source['observed_ts']) != samples[-1][1]:
        raise ValueError('REACTION_OBSERVED_ENDPOINT_MISMATCH')
    return kind, publication, samples, scopes


def event_reaction_features(source: dict, cutoff: float, target_instrument: str) -> dict:
    result = dict(features={}, feature_provenance={}, rejections=[])
    try:
        if not isinstance(source, dict):
            raise ValueError('REACTION_SOURCE_NOT_OBJECT')
        if not EXTENSION_FIELDS.intersection(source):
            return result
        if not EXTENSION_FIELDS.issubset(source):
            raise ValueError('REACTION_EXTENSION_PARTIAL_OR_UNKNOWN')
        kind, published, samples, scopes = _validate(source, cutoff, target_instrument)
        release, series = source['reaction_release'], source['reaction_series']
        root = {key: deepcopy(source[key]) for key in ROOT_KEYS if key in source}
        supports = [deepcopy(release), deepcopy(series)]
        meta = dict(source_id=source['source_id'], source_verified=True,
            source_instrument=target_instrument, global_context=False, proxy_mapping=None,
            observed_ts=source['observed_ts'], received_ts=source['received_ts'], published_at=published,
            max_age_sec=14400., quality=source.get('quality', 1.), dependency_group='release:' + source['release_id'],
            release_id=source['release_id'], reaction_contract_version=CONTRACT,
            root_provenance=root, constituent_provenance=supports,
            applicability_provenance=scopes, supporting_source_ids=[item['source_id'] for item in supports],
            supporting_body_sha256={release['source_id']: release['body_sha256'], series['source_id']: series['observation_sha256']},
            supporting_hash_kinds={item['source_id']: item['hash_kind'] for item in supports})
        values = [('return_1m', math.log(samples[1][2]) - math.log(samples[0][2]), 1, 60),
                  ('start_delay_seconds', samples[0][0] - published, 0, 0)]
        if len(samples) == 6:
            values.append(('return_5m', math.log(samples[5][2]) - math.log(samples[0][2]), 5, 300))
        for suffix, value, index, interval in values:
            feature = f'event.{kind}.reaction_{suffix}'
            result['features'][feature] = value
            result['feature_provenance'][feature] = deepcopy(dict(meta,
                window_seconds=samples[index][1] - published, return_interval_seconds=interval))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        result = dict(features={}, feature_provenance={}, rejections=[dict(
            source_id=str(source.get('source_id') or '')[:128] if isinstance(source, dict) else '',
            reason=str(exc)[:160])])
    return result


def reaction_provenance_reason(feature: str, value: float, meta: dict,
                               captured: float, horizon: float, instrument: str) -> str | None:
    """Rebuild the bounded frozen proof and independently recompute its value."""
    explicit = isinstance(meta, dict) and 'reaction_contract_version' in meta
    if not isinstance(feature, str) or not REACTION_NAME.fullmatch(feature):
        return 'FEATURE_REACTION_PROVENANCE_INVALID' if explicit else None
    try:
        if not isinstance(meta, dict) or meta['reaction_contract_version'] != CONTRACT:
            raise ValueError('REACTION_PROOF_MISSING')
        root, supports = meta['root_provenance'], meta['constituent_provenance']
        if not isinstance(root, dict) or set(root) - set(ROOT_KEYS) or not isinstance(supports, list) or len(supports) != 2:
            raise ValueError('REACTION_NORMALIZED_PROOF_INVALID')
        _bounded(root, max_bytes=MAX_ROOT_BYTES)
        _bounded(meta, max_bytes=MAX_NORMALIZED_META_BYTES)
        packet = dict(root, event_reaction_contract=CONTRACT, reaction_release=supports[0], reaction_series=supports[1])
        _reaction_projection(packet)
        result = event_reaction_features(packet, captured, instrument)
        expected = result['feature_provenance'][feature]
        if set(meta) != set(expected):
            raise ValueError('REACTION_NORMALIZED_FIELDS_INVALID')
        if _number(value) != result['features'][feature]:
            raise ValueError('REACTION_VALUE_MISMATCH')
        for key, wanted in expected.items():
            if meta.get(key) != wanted:
                raise ValueError('REACTION_NORMALIZED_AGGREGATE_MISMATCH')
        from .edge_family_history import feature_applicability_reason
        if feature_applicability_reason(meta, horizon):
            raise ValueError('REACTION_APPLICABILITY_MISMATCH')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return 'FEATURE_REACTION_PROVENANCE_INVALID'
    return None


def build_received_event_reaction_source(release: dict, feed, cutoff: float, instrument: str) -> dict:
    """Copy the existing locked feed batch only; do not refresh or read archives."""
    result = dict(source=None, rejections=[])
    try:
        _number(cutoff)
        if not isinstance(release, dict) or release.get('status', 'VALID') != 'VALID':
            raise ValueError('RECEIVED_FOMC_RELEASE_UNAVAILABLE')
        normalized = {key: deepcopy(release[key]) for key in RELEASE_KEYS if key in release}
        # Actual receipt is mandatory. A legacy available_at=publication read is
        # not enough to construct a reaction packet.
        _receipt(normalized)
        _scope(normalized, normalized)
        identity = _configured_identity(instrument)
        from .edge_regime import AUTHORITY_CONTRACT
        with feed._intraday_lock:
            authority = deepcopy(feed.intraday_source_authority)
            bars = deepcopy(feed.intraday_ohlcv)
            if feed.demo or feed.intraday_is_offset or feed.instrument_code != instrument:
                raise ValueError('REACTION_FEED_DEMO_OFFSET_OR_TARGET_INVALID')
        _scope(authority, normalized)
        if (authority.get('contract_version') != AUTHORITY_CONTRACT
                or _number(authority.get('interval_sec')) != 60
                or authority.get('authority_role') != identity['price_basis']
                or authority.get('source_instrument') != instrument
                or authority.get('target_instrument') != instrument
                or any(authority.get(key) != identity[key] for key in ('provider', 'source_symbol'))
                or authority.get('source_id') != identity['provider'] + ':' + identity['source_symbol'] + ':1m'
                or authority.get('source_verified') is not True or authority.get('direct_source') is not True
                or authority.get('derived') is not False or authority.get('proxy') is not False
                or authority.get('broker_execution_bars') is not False):
            raise ValueError('REACTION_CONFIGURED_FEED_AUTHORITY_INVALID')
        receipt = _number(authority['available_at'])
        if 'received_ts' in authority and _number(authority['received_ts']) != receipt:
            raise ValueError('REACTION_FEED_RECEIPT_MISMATCH')
        observed = _number(authority['observed_ts'])
        quality = _number(authority['quality'])
        if not 0 < quality <= 1:
            raise ValueError('REACTION_FEED_QUALITY_INVALID')
        if not 0 < observed <= receipt <= cutoff:
            raise ValueError('REACTION_FEED_BATCH_AFTER_CAPTURE')
        published = _number(normalized['published_at'])
        start = math.ceil(published/60)*60
        if not isinstance(bars, (list, tuple)) or len(bars) > 4096:
            raise ValueError('REACTION_FEED_BAR_BOUND_INVALID')
        retained = {}
        for bar in bars:
            if not isinstance(bar, (list, tuple)) or len(bar) < 5:
                raise ValueError('REACTION_FEED_BAR_INVALID')
            stamp = _number(bar[0])
            # Future/uncompleted/out-of-window observations are never features.
            if not start <= stamp < start+360 or stamp+60 > receipt:
                continue
            opened, high, low, close = map(_number, bar[1:5])
            if (stamp % 60 or min(opened, high, low, close) <= 0
                    or low > min(opened, close) or high < max(opened, close) or low > high
                    or stamp in retained or stamp+60 > observed):
                raise ValueError('REACTION_FEED_OHLC_GRID_OR_DUPLICATE_INVALID')
            retained[stamp] = close
        expected_n = min(6, int((receipt-start)//60))
        if expected_n < 2 or set(retained) != {start+i*60 for i in range(expected_n)}:
            raise ValueError('REACTION_COMPLETED_CONSECUTIVE_WINDOW_UNAVAILABLE')
        series = dict(identity, source_id=authority['source_id'], source_instrument=instrument,
            target_instrument=instrument, orientation='direct', authority_role=ROLE,
            source_verified=True, direct_source=True, derived=False, proxy=False, broker_execution_bars=False,
            received_ts=receipt, available_at=receipt, hash_kind='FROZEN_CLOSE_OBSERVATIONS_SHA256',
            samples=[[stamp, stamp+60, retained[stamp], receipt] for stamp in sorted(retained)],
            **{key: deepcopy(authority[key]) for key in DECLARATIONS if key in authority})
        series['observation_sha256'] = reaction_observation_sha256(series)
        parent_receipt = max(receipt, normalized['received_ts'])
        source = dict(source_id='reaction:' + normalized['release_id'] + ':' + instrument,
            source_verified=True, available=True, quality=quality, instrument=instrument,
            observed_ts=series['samples'][-1][1], received_ts=parent_receipt, available_at=parent_receipt,
            published_at=published, release_id=normalized['release_id'], event_type=normalized['event_type'],
            event_reaction_contract=CONTRACT, reaction_release=normalized, reaction_series=series)
        validation = event_reaction_features(source, cutoff, instrument)
        if validation['rejections']:
            return dict(source=None, rejections=validation['rejections'])
        result['source'] = source
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        result['rejections'] = [dict(source_id='', reason=str(exc)[:160])]
    return result


def attach_observed_event_reaction(engine, snapshot: dict) -> None:
    """Append prospective observed context before the immutable review identity."""
    audit = dict(contract_version=CONTRACT, available=False, network_calls=False,
        reason='RECEIVED_FOMC_STORE_UNAVAILABLE', authority_role=ROLE, rejections=[])
    snapshot['edge_family_event_reaction_audit'] = audit
    try:
        cutoff = _number(snapshot['captured_ts'])
        instrument = canonical_instrument_code((snapshot.get('strategy') or {}).get('instrument') or snapshot.get('instrument'))
        factory = getattr(getattr(engine, 'passive', None), '_macro_data_factory', None)
        store = getattr(factory, 'fomc_deterministic_store', None)
        if store is None:
            return
        release = store.latest_received(cutoff)
        produced = build_received_event_reaction_source(release, engine.market, cutoff, instrument)
        if produced['source'] is None:
            audit.update(reason=produced['rejections'][0]['reason'], rejections=produced['rejections'])
            return
        source = produced['source']
        existing = snapshot.get('edge_family_sources', {})
        if not isinstance(existing, dict):
            raise ValueError('EXISTING_SOURCE_FACTS_PRESERVED_UNMERGEABLE')
        prior = existing.get('event', [])
        if isinstance(prior, dict):
            prior = [prior]
        if not isinstance(prior, list):
            raise ValueError('EXISTING_EVENT_FACTS_PRESERVED_UNMERGEABLE')
        if any(isinstance(item, dict) and item.get('source_id') == source['source_id'] for item in prior):
            audit['reason'] = 'EXISTING_REACTION_SOURCE_PRESERVED'
            return
        if len(prior) >= 128:
            raise ValueError('SOURCE_FAMILY_RECORD_BOUND_EXCEEDED')
        combined = {**existing, 'event': [*prior, source]}
        from .edge_family_source_runtime import MAX_SELECTED_BYTES
        if len(json.dumps(combined, ensure_ascii=True, allow_nan=False).encode('utf-8')) > MAX_SELECTED_BYTES:
            raise ValueError('SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET')
        snapshot['edge_family_sources'] = combined
        audit.update(available=True, reason='RECEIVED_OBSERVED_EVENT_REACTION_ATTACHED',
            source_id=source['source_id'], source_bytes=len(json.dumps(source, separators=(',', ':')).encode()))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        audit['reason'] = str(exc)[:160]
