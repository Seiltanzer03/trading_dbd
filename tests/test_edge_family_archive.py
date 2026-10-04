"""Archive admission tests use synthetic CI records, never broker evidence."""
from copy import deepcopy
import hashlib
import importlib
import json
import time

import pytest


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def episode(review='r1', captured=100.):
    snapshot = {'captured_ts': captured, 'trade_id': 7, 'instrument': 'NAS100',
                'source_receipts': [{'received_ts': captured - 1, 'raw': {'value': 4}}]}
    raw = canonical(snapshot).decode()
    return {'review_id': review, 'trade_id': 7, 'instrument': 'NAS100', 'captured_ts': captured,
            'snapshot_json': raw, 'snapshot_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'production_policy': 'HOLD', 'path_points': [{'ts': captured, 'r': 0., 'price': 100.},
            {'ts': captured + 10, 'r': 1., 'price': 101.}], 'path_truncated': False,
            'stored_replay': None}


def export(*records, exported=1000.):
    return {'read_only': True, 'exported_ts': exported, 'reviews': list(records)}


def assemble(*args, **kwargs):
    # Keep RED an explicit missing-feature assertion rather than collection error.
    spec = importlib.util.find_spec('seiltanzer.edge_family_archive')
    assert spec is not None, 'bounded archive implementation is absent'
    return importlib.import_module('seiltanzer.edge_family_archive').assemble_archive(*args, **kwargs)


def reasons(result):
    return {item['reason'] for item in result['exclusions']}


def test_deterministic_immutable_merge_and_snapshot_preservation():
    old, new = episode('old'), episode('new', 200)
    inputs = [export(new), export(old)]
    frozen = deepcopy(inputs)
    result = assemble(inputs)
    assert result['contract_version'] == 'edge-family-archive-v1'
    assert result['episodes'] == [old, new]
    assert result['dataset_sha256'] == hashlib.sha256(canonical([old, new])).hexdigest()
    assert result == assemble(list(reversed(inputs)))
    previous = assemble([export(old)])
    prior_copy = deepcopy(previous)
    assert assemble([export(new)], previous)['episodes'] == [old, new]
    result['episodes'][0]['path_points'][0]['r'] = 99
    assert inputs == frozen and previous == prior_copy


def test_identical_duplicates_collapse_without_changing_observations():
    value = episode()
    assert assemble([export(value, deepcopy(value)), export(value)])['episodes'] == [value]


def test_conflict_removes_both_versions_and_remains_excluded_across_merges():
    first = episode()
    second = episode()
    snapshot = json.loads(second['snapshot_json'])
    snapshot['source_receipts'][0]['raw']['value'] = 5
    second['snapshot_json'] = canonical(snapshot).decode()
    second['snapshot_sha256'] = hashlib.sha256(second['snapshot_json'].encode()).hexdigest()
    prior = assemble([export(first)])
    result = assemble([export(second)], prior)
    assert result['episodes'] == []
    assert 'IDENTITY_CONFLICT' in reasons(result)
    assert result == assemble([export(first), export(second)])
    assert assemble([export(first)], result)['episodes'] == []


def test_same_snapshot_changed_real_path_is_immutable_conflict():
    first = episode()
    second = deepcopy(first)
    second['path_points'][-1]['r'] = 2
    assert assemble([export(first, second)])['episodes'] == []


@pytest.mark.parametrize('mutation,reason', [
    (lambda r: r.update(snapshot_sha256='0' * 64), 'SNAPSHOT_HASH_MISMATCH'),
    (lambda r: r.update(snapshot_json='x' * 2_000_001), 'SNAPSHOT_TOO_LARGE'),
    (lambda r: r.update(path_points=[r['path_points'][0]] * 6001), 'PATH_TOO_LARGE'),
    (lambda r: r.update(captured_ts=2000.), 'CAPTURE_AFTER_EXPORT'),
    (lambda r: r['path_points'][-1].update(ts=2000.), 'OUTCOME_AFTER_EXPORT'),
    (lambda r: r['path_points'][0].update(r=float('nan')), 'INVALID_JSON'),
    (lambda r: r.update(synthetic=True), 'SYNTHETIC_RECORD'),
    (lambda r: r.update(demo=True), 'SYNTHETIC_RECORD'),
])
def test_invalid_records_are_explicitly_excluded(mutation, reason):
    value = episode()
    mutation(value)
    result = assemble([export(value)])
    assert result['episodes'] == []
    assert reason in reasons(result)


def test_future_export_and_non_read_only_envelopes_excluded():
    future = export(episode(), exported=time.time() + 1000)
    assert 'FUTURE_EXPORT' in reasons(assemble([future]))
    value = export(episode())
    value['read_only'] = False
    assert 'EXPORT_NOT_READ_ONLY' in reasons(assemble([value]))
    assert 'EXPORT_TOO_MANY_REVIEWS' in reasons(assemble([export(*[episode(str(i)) for i in range(33)])]))


def test_whole_episode_count_eviction_keeps_latest_512():
    records = [episode(str(i).zfill(4), 100 + i) for i in range(513)]
    exports = [export(*records[i:i + 32]) for i in range(0, 513, 32)]
    result = assemble(exports)
    assert result['episodes'] == records[1:]
    assert result['evictions'] == [{'review_id': '0000', 'reason': 'ARCHIVE_EPISODE_LIMIT'}]


def test_byte_limit_evicts_whole_episode(monkeypatch):
    assemble([])
    module = importlib.import_module('seiltanzer.edge_family_archive')
    first, second = episode('first'), episode('second', 200)
    expected_manifest = {'contract_version': 'edge-family-archive-v1', 'episodes': [second],
                         'dataset_sha256': hashlib.sha256(canonical([second])).hexdigest(),
                         'exclusions': [],
                         'evictions': [{'review_id': 'first', 'reason': 'ARCHIVE_BYTE_LIMIT'}]}
    monkeypatch.setattr(module, 'MAX_ARCHIVE_BYTES', len(canonical(expected_manifest)) + 1)
    result = assemble([export(first, second)])
    assert result['episodes'] == [second]
    assert result['evictions'] == [{'review_id': 'first', 'reason': 'ARCHIVE_BYTE_LIMIT'}]


def test_corrupt_previous_hash_never_carries_episodes_forward():
    previous = assemble([export(episode())])
    previous['episodes'][0]['path_points'][-1]['r'] = 88
    result = assemble([], previous)
    assert result['episodes'] == []
    assert 'PREVIOUS_HASH_MISMATCH' in reasons(result)


def test_append_only_path_completion_retains_all_real_observations():
    prefix = episode()
    prefix['path_points'] = prefix['path_points'][:1]
    prefix['path_truncated'] = True
    complete = episode()
    complete['stored_replay'] = {'resolved_ts': 110., 'resolution_kind': 'stop', 'replay_json': '{}'}
    result = assemble([export(complete)], assemble([export(prefix)]))
    assert result['episodes'] == [complete]
    assert result == assemble([export(complete), export(prefix)])


def test_snapshot_identity_and_synthetic_snapshot_rejected():
    for field, value, reason in [('trade_id', 9, 'SNAPSHOT_IDENTITY_MISMATCH'),
                                 ('synthetic', True, 'SYNTHETIC_RECORD')]:
        item = episode()
        snapshot = json.loads(item['snapshot_json'])
        snapshot[field] = value
        item['snapshot_json'] = canonical(snapshot).decode()
        item['snapshot_sha256'] = hashlib.sha256(item['snapshot_json'].encode()).hexdigest()
        assert reason in reasons(assemble([export(item)]))


def test_one_invalid_review_does_not_discard_other_valid_reviews():
    invalid = episode('invalid')
    invalid['snapshot_sha256'] = '0' * 64
    good = episode('good')
    result = assemble([export(invalid, good)])
    assert result['episodes'] == [good]
    assert {'review_id': 'invalid', 'reason': 'SNAPSHOT_HASH_MISMATCH'} in result['exclusions']


def test_malformed_snapshot_strategy_is_excluded_without_crash():
    item = episode()
    snapshot = json.loads(item['snapshot_json'])
    snapshot['strategy'] = 'bad'
    item['snapshot_json'] = canonical(snapshot).decode()
    item['snapshot_sha256'] = hashlib.sha256(item['snapshot_json'].encode()).hexdigest()
    result = assemble([export(item)])
    assert result['episodes'] == []
    assert 'INVALID_SNAPSHOT' in reasons(result)


def test_conflicting_snapshot_hash_cannot_leave_first_identity_admitted():
    good = episode()
    bad = deepcopy(good)
    bad['snapshot_sha256'] = '0' * 64
    result = assemble([export(good, bad)])
    assert result['episodes'] == []
    assert 'IDENTITY_CONFLICT' in reasons(result)
    assert 'SNAPSHOT_HASH_MISMATCH' in reasons(result)


def test_conflicting_terminal_resolution_is_not_replaced():
    first, second = episode(), episode()
    first['stored_replay'] = {'resolved_ts': 110., 'resolution_kind': 'stop', 'replay_json': '{}'}
    second['stored_replay'] = {'resolved_ts': 111., 'resolution_kind': 'manual_close', 'replay_json': '{}'}
    result = assemble([export(first), export(second)])
    assert result['episodes'] == []
    assert 'IDENTITY_CONFLICT' in reasons(result)


def test_aggregate_input_bound_fails_closed_without_partial_order_selection(monkeypatch):
    assemble([])
    module = importlib.import_module('seiltanzer.edge_family_archive')
    monkeypatch.setattr(module, 'MAX_INPUT_BYTES', 1000, raising=False)
    inputs = [export(episode('a')), export(episode('b'))]
    result = assemble(inputs)
    assert result['episodes'] == []
    assert 'INPUT_TOO_LARGE' in reasons(result)
    assert result == assemble(list(reversed(inputs)))


def test_transport_envelope_bound_applies_to_extra_fields(monkeypatch):
    assemble([])
    module = importlib.import_module('seiltanzer.edge_family_archive')
    monkeypatch.setattr(module, 'MAX_EXPORT_BYTES', 1000)
    item = export(episode())
    item['unexpected_payload'] = 'x' * 1000
    result = assemble([item])
    assert result['episodes'] == []
    assert 'PAYLOAD_TOO_LARGE' in reasons(result)


def test_full_manifest_not_only_episodes_obeys_decoded_byte_ceiling(monkeypatch):
    assemble([])
    module = importlib.import_module('seiltanzer.edge_family_archive')
    ceiling = len(canonical([episode()])) + 10
    monkeypatch.setattr(module, 'MAX_ARCHIVE_BYTES', ceiling)
    result = assemble([export(episode())])
    assert len(canonical(result)) <= ceiling


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('use_previous', [False, True])
def test_longer_truncated_export_retains_its_actual_completeness(reverse, use_previous):
    short = episode()
    longer = deepcopy(short)
    longer['path_points'].append({'ts': 120., 'r': 2., 'price': 102.})
    longer['path_truncated'] = True
    records = [longer, short] if reverse else [short, longer]
    if use_previous:
        result = assemble([export(records[1])], assemble([export(records[0])]))
    else:
        result = assemble([export(record) for record in records])
    assert canonical(result['episodes']) == canonical([longer])


@pytest.mark.parametrize('field', ['path', 'metadata', 'replay'])
@pytest.mark.parametrize('use_previous', [False, True])
def test_numeric_serialization_conflict_is_order_independent(field, use_previous):
    first = episode()
    if field == 'replay':
        first['stored_replay'] = {'resolved_ts': 110., 'r': 1.}
    second = deepcopy(first)
    if field == 'path':
        second['path_points'][0]['price'] = 100
    elif field == 'metadata':
        second['captured_ts'] = 100
    else:
        second['stored_replay']['r'] = 1
    results = []
    for records in ([first, second], [second, first]):
        if use_previous:
            result = assemble([export(records[1])], assemble([export(records[0])]))
        else:
            result = assemble([export(record) for record in records])
        assert canonical(result['episodes']) == b'[]'
        assert 'IDENTITY_CONFLICT' in reasons(result)
        results.append(result)
    assert canonical(results[0]) == canonical(results[1])
    assert results[0]['dataset_sha256'] == hashlib.sha256(b'[]').hexdigest()


@pytest.mark.parametrize('field', ['r', 'price'])
@pytest.mark.parametrize('bad', ['invalid', True, False, None, float('nan'),
                                 float('inf'), float('-inf'), 'missing'])
def test_observed_path_values_require_finite_real_numbers(field, bad):
    record = episode()
    if bad == 'missing':
        record['path_points'][0].pop(field)
    else:
        record['path_points'][0][field] = bad
    result = assemble([export(record)])
    assert result['episodes'] == []
    assert reasons(result) & {'INVALID_PATH', 'INVALID_JSON'}


def test_finite_real_path_values_preserve_original_serialization():
    record = episode()
    record['path_points'][0].update(r=-1, price=0)
    record['path_points'][1].update(r=1.25, price=100.25)
    assert canonical(assemble([export(record)])['episodes']) == canonical([record])


@pytest.mark.parametrize('field,bad', [
    ('exclusions', None), ('exclusions', {}), ('exclusions', ['bad']),
    ('exclusions', [{'reason': None}]), ('exclusions', [{'reason': 'x' * 129}]),
    ('exclusions', [{'reason': 'IDENTITY_CONFLICT', 'review_id': 7}]),
    ('exclusions', [{'reason': 'IDENTITY_CONFLICT', 'review_id': 'x' * 257}]),
    ('exclusions', [{'reason': 'IDENTITY_CONFLICT'}]),
    ('evictions', None), ('evictions', {}), ('evictions', ['bad']),
    ('evictions', [{'review_id': 1, 'reason': 'ARCHIVE_BYTE_LIMIT'}]),
])
def test_malformed_previous_diagnostics_reject_entire_prior(field, bad):
    previous = assemble([export(episode('old'))])
    previous[field] = bad
    frozen = deepcopy(previous)
    good = episode('new', 200)
    result = assemble([export(good)], previous)
    assert result['episodes'] == [good]
    assert 'INVALID_PREVIOUS' in reasons(result)
    assert previous == frozen


def test_diagnostic_overflow_is_bounded_and_persistently_fail_closed(monkeypatch):
    module = importlib.import_module('seiltanzer.edge_family_archive')
    monkeypatch.setattr(module, 'MAX_DIAGNOSTICS', 2, raising=False)
    invalid = [episode(str(i)) for i in range(3)]
    for record in invalid:
        record['path_points'][0]['price'] = None
    result = assemble([export(*invalid, episode('good'))])
    assert result['episodes'] == []
    assert result['exclusions'] == [{'reason': 'ARCHIVE_DIAGNOSTIC_LIMIT'}]
    assert result['evictions'] == []
    assert assemble([export(episode('good'))], result) == result


def test_diagnostics_only_manifest_cannot_overrun_archive_budget(monkeypatch):
    module = importlib.import_module('seiltanzer.edge_family_archive')
    previous = assemble([])
    previous['exclusions'] = [{'reason': 'IDENTITY_CONFLICT', 'review_id': 'x' * 256}]
    # Prior is admitted under the budget, but new diagnostics exceed it even
    # with no episodes left to evict.
    monkeypatch.setattr(module, 'MAX_ARCHIVE_BYTES', len(canonical(previous)) + 1)
    bad = episode('y' * 256)
    bad['path_points'][0]['price'] = None
    result = assemble([export(bad)], previous)
    assert len(canonical(result)) <= len(canonical(previous)) + 1
    assert result['episodes'] == []
    assert result['exclusions'] == [{'reason': 'ARCHIVE_DIAGNOSTIC_LIMIT'}]


def padded(value, size):
    value = deepcopy(value)
    value['padding'] = ''
    value['padding'] = 'x' * (size - len(canonical(value)))
    return value


def test_decimal_export_byte_boundary_admits_exactly_32_million():
    envelope = padded(export(episode()), 32_000_000)
    assert assemble([envelope])['episodes'] == [episode()]
    envelope['padding'] += 'x'
    result = assemble([envelope])
    assert result['episodes'] == []
    assert 'PAYLOAD_TOO_LARGE' in reasons(result)


def test_decimal_aggregate_byte_boundary_admits_exactly_96_million():
    envelope = padded(export(episode()), 32_000_000)
    assert assemble([envelope] * 3)['episodes'] == [episode()]
    result = assemble([envelope] * 3 + [export()])
    assert result['episodes'] == []
    assert 'INPUT_TOO_LARGE' in reasons(result)


def test_decimal_previous_archive_byte_boundary_admits_exactly_96_million():
    previous = padded(assemble([export(episode())]), 96_000_000)
    assert assemble([], previous)['episodes'] == [episode()]
    previous['padding'] += 'x'
    result = assemble([], previous)
    assert result['episodes'] == []
    assert 'PAYLOAD_TOO_LARGE' in reasons(result)
