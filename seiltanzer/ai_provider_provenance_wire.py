"""Lossless sharing of repeated source metadata in the independent LLM wire."""
from copy import deepcopy
import json


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def share_provenance(projected: dict) -> dict:
    """Resolve refs recursively, then merge family defaults with feature overrides.

    Only metadata is encoded. Every numerical fact and all original source clocks
    remain in traversable JSON; the authoritative snapshot is never mutated.
    """
    if 'feature_provenance_table' in projected or 'feature_provenance_defaults' in projected:
        return projected
    facts = projected.get('edge_family_facts') or {}
    if not isinstance(facts, dict):
        return projected
    result = deepcopy(projected)
    defaults = {}
    roots = []

    for family, row in sorted(facts.items()):
        provenance = row.get('feature_provenance') if isinstance(row, dict) else None
        if not isinstance(provenance, dict):
            continue
        rows = [meta for _, meta in sorted(provenance.items()) if isinstance(meta, dict)]
        common = {}
        if len(rows) > 1:
            for key, value in sorted(rows[0].items()):
                encoded = _json(value)
                if all(key in meta and _json(meta[key]) == encoded for meta in rows[1:]):
                    common[key] = value
        target = result['edge_family_facts'][family]['feature_provenance']
        if common:
            defaults[family] = deepcopy(common)
            for feature, meta in sorted(provenance.items()):
                if isinstance(meta, dict):
                    target[feature] = {key: deepcopy(value) for key, value in meta.items()
                                       if key not in common}
        roots.append(target)
    if defaults:
        result['feature_provenance_defaults'] = defaults
        roots.append(defaults)

    counts = {}
    reserved_ref = False

    def count(value):
        nonlocal reserved_ref
        if isinstance(value, dict):
            if set(value) == {'provenance_ref'}:
                reserved_ref = True
            for child in value.values():
                count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)
        else:
            return
        key = _json(value)
        if len(key) >= 128:
            counts[key] = counts.get(key, 0) + 1

    for root in roots:
        count(root)
    # Preserve a pre-existing literal reserved object rather than misdecode it.
    if reserved_ref:
        return projected

    table = {}
    identities = {}

    def encode(value, permit_ref=True):
        if not isinstance(value, (dict, list)):
            return value
        key = _json(value)
        if permit_ref and counts.get(key, 0) > 1:
            if key not in identities:
                identity = 'p' + str(len(identities))
                identities[key] = identity
                table[identity] = encode(value, False)
            return {'provenance_ref': identities[key]}
        if isinstance(value, dict):
            return {name: encode(child) for name, child in sorted(value.items())}
        return [encode(child) for child in value]

    # Keep the family/feature maps explicit; references encode metadata values.
    for family, row in sorted(result['edge_family_facts'].items()):
        provenance = row.get('feature_provenance') if isinstance(row, dict) else None
        if isinstance(provenance, dict):
            row['feature_provenance'] = {name: encode(meta) for name, meta in sorted(provenance.items())}
    if defaults:
        result['feature_provenance_defaults'] = {family: encode(meta) for family, meta in sorted(defaults.items())}
    if table:
        result['feature_provenance_table'] = table
    return result if len(_json(result).encode()) < len(_json(projected).encode()) else projected
