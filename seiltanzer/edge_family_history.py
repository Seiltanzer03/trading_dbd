"""Bounded deterministic transforms of received history; no fetching or fitting."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from .canonical_market_context import canonical_instrument_code

POSITION_CONTRACT = 'edge-family-position-history-v1'
INTERMARKET_CONTRACT = 'edge-family-intermarket-history-v1'
POSITION_FIELDS = frozenset(('position_history_contract', 'position_series', 'position_change_history'))
INTERMARKET_FIELDS = frozenset(('intermarket_history_contract', 'historical_series'))
UNIVERSE = ('BTC-USD', 'ETH-USD', 'SOL-USD')
IDENTITY = ('series_id', 'kind', 'unit', 'category', 'venue')
FLAGS = ('synthetic', 'demo', 'is_demo', 'synthetic_demo', 'contract_fixture')


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError('HISTORY_NONFINITE_OR_NONNUMERIC_VALUE')
    return float(value)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > 128:
        raise ValueError('HISTORY_IDENTITY_MISSING_OR_UNBOUNDED')
    return value


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('HISTORY_BODY_HASH_INVALID')
    return value


def _depth(value, level=0):
    if level > 8:
        raise ValueError('HISTORY_DEPTH_BOUND_EXCEEDED')
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def _extension(source, fields, contract_key, contract, limit):
    if not isinstance(source, dict):
        raise ValueError('HISTORY_SOURCE_NOT_OBJECT')
    if not fields.intersection(source):
        return False
    if not fields.issubset(source) or source[contract_key] != contract:
        raise ValueError('HISTORY_EXTENSION_PARTIAL_OR_UNKNOWN')
    projection = {key: source[key] for key in fields}
    _depth(projection)
    if len(json.dumps(projection, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) > limit:
        raise ValueError('HISTORY_BYTE_BOUND_EXCEEDED')
    return True


def _declarations(value, parent):
    if not isinstance(value, dict):
        raise ValueError('HISTORY_PROVENANCE_NOT_OBJECT')
    if any(value.get(flag) for flag in FLAGS):
        raise ValueError('SYNTHETIC_HISTORY_NOT_ADMISSIBLE')
    if 'horizon_minutes' in value:
        horizon = _number(value['horizon_minutes'])
        if horizon <= 0:
            raise ValueError('HISTORY_HORIZON_INVALID')
        if 'horizon_minutes' in parent and horizon != _number(parent['horizon_minutes']):
            raise ValueError('HISTORY_CONSTITUENT_HORIZON_MISMATCH')
    return {key: deepcopy(value[key]) for key in ('context_only', 'horizon_minutes') if key in value}


def _position_declarations(value, parent):
    declarations = _declarations(value, parent)
    if value.get('context_only'):
        raise ValueError('CONTEXT_ONLY_POSITION_HISTORY_NOT_ADMISSIBLE')
    return declarations


def _review_target(source, cutoff):
    """Use only a mapping admitted by the existing source identity guard."""
    target = canonical_instrument_code(source.get('instrument'))
    mapping = source.get('proxy_mapping')
    if isinstance(mapping, dict) and mapping.get('validated') is True:
        mapped = canonical_instrument_code(mapping.get('target_instrument'))
        if mapped:
            # The pure transform shares admission, never creates a mapping.
            from .edge_family_adapters import _meta as source_meta
            meta, _ = source_meta(source, family='intermarket', instrument=mapped, cutoff=cutoff)
            if meta is not None:
                target = mapped
    return target


def _parent(source, cutoff):
    _declarations(source, source)
    cutoff = _number(cutoff)
    observed = _number(source['observed_ts'])
    receipt = _number(source.get('available_at', source.get('received_ts')))
    if 'published_at' in source and not 0 < _number(source['published_at']) <= receipt:
        raise ValueError('HISTORY_PARENT_PUBLICATION_CLOCK_INVALID')
    if 'received_ts' in source and _number(source['received_ts']) != receipt:
        raise ValueError('HISTORY_PARENT_RECEIPT_MISMATCH')
    if source.get('source_verified') is not True or not 0 < observed <= receipt <= cutoff:
        raise ValueError('HISTORY_PARENT_CLOCK_OR_VERIFICATION_INVALID')
    _text(source['source_id'])
    return observed, receipt


def _meta(source, constituents, window, **extra):
    declarations = [_declarations(item, source) for item in constituents]
    declarations.append(_declarations(source, source))
    return dict(source_id=source['source_id'], source_verified=True,
        history_contract_version=(source.get('position_history_contract') or source['intermarket_history_contract']),
        supporting_body_sha256={item['source_id']: item['body_sha256'] for item in constituents},
        received_ts=max(item['received_ts'] for item in constituents),
        supporting_source_ids=list(dict.fromkeys(item['source_id'] for item in constituents)),
        constituent_provenance=deepcopy(constituents), applicability_provenance=declarations,
        window_seconds=window, **extra)


def _result():
    return dict(features={}, feature_provenance={}, rejections=[])


def _reject(result, source, exc):
    # Do not echo unbounded or malformed source payloads into diagnostics.
    source_id = source.get('source_id') if isinstance(source, dict) else None
    result['rejections'] = [dict(source_id=str(source_id or '')[:128], reason=str(exc)[:160])]
    result['features'] = {}; result['feature_provenance'] = {}
    return result


def position_history_features(source: dict, cutoff: float) -> dict:
    result = _result()
    try:
        if not _extension(source, POSITION_FIELDS, 'position_history_contract', POSITION_CONTRACT, 2048):
            return result
        _position_declarations(source, source)
        observed, receipt = _parent(source, cutoff)
        series = source['position_series']
        _position_declarations(series, source)
        identity = {key: _text(series[key]) for key in IDENTITY}
        digest = _hash(series['body_sha256'])
        if identity['kind'] not in ('cot_report', 'fund_flow', 'observed_open_interest') or identity['kind'] != source['kind']:
            raise ValueError('POSITION_HISTORY_KIND_MISMATCH')
        report = _number(source['report_ts']); published = _number(source['published_at']); net = _number(source['net_position'])
        if not 0 < report <= published <= receipt or observed != report:
            raise ValueError('POSITION_HISTORY_CURRENT_CLOCK_INVALID')
        history = source['position_change_history']
        if not isinstance(history, list) or not history or len(history) > 8:
            raise ValueError('POSITION_HISTORY_RECORD_BOUND_OR_MISSING_PREDECESSOR')
        seen = {}
        for item in history:
            _position_declarations(item, source)
            stamp = _number(item['report_ts']); value = _number(item['net_position']); available = _number(item['available_at'])
            proof = item['provenance']
            _position_declarations(proof, source)
            if {key: _text(proof[key]) for key in IDENTITY} != identity:
                raise ValueError('POSITION_HISTORY_SERIES_IDENTITY_MISMATCH')
            _hash(proof['body_sha256']); _text(proof['source_id'])
            received = _number(proof['received_ts']); publication = _number(proof['published_at'])
            if proof.get('source_verified') is not True or not 0 < stamp < report or not stamp <= publication <= received == available <= receipt:
                raise ValueError('POSITION_HISTORY_CONSTITUENT_CLOCK_OR_VERIFICATION_INVALID')
            if stamp in seen and seen[stamp] != item:
                raise ValueError('POSITION_HISTORY_CONFLICTING_DUPLICATE')
            seen[stamp] = item
        previous = seen[max(seen)]
        current = dict(identity, source_id=source['source_id'], source_verified=True, body_sha256=digest,
            report_ts=report, published_at=published, received_ts=receipt, **_declarations(source, source))
        prior = dict(previous['provenance'], report_ts=previous['report_ts'])
        window = report - previous['report_ts']
        meta = _meta(source, [current, prior], window, **identity)
        # Declarations on the identity object are also binding.
        meta['applicability_provenance'].extend([_declarations(series, source),
            _declarations(previous, source), _declarations(previous['provenance'], source)])
        result['features'] = {f"positioning.{identity['kind']}.change": _number(net - previous['net_position']),
            f"positioning.{identity['kind']}.previous_report_age_days": window / 86400}
        result['feature_provenance'] = {name: deepcopy(meta) for name in result['features']}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return _reject(result, source, exc)
    return result


def intermarket_history_features(source: dict, cutoff: float, *, target_instrument: str | None = None) -> dict:
    result = _result()
    try:
        if not _extension(source, INTERMARKET_FIELDS, 'intermarket_history_contract', INTERMARKET_CONTRACT, 4096):
            return result
        observed, receipt = _parent(source, cutoff)
        series = source['historical_series']
        if not isinstance(series, list) or not 1 <= len(series) <= 3:
            raise ValueError('INTERMARKET_HISTORY_SERIES_BOUND_OR_MISSING')
        values, proofs = {}, {}
        for item in series:
            declarations = _declarations(item, source)
            symbol = item['symbol']
            if (symbol not in UNIVERSE or symbol in values or item['provider'] != 'COINBASE'
                    or item['base_currency'] != symbol.split('-')[0] or item['quote_currency'] != 'USD'
                    or item['orientation'] != 'direct'):
                raise ValueError('INTERMARKET_HISTORY_IDENTITY_OR_DUPLICATE_INVALID')
            source_id = _text(item['source_id']); digest = _hash(item['body_sha256'])
            available = _number(item['available_at'])
            if not observed <= available <= receipt:
                raise ValueError('INTERMARKET_HISTORY_RECEIPT_AFTER_PARENT')
            if 'received_ts' in item and _number(item['received_ts']) != available:
                raise ValueError('INTERMARKET_HISTORY_RECEIPT_MISMATCH')
            if item.get('source_verified', True) is not True:
                raise ValueError('INTERMARKET_HISTORY_UNVERIFIED')
            bars = item['bars']
            if not isinstance(bars, list) or len(bars) != 7:
                raise ValueError('INTERMARKET_HISTORY_EXACT_WINDOW_MISSING')
            prices = []
            for index, bar in enumerate(bars):
                if not isinstance(bar, list) or len(bar) != 3:
                    raise ValueError('INTERMARKET_HISTORY_BAR_INVALID')
                end, close, received = map(_number, bar)
                if end != observed - 360 + index * 60 or end % 60 or not 0 < end <= received == available or close <= 0:
                    raise ValueError('INTERMARKET_HISTORY_GAP_PRICE_OR_RECEIPT_INVALID')
                prices.append(close)
            name = 'COINBASE' + symbol
            value = math.log(prices[5]) - math.log(prices[0])
            values[symbol] = value
            proof = dict(source_id=source_id, provider='COINBASE', symbol=symbol,
                base_currency=item['base_currency'], quote_currency='USD', orientation='direct',
                body_sha256=digest, received_ts=available, start_ts=observed - 360,
                end_ts=observed - 60, **declarations)
            proofs[symbol] = proof
            feature = f'intermarket.{name}.return_5m_lag_1m'
            result['features'][feature] = value
            result['feature_provenance'][feature] = _meta(source, [proof], 360, lag_seconds=60,
                return_seconds=300, start_ts=observed - 360, end_ts=observed - 60)
        for index, left in enumerate(UNIVERSE):
            for right in UNIVERSE[index + 1:]:
                if left in values and right in values:
                    feature = f'intermarket.COINBASE{left}.COINBASE{right}.relative_return_5m'
                    result['features'][feature] = values[left] - values[right]
                    result['feature_provenance'][feature] = _meta(source, [proofs[left], proofs[right]], 300,
                        start_ts=observed - 360, end_ts=observed - 60, lag_seconds=60, ordered_pair=[left, right])
        target = (canonical_instrument_code(target_instrument) if target_instrument is not None
                  else _review_target(source, cutoff))
        own = next((symbol for symbol in UNIVERSE if target in (symbol.replace('-', ''), symbol.split('-')[0] + 'USDT')), None)
        peers = [symbol for symbol in UNIVERSE if symbol != own]
        received_peers = [symbol for symbol in peers if symbol in values]
        if len(received_peers) >= 2:
            breadth = dict(breadth_up_fraction_5m=sum(values[symbol] > 0 for symbol in received_peers) / len(received_peers),
                observed_n=len(received_peers), expected_n=len(peers), coverage=len(received_peers) / len(peers))
            meta = _meta(source, [proofs[symbol] for symbol in received_peers], 300,
                start_ts=observed - 360, end_ts=observed - 60, lag_seconds=60,
                universe=list(UNIVERSE), excluded_own_asset=own, related_peer_symbols=peers)
            for name, value in breadth.items():
                feature = 'intermarket.related_crypto.' + name
                result['features'][feature] = value
                result['feature_provenance'][feature] = deepcopy(meta)
        else:
            result['rejections'].append(dict(source_id=source['source_id'], reason='RELATED_CRYPTO_BREADTH_REQUIRES_TWO_ADMISSIBLE_PEERS'))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return _reject(result, source, exc)
    return result


_HISTORY_META_FIELDS = frozenset(('history_contract_version', 'constituent_provenance',
                                 'supporting_body_sha256'))
_CLOCK_FIELDS = frozenset(('observed_ts', 'received_ts', 'published_at', 'report_ts',
                          'available_at', 'start_ts', 'end_ts'))


def _bounded_provenance(value):
    _depth(value)
    # Reuse the existing selected-fact envelope, without changing source caps.
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()) > 8000:
        raise ValueError('HISTORY_PROVENANCE_BYTE_BOUND_EXCEEDED')


def _scope(value, horizon):
    if isinstance(value, dict):
        _declarations(value, {'horizon_minutes': horizon})
        if value.get('context_only'):
            raise ValueError('HISTORY_CONTEXT_ONLY_NOT_APPLICABLE')
        if 'applicability_provenance' in value:
            children = value['applicability_provenance']
            if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
                raise ValueError('HISTORY_APPLICABILITY_SCHEMA_INVALID')
        for child in value.values():
            _scope(child, horizon)
    elif isinstance(value, list):
        for child in value:
            _scope(child, horizon)


def feature_applicability_reason(meta: dict, horizon: float) -> str | None:
    """Validate explicit recursive scope, while retaining absent legacy scope."""
    try:
        projection = {key: meta[key] for key in ('context_only', 'horizon_minutes',
            'applicability_provenance', 'consensus_provenance', 'constituent_provenance') if key in meta}
        _bounded_provenance(projection)
        _scope(projection, _number(horizon))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return 'FEATURE_APPLICABILITY_INVALID'
    return None


def _proof_clocks(value, captured):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _CLOCK_FIELDS and not 0 < _number(item) <= captured:
                raise ValueError('HISTORY_CONSTITUENT_CLOCK_INVALID')
            _proof_clocks(item, captured)
    elif isinstance(value, list):
        for item in value:
            _proof_clocks(item, captured)


def _feature_history_contract(feature):
    if feature.startswith('positioning.') and feature.endswith(('.change', '.previous_report_age_days')):
        return POSITION_CONTRACT
    if (feature.startswith('intermarket.') and feature.endswith(('.return_5m_lag_1m', '.relative_return_5m'))
            or feature.startswith('intermarket.related_crypto.')):
        return INTERMARKET_CONTRACT
    return None


def history_provenance_reason(feature: str, meta: dict, captured: float,
                              horizon: float, instrument: str) -> str | None:
    """Validate normalized received history at offline dataset import.

    Hash manifests establish internal consistency with retained proof, not
    authenticity or statistical usefulness of independently supplied evidence.
    """
    expected = _feature_history_contract(feature)
    declared = _HISTORY_META_FIELDS.intersection(meta) | POSITION_FIELDS.intersection(meta) | INTERMARKET_FIELDS.intersection(meta)
    if expected is None and not declared:
        return None  # Legacy facts without a recognized historical extension.
    try:
        if expected is None or meta.get('history_contract_version') != expected or not _HISTORY_META_FIELDS.issubset(meta):
            raise ValueError('HISTORY_PROVENANCE_PARTIAL_OR_UNKNOWN')
        _bounded_provenance(meta)
        if feature_applicability_reason(meta, horizon):
            raise ValueError('HISTORY_APPLICABILITY_INVALID')
        captured = _number(captured)
        observed = _number(meta['observed_ts']); receipt = _number(meta['received_ts'])
        constituents = meta['constituent_provenance']
        supporting = meta['supporting_source_ids']; hashes = meta['supporting_body_sha256']
        if (not isinstance(constituents, list) or not constituents or len(constituents) > 3
                or not isinstance(supporting, list) or len(supporting) != len(set(supporting))
                or not isinstance(hashes, dict)):
            raise ValueError('HISTORY_PROVENANCE_SCHEMA_INVALID')
        ids = set()
        receipts = []
        for proof in constituents:
            if not isinstance(proof, dict):
                raise ValueError('HISTORY_CONSTITUENT_SCHEMA_INVALID')
            source_id = _text(proof['source_id']); digest = _hash(proof['body_sha256'])
            _proof_clocks(proof, captured)
            if proof.get('source_verified', True) is not True:
                raise ValueError('HISTORY_CONSTITUENT_UNVERIFIED')
            actual = _number(proof['received_ts'])
            if hashes.get(source_id) != digest or not 0 < actual <= receipt <= captured:
                raise ValueError('HISTORY_HASH_OR_RECEIPT_INCONSISTENT')
            ids.add(source_id); receipts.append(actual)
        if (set(supporting) != ids or set(hashes) != ids or receipt != max(receipts)
                or any(_hash(digest) != digest for digest in hashes.values())):
            raise ValueError('HISTORY_SUPPORT_OR_AGGREGATE_INCONSISTENT')
        window = _number(meta['window_seconds'])
        if expected == POSITION_CONTRACT:
            if len(constituents) != 2:
                raise ValueError('POSITION_HISTORY_EXACT_SUPPORT_MISSING')
            identity = {key: _text(meta[key]) for key in IDENTITY}
            if identity['kind'] not in ('cot_report', 'fund_flow', 'observed_open_interest') or feature not in (
                    f"positioning.{identity['kind']}.change", f"positioning.{identity['kind']}.previous_report_age_days"):
                raise ValueError('POSITION_HISTORY_FEATURE_IDENTITY_INVALID')
            current, previous = constituents
            for proof in constituents:
                if {key: _text(proof[key]) for key in IDENTITY} != identity or proof.get('source_verified') is not True:
                    raise ValueError('POSITION_HISTORY_SUPPORT_IDENTITY_INVALID')
                report, publication, actual = (_number(proof[key]) for key in ('report_ts', 'published_at', 'received_ts'))
                if not 0 < report <= publication <= actual:
                    raise ValueError('POSITION_HISTORY_SUPPORT_CLOCK_INVALID')
            if (current['source_id'] != meta['source_id'] or current['report_ts'] != observed
                    or current['report_ts'] != meta['report_ts']
                    or current['published_at'] != meta['published_at']
                    or not previous['report_ts'] < current['report_ts']
                    or current['report_ts'] - previous['report_ts'] != window
                    or previous['received_ts'] > current['received_ts']):
                raise ValueError('POSITION_HISTORY_AGGREGATE_INVALID')
        else:
            symbols = []
            for proof in constituents:
                symbol = proof['symbol']
                if (symbol not in UNIVERSE or symbol in symbols or proof['provider'] != 'COINBASE'
                        or proof['base_currency'] != symbol.split('-')[0] or proof['quote_currency'] != 'USD'
                        or proof['orientation'] != 'direct' or proof['start_ts'] != observed - 360
                        or proof['end_ts'] != observed - 60 or proof['received_ts'] < observed):
                    raise ValueError('INTERMARKET_HISTORY_SUPPORT_IDENTITY_OR_WINDOW_INVALID')
                symbols.append(symbol)
            if len(ids) != len(constituents) or meta['start_ts'] != observed - 360 or meta['end_ts'] != observed - 60 or meta['lag_seconds'] != 60:
                raise ValueError('INTERMARKET_HISTORY_AGGREGATE_INVALID')
            single = {f'intermarket.COINBASE{symbol}.return_5m_lag_1m': symbol for symbol in UNIVERSE}
            pairs = {f'intermarket.COINBASE{left}.COINBASE{right}.relative_return_5m': [left, right]
                for index, left in enumerate(UNIVERSE) for right in UNIVERSE[index + 1:]}
            if feature in single:
                if symbols != [single[feature]] or window != 360 or meta['return_seconds'] != 300:
                    raise ValueError('INTERMARKET_HISTORY_SINGLE_FEATURE_INVALID')
            elif feature in pairs:
                if symbols != pairs[feature] or meta['ordered_pair'] != symbols or window != 300:
                    raise ValueError('INTERMARKET_HISTORY_PAIR_FEATURE_INVALID')
            elif feature in {f'intermarket.related_crypto.{name}' for name in
                    ('breadth_up_fraction_5m', 'observed_n', 'expected_n', 'coverage')}:
                own = next((symbol for symbol in UNIVERSE if instrument in
                    (symbol.replace('-', ''), symbol.split('-')[0] + 'USDT')), None)
                peers = [symbol for symbol in UNIVERSE if symbol != own]
                if (len(symbols) < 2 or any(symbol not in peers for symbol in symbols) or window != 300
                        or meta['universe'] != list(UNIVERSE) or meta['excluded_own_asset'] != own
                        or meta['related_peer_symbols'] != peers):
                    raise ValueError('INTERMARKET_HISTORY_BASKET_IDENTITY_INVALID')
            else:
                raise ValueError('INTERMARKET_HISTORY_FEATURE_UNKNOWN')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return 'FEATURE_HISTORY_PROVENANCE_INVALID'
    return None
