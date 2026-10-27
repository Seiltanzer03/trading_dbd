"""Share exact repeated provenance in the LLM wire; no input fact is dropped."""
from copy import deepcopy
import json


def share_provenance(projected: dict) -> dict:
    if 'feature_provenance_table' in projected:
        return projected
    facts = projected.get('edge_family_facts') or {}
    if not isinstance(facts, dict):
        return projected
    groups = {}
    for family, row in facts.items():
        provenance = row.get('feature_provenance') if isinstance(row, dict) else None
        if not isinstance(provenance, dict):
            continue
        for feature, meta in provenance.items():
            if not isinstance(meta, dict):
                continue
            key = json.dumps(meta, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
            if len(key) >= 128:
                groups.setdefault(key, []).append((family, feature))
    repeated = [(key, locations) for key, locations in groups.items() if len(locations) > 1]
    if not repeated:
        return projected
    result = deepcopy(projected)
    table = {}
    for index, (key, locations) in enumerate(repeated):
        identity = 'p'+str(index)
        table[identity] = json.loads(key)
        for family, feature in locations:
            result['edge_family_facts'][family]['feature_provenance'][feature] = {'provenance_ref': identity}
    result['feature_provenance_table'] = table
    return result
