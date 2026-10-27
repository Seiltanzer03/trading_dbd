"""Decision facts for the provider; exact technical audit stays in local storage."""
from copy import deepcopy
import json
import re


PACKET_VERSION = 'independent-decision-packet-v1'
_DIGEST = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])')
_PROOF_FIELDS = frozenset((
    'body_sha256', 'supporting_body_sha256', 'preliminary_context_sha256',
    'reference_proof', 'constituent_provenance', 'supporting_source_ids',
    'history_contract_version',
))


def _without_byte_proofs(value):
    if isinstance(value, dict):
        return {key: _without_byte_proofs(child) for key, child in value.items()
                if key not in _PROOF_FIELDS}
    if isinstance(value, list):
        return [_without_byte_proofs(child) for child in value]
    return value


def _rows_table(rows):
    sequence = isinstance(rows, list)
    if not isinstance(rows, (dict, list)) or len(rows) < 2:
        return rows
    items = list(enumerate(rows)) if sequence else list(rows.items())
    if not all(isinstance(row, dict) for _, row in items):
        return rows
    groups = {}
    for name, row in items:
        columns = tuple(sorted(row))
        group = groups.setdefault(columns, {'columns': list(columns), 'rows': {}})
        group['rows'][str(name)] = [row[key] for key in columns]
    result = {'row_kind': 'sequence' if sequence else 'mapping',
              'column_groups': [groups[key] for key in sorted(groups)]}
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
    return result if size(result) < size(rows) else rows


def build_decision_packet(full_input: dict) -> tuple[dict, dict[str, str]]:
    """Return a packet and label->exact digest mapping without changing inputs.

    Caller retains the full masked input and validates its original clocks.
    Removing byte proofs does not grant source authority: admitted availability,
    provider/symbol, clocks, dependencies, quality and preliminary roles stay.
    """
    packet = deepcopy(full_input)
    packet.pop('edge_family_source_bundle_audit', None)
    manager = packet.get('policy_manager')
    if isinstance(manager, dict):
        # Delivery details and another expert's exploratory budget are local
        # audit, not independent current-model evidence. Risk/gate/economics,
        # source reliability and metric coverage are retained in their entirety.
        audit = manager.pop('input_audit', None)
        quote = ((audit.get('rows') or {}).get('instrument_price')
                 if isinstance(audit, dict) else None)
        if isinstance(quote, dict):
            manager['instrument_quote_audit'] = quote
        manager.pop('llm_edge_exploratory_weight', None)
    context = packet.get('research_working_context')
    if isinstance(context, dict) and isinstance(context.get('source_provenance'), dict):
        context['source_provenance'] = _without_byte_proofs(context['source_provenance'])
    facts = packet.get('edge_family_facts')
    if isinstance(facts, dict):
        for row in facts.values():
            if isinstance(row, dict) and isinstance(row.get('feature_provenance'), dict):
                row['feature_provenance'] = _without_byte_proofs(row['feature_provenance'])

    original = json.dumps(full_input, ensure_ascii=False, allow_nan=False)
    source_ids = set()
    def collect_sources(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'features':
                    continue
                if key in ('source_id', 'source_ids', 'supporting_source_ids'):
                    values = child if isinstance(child, list) else [child]
                    source_ids.update(item for item in values if isinstance(item, str) and len(item) >= 48)
                collect_sources(child)
        elif isinstance(value, list):
            for child in value:
                collect_sources(child)
    collect_sources(packet)
    source_prefix = '@source:'
    while source_prefix in original:
        source_prefix += '_'
    source_forward = {identity: source_prefix + str(index) + ';'
                      for index, identity in enumerate(sorted(source_ids))}
    source_pattern = (re.compile('|'.join(re.escape(identity) for identity in
                                        sorted(source_ids, key=lambda item: (-len(item), item))))
                      if source_ids else None)
    digests = set()

    def visit(value, replace=None):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                # Numerical feature names and values are exact, even if a
                # producer happened to use a digest-looking feature identifier.
                if key == 'features':
                    result[key] = child
                    continue
                if key == 'feature_provenance' and isinstance(child, dict):
                    result[key] = {name: visit(meta, replace) for name, meta in child.items()}
                    continue
                new_key = text(key, replace)
                result[new_key] = visit(child, replace)
            return result
        if isinstance(value, list):
            return [visit(child, replace) for child in value]
        if isinstance(value, str):
            return text(value, replace)
        return value

    def text(value, replace):
        if not isinstance(value, str):
            return value
        if replace is None:
            digests.update(_DIGEST.findall(value))
            return value
        return replace(value)

    if source_pattern:
        packet = visit(packet, lambda value: source_pattern.sub(
            lambda match: source_forward[match.group()], value))
    visit(packet)
    # A label namespace absent from every original string cannot collide with
    # literal source identifiers. Terminators also distinguish label1/label10.
    prefix = '@sha:'
    while prefix in original:
        prefix += '_'
    forward = {digest: prefix + str(index) + ';' for index, digest in enumerate(sorted(digests))}
    packet = visit(packet, replace=lambda value: _DIGEST.sub(lambda match: forward[match.group()], value))
    if isinstance(packet.get('edge_family_facts'), dict) and packet['edge_family_facts']:
        from .ai_provider_provenance_wire import share_provenance
        packet = share_provenance(packet)
    manager = packet.get('policy_manager')
    if isinstance(manager, dict) and 'policies' in manager:
        manager['policies'] = _rows_table(manager['policies'])
    if 'active_management_candidates' in packet:
        packet['active_management_candidates'] = _rows_table(packet['active_management_candidates'])
    for row in (packet.get('edge_family_facts') or {}).values():
        if isinstance(row, dict) and 'feature_provenance' in row:
            row['feature_provenance'] = _rows_table(row['feature_provenance'])
    aliases = {label: identity for identity, label in source_forward.items()}
    aliases.update({label: digest for digest, label in forward.items()})
    packet['decision_packet_version'] = PACKET_VERSION
    return packet, aliases


def _original_rows(value, kind):
    if not isinstance(value, dict) or 'column_groups' not in value:
        return value
    if set(value) != {'row_kind', 'column_groups'} or value['row_kind'] != kind:
        raise ValueError('invalid decision row table')
    groups = value['column_groups']
    if not isinstance(groups, list) or not groups:
        raise ValueError('invalid decision row groups')
    result = {}
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'columns', 'rows'}:
            raise ValueError('invalid decision row group')
        columns, rows = group['columns'], group['rows']
        if (not isinstance(columns, list) or not all(isinstance(key, str) for key in columns)
                or len(columns) != len(set(columns)) or not isinstance(rows, dict) or not rows):
            raise ValueError('invalid decision row columns')
        for name, values in rows.items():
            if (not isinstance(name, str) or name in result or not isinstance(values, list)
                    or len(values) != len(columns)):
                raise ValueError('invalid decision row values')
            result[name] = dict(zip(columns, values, strict=True))
    if kind == 'sequence':
        if set(result) != {str(i) for i in range(len(result))}:
            raise ValueError('invalid decision row indices')
        return [result[str(i)] for i in range(len(result))]
    return result


def restore_decision_rows(packet: dict) -> dict:
    """Expose original clock paths to the unchanged timestamp/risk validator."""
    if 'decision_packet_version' not in packet:
        manager = packet.get('policy_manager') or {}
        manager = manager if isinstance(manager, dict) else {}
        facts = packet.get('edge_family_facts') or {}
        facts = facts if isinstance(facts, dict) else {}
        values = [manager.get('policies'), packet.get('active_management_candidates')]
        values.extend(row.get('feature_provenance') for row in
                      facts.values() if isinstance(row, dict))
        if any(isinstance(value, dict) and 'column_groups' in value for value in values):
            raise ValueError('unversioned decision row table')
        return packet
    if packet.get('decision_packet_version') != PACKET_VERSION:
        raise ValueError('unsupported decision packet version')
    result = deepcopy(packet)
    result.pop('decision_packet_version')
    manager = result.get('policy_manager')
    if isinstance(manager, dict) and 'policies' in manager:
        manager['policies'] = _original_rows(manager['policies'], 'mapping')
    if 'active_management_candidates' in result:
        result['active_management_candidates'] = _original_rows(result['active_management_candidates'], 'sequence')
    for row in (result.get('edge_family_facts') or {}).values():
        if isinstance(row, dict) and 'feature_provenance' in row:
            row['feature_provenance'] = _original_rows(row['feature_provenance'], 'mapping')
    return result


def restore_evidence_identities(values: list[str], aliases: dict[str, str]) -> list[str]:
    """Restore only known digest labels before local source-claim validation."""
    if not aliases:
        return values
    pattern = re.compile('|'.join(re.escape(label) for label in aliases))
    return [pattern.sub(lambda match: aliases[match.group()], value) for value in values]
