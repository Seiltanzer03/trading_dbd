"""Frozen unsigned lexical distance of exact received FOMC decision sentences.

The store verifies full normalized documents. Frozen replay verifies the scoped
sentence digest and metric; a snippet does not authenticate the original body.
No acquisition, sentiment, forecast, broker or outcome authority is supplied.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from .canonical_market_context import canonical_instrument_code

CONTRACT = 'edge-family-fomc-policy-sentence-novelty-v1'
FEATURE = 'event.fomc.policy_sentence_lexical_distance'
NAMESPACE = 'event.fomc.policy_sentence_'
REGION = 'FOMC_TARGET_RATE_DECISION_SENTENCE_V1'
TOKENIZER = 'ASCII_WORD_DECIMAL_SET_V1'
METRIC = 'UNSIGNED_TOKEN_SET_JACCARD_DISTANCE_V1'
UNIT = 'DIMENSIONLESS_JACCARD_DISTANCE'
ROLE = 'OBSERVED_OFFICIAL_TEXT_NOT_CAUSAL_EFFECT_OR_BROKER_OUTCOME'
EXTENSION_FIELDS = frozenset(('event_novelty_contract', 'novelty_current', 'novelty_previous'))
FLAGS = ('synthetic', 'demo', 'is_demo', 'synthetic_demo', 'contract_fixture')
DECLARATIONS = ('context_only', 'horizon_minutes', *FLAGS)
ROOT_KEYS = ('source_id', 'source_verified', 'available', 'global_context', 'quality',
    'observed_ts', 'received_ts', 'available_at', 'published_at', 'release_id', 'event_type',
    'authority_role', *DECLARATIONS)
SUPPORT_KEYS = ('source_id', 'release_id', 'date_code', 'source_url', 'published_at',
    'received_ts', 'created_ts', 'available_at', 'body_sha256', 'contract_version',
    'previous_release_id', 'previous_source_url', 'source_verified', 'hash_kind',
    'historical_reconstruction', 'source_vintage_guarantee', 'sentence_text',
    'sentence_start', 'sentence_end', 'sentence_sha256', 'projection_hash_kind', *DECLARATIONS)
NATIVE_CONTRACT = 'fomc-deterministic-point-in-time-v1'
NATIVE_KEYS = ('release_id', 'date_code', 'source_url', 'published_at', 'fetched_at',
    'created_ts', 'body_text', 'body_sha256', 'contract_version', 'previous_release_id', 'previous_source_url')


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError('NOVELTY_NUMBER_INVALID')
    return float(value)


def _text(value, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or len(value.encode('utf-8')) > limit:
        raise ValueError('NOVELTY_IDENTITY_OR_TEXT_BOUND_INVALID')
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('NOVELTY_DIGEST_INVALID')
    return value


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _bounded(value, limit=4096, depth=0, field=None):
    if depth > 8:
        raise ValueError('NOVELTY_DEPTH_BOUND_EXCEEDED')
    if isinstance(value, dict):
        for key, item in value.items():
            _text(key)
            _bounded(item, limit, depth+1, key)
    elif isinstance(value, list):
        for item in value:
            _bounded(item, limit, depth+1)
    elif isinstance(value, str):
        _text(value, 768 if field == 'sentence_text' else 2048 if field in ('source_url', 'previous_source_url') else 136 if field == 'dependency_group' else 128)
    elif not (value is None or isinstance(value, (bool, int, float))):
        raise ValueError('NOVELTY_JSON_INVALID')
    if depth == 0:
        for ascii_, separators in ((False, (',', ':')), (True, None)):
            if len(json.dumps(value, ensure_ascii=ascii_, allow_nan=False, separators=separators).encode('utf-8')) > limit:
                raise ValueError('NOVELTY_BYTE_BOUND_EXCEEDED')


def _scope(item, parent):
    if not isinstance(item, dict) or any(key in item and item[key] is not False for key in FLAGS) or ('context_only' in item and item['context_only'] is not False):
        raise ValueError('NOVELTY_SYNTHETIC_OR_CONTEXT_ONLY')
    if 'horizon_minutes' in item:
        horizon = _number(item['horizon_minutes'])
        if horizon <= 0 or ('horizon_minutes' in parent and horizon != _number(parent['horizon_minutes'])):
            raise ValueError('NOVELTY_APPLICABILITY_INVALID')
    return {key: deepcopy(item[key]) for key in DECLARATIONS if key in item}


def _identity(record, cutoff, *, receipt_key):
    code = _text(record['date_code'])
    url = _text(record['source_url'], 2048)
    if not re.fullmatch(r'https://(?:www\.)?federalreserve\.gov/newsevents/pressreleases/monetary' + re.escape(code) + r'a\.htm', url) or not re.fullmatch('[0-9]{8}', code):
        raise ValueError('NOVELTY_OFFICIAL_DATED_IDENTITY_INVALID')
    pub, receipt, created = (_number(record[key]) for key in ('published_at', receipt_key, 'created_ts'))
    if not 0 < pub <= receipt <= cutoff or not 0 < created <= cutoff:
        raise ValueError('NOVELTY_RECEIPT_OR_MATERIALIZATION_CLOCK_INVALID')
    if datetime.fromtimestamp(pub, ZoneInfo('America/New_York')).strftime('%Y%m%d') != code:
        raise ValueError('NOVELTY_PUBLICATION_DATE_MISMATCH')
    body_sha = _digest(record['body_sha256'])
    expected = 'macro-fomc-det-' + _sha(f'{code}|{url}|{pub:.6f}|{body_sha}')[:28]
    if _text(record['release_id']) != expected or record['contract_version'] != NATIVE_CONTRACT:
        raise ValueError('NOVELTY_NATIVE_RELEASE_ID_OR_CONTRACT_INVALID')
    for key in ('previous_release_id', 'previous_source_url'):
        if record[key] is not None:
            _text(record[key], 2048 if key.endswith('url') else 128)
    return pub, receipt, created


def validate_native_record(record, cutoff):
    _identity(record, _number(cutoff), receipt_key='fetched_at')
    text = _text(record['body_text'], 65536)
    if _sha(text) != record['body_sha256']:
        raise ValueError('NOVELTY_NORMALIZED_DOCUMENT_HASH_MISMATCH')


def validate_native_pair(current, previous, cutoff):
    if (current['previous_release_id'] != previous['release_id'] or current['previous_source_url'] != previous['source_url']
            or not previous['published_at'] < current['published_at']):
        raise ValueError('NOVELTY_EXACT_PREVIOUS_LINK_OR_ORDER_INVALID')
    if _number(cutoff) - current['published_at'] > 14400:
        raise ValueError('NOVELTY_CURRENT_PUBLICATION_STALE')


def _sentence(text):
    def qualifying(sentence):
        return (re.search(r'\bthe\s+Committee\s+decided\s+to\b', sentence, re.I)
                and re.search(r'\btarget\s+range\s+for\s+the\s+federal\s+funds\s+rate\b', sentence, re.I))

    found = []
    start = 0
    for index, char in enumerate(text):
        if char not in '.?!' or (char == '.' and index > 0 and index+1 < len(text) and text[index-1].isdigit() and text[index+1].isdigit()):
            continue
        raw = text[start:index+1]
        sentence = raw.strip()
        if qualifying(sentence):
            offset = start + len(raw) - len(raw.lstrip())
            found.append((sentence, offset, index+1))
        start = index+1
    if qualifying(text[start:]):
        raise ValueError('NOVELTY_DECISION_SENTENCE_INCOMPLETE')
    if len(found) != 1:
        raise ValueError('NOVELTY_DECISION_SENTENCE_ABSENT_OR_AMBIGUOUS')
    sentence, start, end = found[0]
    _text(sentence, 768)
    _tokens(sentence)
    return sentence, start, end


def _tokens(sentence):
    tokens = set(re.findall(r'[a-z]+|\d+(?:\.\d+)?', sentence.lower(), flags=re.ASCII))
    if not 0 < len(tokens) <= 128:
        raise ValueError('NOVELTY_COMPLETE_TOKEN_SET_BOUND_INVALID')
    return tokens


def build_received_event_novelty_source(pair: dict, cutoff: float) -> dict:
    result = dict(source=None, rejections=[])
    try:
        cutoff = _number(cutoff)
        if not isinstance(pair, dict) or pair.get('status') != 'AVAILABLE':
            raise ValueError(str(pair.get('reason', 'RECEIVED_FOMC_TEXT_PAIR_UNAVAILABLE'))[:160] if isinstance(pair, dict) else 'RECEIVED_FOMC_TEXT_PAIR_UNAVAILABLE')
        scope = _scope(pair, pair)
        current, previous = pair['current'], pair['previous']
        for record in (current, previous):
            _scope(record, pair)
            validate_native_record(record, cutoff)
        validate_native_pair(current, previous, cutoff)
        supports = []
        for record in (current, previous):
            sentence, start, end = _sentence(record['body_text'])
            support = {key: deepcopy(record[key]) for key in NATIVE_KEYS if key not in ('body_text', 'fetched_at')}
            support.update(source_id=record['release_id'], received_ts=record['fetched_at'],
                available_at=max(record['fetched_at'], record['created_ts']), source_verified=True,
                hash_kind='OFFICIAL_NORMALIZED_DOCUMENT_SHA256', historical_reconstruction=True,
                source_vintage_guarantee='OFFICIAL_DATED_PAGE_NOT_VERSIONED', sentence_text=sentence,
                sentence_start=start, sentence_end=end, sentence_sha256=_sha(sentence),
                projection_hash_kind='FROZEN_POLICY_SENTENCE_UTF8_SHA256')
            support.update(_scope(record, pair))
            supports.append(support)
        available = max(item['available_at'] for item in supports)
        source = dict(source_id='novelty:' + current['release_id'], source_verified=True, available=True,
            global_context=True, quality=1., observed_ts=current['published_at'], published_at=current['published_at'],
            received_ts=available, available_at=available, release_id=current['release_id'], event_type='fomc',
            authority_role=ROLE, event_novelty_contract=CONTRACT, novelty_current=supports[0], novelty_previous=supports[1])
        source.update(scope)
        validated = event_novelty_features(source, cutoff, 'NAS100')
        if validated['rejections']:
            raise ValueError(validated['rejections'][0]['reason'])
        result['source'] = source
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        result['rejections'] = [dict(reason=str(exc)[:160])]
    return result


def event_novelty_features(source: dict, cutoff: float, target_instrument: str) -> dict:
    result = dict(features={}, feature_provenance={}, rejections=[])
    try:
        if not isinstance(source, dict):
            raise ValueError('NOVELTY_SOURCE_NOT_OBJECT')
        if not EXTENSION_FIELDS.intersection(source):
            return result
        if not EXTENSION_FIELDS.issubset(source) or source['event_novelty_contract'] != CONTRACT:
            raise ValueError('NOVELTY_EXTENSION_PARTIAL_OR_UNKNOWN')
        if set(source) - set(ROOT_KEYS) - EXTENSION_FIELDS:
            raise ValueError('NOVELTY_UNKNOWN_ROOT_FIELD')
        _bounded({key: source[key] for key in EXTENSION_FIELDS})
        root = {key: deepcopy(source[key]) for key in ROOT_KEYS if key in source}
        _bounded(root)
        cutoff = _number(cutoff)
        if not target_instrument or target_instrument != canonical_instrument_code(target_instrument):
            raise ValueError('NOVELTY_TARGET_IDENTITY_INVALID')
        scopes = [_scope(source, source)]
        supports = [source['novelty_current'], source['novelty_previous']]
        for support in supports:
            scopes.append(_scope(support, source))
            if set(support) - set(SUPPORT_KEYS):
                raise ValueError('NOVELTY_UNKNOWN_PROOF_FIELD')
            _, receipt, created = _identity(support, cutoff, receipt_key='received_ts')
            if (support['available_at'] != max(receipt, created) or support['source_id'] != support['release_id']
                    or support['source_verified'] is not True or support['historical_reconstruction'] is not True
                    or support['source_vintage_guarantee'] != 'OFFICIAL_DATED_PAGE_NOT_VERSIONED'
                    or support['hash_kind'] != 'OFFICIAL_NORMALIZED_DOCUMENT_SHA256'
                    or support['projection_hash_kind'] != 'FROZEN_POLICY_SENTENCE_UTF8_SHA256'):
                raise ValueError('NOVELTY_SUPPORT_CLOCK_OR_HASH_BASIS_INVALID')
            sentence = _text(support['sentence_text'], 768)
            selected, start, end = _sentence(sentence)
            if selected != sentence or start != 0 or end != len(sentence) or _digest(support['sentence_sha256']) != _sha(sentence):
                raise ValueError('NOVELTY_EXACT_SENTENCE_OR_PROJECTION_HASH_INVALID')
            a, b = support['sentence_start'], support['sentence_end']
            if any(not isinstance(v, int) or isinstance(v, bool) for v in (a, b)) or not 0 <= a < b <= 65536 or b-a != len(sentence):
                raise ValueError('NOVELTY_SENTENCE_OFFSETS_INVALID')
        current, previous = supports
        validate_native_pair(current, previous, cutoff)
        if len({scope['horizon_minutes'] for scope in scopes if 'horizon_minutes' in scope}) > 1:
            raise ValueError('NOVELTY_CONSTITUENT_HORIZON_MISMATCH')
        available = max(item['available_at'] for item in supports)
        if (source['source_id'] != 'novelty:' + current['release_id'] or source['release_id'] != current['release_id']
                or source['source_verified'] is not True or source['available'] is not True or source['global_context'] is not True
                or _number(source['quality']) != 1. or source['event_type'] != 'fomc' or source['authority_role'] != ROLE
                or source['published_at'] != current['published_at'] or source['observed_ts'] != current['published_at']
                or _number(source['received_ts']) != available or _number(source['available_at']) != available):
            raise ValueError('NOVELTY_ROOT_IDENTITY_OR_CLOCK_MISMATCH')
        c, p = (_tokens(item['sentence_text']) for item in supports)
        value = 1. - len(c & p) / len(c | p)
        meta = dict(source_id=source['source_id'], source_verified=True, source_instrument='GLOBAL', global_context=True,
            proxy_mapping=None, observed_ts=source['observed_ts'], received_ts=available, published_at=source['published_at'],
            max_age_sec=14400., quality=1., dependency_group='release:' + current['release_id'], release_id=current['release_id'],
            novelty_contract_version=CONTRACT, selector_version=REGION, tokenizer_version=TOKENIZER, metric_version=METRIC,
            unit=UNIT, authority_role=ROLE, root_provenance=root, constituent_provenance=deepcopy(supports),
            applicability_provenance=scopes, supporting_source_ids=[item['source_id'] for item in supports],
            supporting_body_sha256={item['source_id']: item['body_sha256'] for item in supports},
            supporting_projection_sha256={item['source_id']: item['sentence_sha256'] for item in supports},
            supporting_hash_kinds={item['source_id']: item['hash_kind'] for item in supports})
        _bounded(meta, 8000)
        result['features'][FEATURE] = value
        result['feature_provenance'][FEATURE] = meta
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        result = dict(features={}, feature_provenance={}, rejections=[dict(source_id=str(source.get('source_id') or '')[:128] if isinstance(source, dict) else '', reason=str(exc)[:160])])
    return result


def _novelty_declared(meta):
    """Inspect only retained proof paths, before allowing a renamed feature.

    Fixed key membership avoids scanning arbitrary dictionaries; the bounded
    stack also refuses malformed/cyclic/oversized provenance containers without
    admitting a declaration hidden beyond the inspection budget.
    """
    markers = (*EXTENSION_FIELDS, 'novelty_contract_version', 'selector_version',
        'tokenizer_version', 'metric_version', 'supporting_projection_sha256',
        'sentence_text', 'sentence_start', 'sentence_end', 'sentence_sha256')
    pending = [(meta, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > 8 or visited > 128:
            raise ValueError('NOVELTY_DISPATCH_PROOF_BOUND_EXCEEDED')
        if isinstance(item, dict):
            source_id = item.get('source_id')
            if (any(key in item for key in markers) or item.get('unit') == UNIT
                    or item.get('authority_role') == ROLE
                    or item.get('projection_hash_kind') == 'FROZEN_POLICY_SENTENCE_UTF8_SHA256'
                    or isinstance(source_id, str) and source_id.startswith('novelty:')):
                return True
            for key in ('root_provenance', 'constituent_provenance'):
                if key in item:
                    pending.append((item[key], depth+1))
        elif isinstance(item, list):
            if visited + len(pending) + len(item) > 128:
                raise ValueError('NOVELTY_DISPATCH_PROOF_BOUND_EXCEEDED')
            pending.extend((child, depth+1) for child in item)
    return False


def novelty_provenance_reason(feature: str, value: float, meta: dict,
                              captured: float, horizon: float, instrument: str) -> str | None:
    try:
        explicit = _novelty_declared(meta)
    except ValueError:
        return 'FEATURE_NOVELTY_PROVENANCE_INVALID'
    reserved = isinstance(feature, str) and feature.startswith(NAMESPACE)
    if feature != FEATURE:
        return 'FEATURE_NOVELTY_PROVENANCE_INVALID' if explicit or reserved else None
    try:
        if not isinstance(meta, dict) or meta['novelty_contract_version'] != CONTRACT:
            raise ValueError('NOVELTY_PROOF_MISSING')
        _bounded(meta, 8000)
        root, supports = meta['root_provenance'], meta['constituent_provenance']
        if not isinstance(root, dict) or set(root) - set(ROOT_KEYS) or not isinstance(supports, list) or len(supports) != 2:
            raise ValueError('NOVELTY_NORMALIZED_PROOF_INVALID')
        packet = dict(root, event_novelty_contract=CONTRACT, novelty_current=supports[0], novelty_previous=supports[1])
        result = event_novelty_features(packet, captured, instrument)
        expected = result['feature_provenance'][FEATURE]
        # Canonical JSON distinguishes a boolean claim from a numeric aggregate;
        # Python dict equality alone considers True equal to1.0.
        canonical = lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if _number(value) != result['features'][FEATURE] or canonical(meta) != canonical(expected):
            raise ValueError('NOVELTY_NORMALIZED_VALUE_OR_METADATA_MISMATCH')
        from .edge_family_history import feature_applicability_reason
        if feature_applicability_reason(meta, horizon):
            raise ValueError('NOVELTY_APPLICABILITY_MISMATCH')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return 'FEATURE_NOVELTY_PROVENANCE_INVALID'
    return None


def novelty_capture_audit(reason: str) -> dict:
    return dict(contract_version=CONTRACT, available=False, network_calls=False,
                authority_role=ROLE, reason=reason, rejections=[])


def attach_observed_event_novelty(engine, snapshot: dict) -> None:
    """Append bounded already-received text proof; preserve every prior fact."""
    audit = novelty_capture_audit('RECEIVED_FOMC_TEXT_STORE_UNAVAILABLE')
    snapshot['edge_family_event_novelty_audit'] = audit
    try:
        cutoff = _number(snapshot['captured_ts'])
        factory = getattr(getattr(engine, 'passive', None), '_macro_data_factory', None)
        store = getattr(factory, 'fomc_deterministic_store', None)
        if store is None:
            return
        from .fomc_prospective_capture import prospective_admission_reason
        reason = prospective_admission_reason(store, cutoff)
        if reason:
            audit['reason'] = reason
            return
        pair = store.latest_received_text_pair(cutoff, nonblocking=True)
        produced = build_received_event_novelty_source(pair, cutoff)
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
            audit['reason'] = 'EXISTING_NOVELTY_SOURCE_PRESERVED'
            return
        if len(prior) >= 128:
            raise ValueError('SOURCE_FAMILY_RECORD_BOUND_EXCEEDED')
        combined = {**existing, 'event': [*prior, source]}
        from .edge_family_source_runtime import MAX_SELECTED_BYTES
        if len(json.dumps(combined, ensure_ascii=True, allow_nan=False).encode('utf-8')) > MAX_SELECTED_BYTES:
            raise ValueError('SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET')
        snapshot['edge_family_sources'] = combined
        audit.update(available=True, reason='RECEIVED_FOMC_POLICY_SENTENCE_NOVELTY_ATTACHED',
            source_id=source['source_id'], source_bytes=len(json.dumps(source, allow_nan=False).encode('utf-8')))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        audit['reason'] = str(exc)[:160]
