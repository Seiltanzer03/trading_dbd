"""Explicit metadata/diagnostic readers for verified cold rejections.

Full feature, forecast, outcome and admitted-model readers use full source rows.
Capability covers the v2 rejected-index/rollup contract; the external checker
also validates the actual data and the three owning calculation contracts.
"""
HISTORY_RUNTIME_CONTRACT = 'sqlite-history-runtime-v1'
JSON_PAYLOAD_READER_CONTRACT = 'sqlite-json-zlib-base64-v1'
from . import history_storage_state as storage
from . import history_runtime_projection as projection
from .passive_history_statistics import history_totals


def runtime_rollups(conn):
    state = storage.read_history_state(conn)
    if state is None:
        return None
    if state['contract'] != storage.RUNTIME_CONTRACT:
        raise ValueError('Runtime readers require a verified v2 history projection')
    from .passive_learning import PASSIVE_SCHEMA_VERSION
    from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
    from .g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION
    payload = projection.read_runtime_rollups(conn,
        expected_sha256=state['runtime_projection']['rollups']['payload_sha256'])
    if (payload['source_contract_version'] != PASSIVE_SCHEMA_VERSION
            or payload['measurement_contract_version'] != MEASUREMENT_RUNTIME_VERSION
            or payload['dataset_contract_version'] != G1_DATASET_CONTRACT_VERSION):
        raise ValueError('Runtime history projection calculation contract mismatch')
    return payload


def _add_counts(hot, cold):
    result = dict(hot)
    for key, value in cold.items():
        if isinstance(value, dict):
            result[key] = dict(result.get(key, {}))
            for name, count in value.items():
                result[key][name] = result[key].get(name, 0)+count
        else:
            result[key] = result.get(key, 0)+value
    return result


def with_retired_statistics(conn, physical):
    payload = runtime_rollups(conn)
    return _add_counts(physical, payload['statistics']) if payload else physical


def historical_source_totals(conn):
    physical = history_totals(conn)
    payload = runtime_rollups(conn)
    if not payload:
        return physical
    return _add_counts(physical, {key:payload['statistics'][key] for key in physical})


def with_retired_measurement(conn, physical):
    payload = runtime_rollups(conn)
    return _add_counts(physical, payload['measurement']) if payload else physical


def retired_evidence_count(conn):
    payload = runtime_rollups(conn)
    return payload['evidence_eligible_n'] if payload else 0


def archived_membership_rows(conn, *, limit=None, verify_hash=True):
    payload = runtime_rollups(conn)
    if not payload:
        return []
    sql = f'SELECT a.*,g.observation_id AS membership_observation_id,' \
        'g.source_record_sha256 AS expected_source_sha256,g.forecast_eval_eligible,g.q_to_p_eligible,' \
        'g.forecast_family,g.exclusion_reasons_json,g.q_exclusion_reasons_json,g.primary_reason,' \
        'g.measurement_runtime_contract ' \
        f'FROM "{projection.TABLE}" a LEFT JOIN g1_dataset_membership g ' \
        'ON g.observation_id=a.observation_id AND g.dataset_contract_version=? ' \
        'ORDER BY a.captured_ts,a.source_rowid'
    args = [payload['dataset_contract_version']]
    if limit is not None:
        sql += ' LIMIT ?'
        args.append(max(1, min(20000, int(limit))))
    cursor = conn.execute(sql, args)
    names = [c[0] for c in cursor.description]
    rows = []
    for raw in cursor:
        row = dict(zip(names, raw))
        if (row['membership_observation_id'] is None
                or type(row['forecast_eval_eligible']) is not int or row['forecast_eval_eligible'] != 0
                or type(row['q_to_p_eligible']) is not int or row['q_to_p_eligible'] != 0
                or (verify_hash and row['expected_source_sha256'] != row['source_record_sha256'])):
            raise ValueError('Archived rejected membership is missing, admitted or changed')
        rows.append(row)
    return rows
