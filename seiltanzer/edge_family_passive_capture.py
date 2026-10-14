"""Capture already-published local facts at the original regular quote cutoff."""
from copy import deepcopy
import json
from types import SimpleNamespace

from .edge_family_source_runtime import load_family_source_context, MAX_SELECTED_BYTES
from .passive_learning import _json, _walk_timestamps
from .runtime_git_identity import runtime_git_sha

MAX_CAPTURE_BYTES = 256_000  # Existing immutable native-export transport ceiling.


def _audit(source, incoming, cutoff):
    rejected = source.get('rejected_sources') or []
    result = {'contract_version': 'family-source-market-capture-v1',
        'available': source.get('available') is True, 'reason': str(source.get('reason') or '')[:160],
        'captured_ts': cutoff, 'active_trade_required': False,
        'network_calls': False, 'runtime_fitting': False, 'models_loaded': 0,
        'forecast_available': False, 'production_authority': False,
        'rejected_source_n': len(rejected),
        'admitted_source_counts': {family: len(records) for family, records in incoming.items()},
        'attached_source_counts': {},
        'rejected_sources': [{'source_id': str(row.get('source_id') or '')[:128],
            'reason': str(row.get('reason') or '')[:160]} for row in rejected[:4]]}
    if len(json.dumps(result, allow_nan=False).encode()) > 2048:
        result.pop('rejected_sources')
    return result


def install_family_source_capture(engine, *, expected_sha=None):
    """Bind once to this engine's loaded generation; no global capture replacement."""
    if getattr(engine.passive, '_family_source_capture', None) is None:
        engine.passive._family_source_capture = SimpleNamespace(settings=engine.settings,
            expected_sha=runtime_git_sha() if expected_sha is None else expected_sha)


def enrich_capture(context, instrument, captured_ts, features):
    """Run after all existing capture hooks, before validation/hash/SQLite write."""
    if context is None or getattr(context.settings, 'demo', False):
        return features
    frozen = dict(features)
    try:
        loaded = load_family_source_context(context,
            {'instrument': instrument, 'captured_ts': captured_ts}, context.expected_sha)
        incoming = loaded['edge_family_sources']
        audit = _audit(loaded['edge_family_source_bundle_audit'], incoming, captured_ts)
        if incoming:
            existing = frozen.get('edge_family_sources', {})
            if not isinstance(existing, dict):
                audit.update(available=False, reason='EXISTING_CAPTURE_SOURCES_UNMERGEABLE')
            else:
                merged = deepcopy(existing)
                added = {}
                for family, records in incoming.items():
                    if family not in merged:
                        merged[family] = deepcopy(records)
                        added[family] = len(records)
                if len(json.dumps(merged, allow_nan=False).encode()) <= MAX_SELECTED_BYTES:
                    frozen['edge_family_sources'] = merged
                    audit['attached_source_counts'] = added
                    if not added:
                        audit.update(available=False, reason='EXISTING_CAPTURE_FAMILY_KEYS_PRESERVED')
                else:
                    audit.update(available=False, reason='CAPTURE_SOURCE_FACTS_EXCEED_BYTE_BOUND')
        frozen.setdefault('edge_family_source_bundle_audit', audit)
    except Exception as exc:
        frozen = dict(features)
        frozen.setdefault('edge_family_source_bundle_audit', _audit({
            'available': False, 'reason': 'CAPTURE_LOCAL_SOURCE_ERROR:' + type(exc).__name__},
            {}, captured_ts))
    # Optional enrichment must not make previously exportable immutable history
    # exceed the existing reader ceiling. Preserve native inputs and macro roots.
    # The source admission contract has fewer timestamp aliases than passive
    # capture. Incompatible optional clocks must yield to the original record;
    # ordinary base validation still rejects invalid original features.
    if any(ts > captured_ts + 1e-6 for _, ts in _walk_timestamps(frozen)):
        return features
    if len(_json(frozen).encode()) > MAX_CAPTURE_BYTES:
        return features
    return frozen
