"""Persist exact retired diagnostics separately from original source data."""
import copy

import pytest

from seiltanzer import history_runtime_projection as projection
from seiltanzer.measurement_path_runtime import runtime_counters_for_rows
from seiltanzer.passive_history_statistics import passive_history_statistics
from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION
from test_history_rejected_source_index import rejected_source


def real_payload(engine):
    return dict(contract='sqlite-history-runtime-rollups-v1',
        source_contract_version=PASSIVE_SCHEMA_VERSION,
        measurement_contract_version=MEASUREMENT_RUNTIME_VERSION,
        dataset_contract_version=G1_DATASET_CONTRACT_VERSION,
        statistics=passive_history_statistics(engine._conn, current_feature_version=PASSIVE_SCHEMA_VERSION),
        measurement=runtime_counters_for_rows(dict(r) for r in engine._conn.execute('SELECT * FROM passive_market_observations')),
        evidence_eligible_n=1, production_authority=False)


def test_exact_real_rollups_are_hashed_bounded_and_immutable(rejected_source):
    engine, output = rejected_source
    payload = real_payload(engine)
    receipt = projection.write_runtime_rollups(output, payload)
    assert receipt['row_count'] == 1
    assert receipt['production_authority'] is False
    assert projection.read_runtime_rollups(output, expected_sha256=receipt['payload_sha256']) == payload
    with pytest.raises(Exception, match='immutable'):
        output.execute('DELETE FROM archived_passive_rollups')


@pytest.mark.parametrize('hazard', ['numeric_authority', 'count_mismatch', 'resolved', 'negative_counter', 'missing_counter'])
def test_bad_rollup_contract_cannot_be_persisted(rejected_source, hazard):
    engine, output = rejected_source
    payload = copy.deepcopy(real_payload(engine))
    if hazard == 'numeric_authority':
        payload['production_authority'] = 0
    elif hazard == 'count_mismatch':
        payload['statistics']['raw_n'] += 1
    elif hazard == 'resolved':
        payload['statistics']['resolution_counts'] = {'resolved':1}
    elif hazard == 'negative_counter':
        payload['measurement']['runtime_current_n'] = -1
    else:
        del payload['measurement']['time_contract_error_n']
    with pytest.raises(ValueError, match='runtime rollup'):
        projection.write_runtime_rollups(output, payload)
    assert output.execute("SELECT count(*) FROM sqlite_schema WHERE name='archived_passive_rollups'").fetchone() == (0,)
