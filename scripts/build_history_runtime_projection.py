"""Build exact derived storage from a verified immutable attached full source."""


def build_runtime_projection(conn, retired_rowids, *, deadline):
    from seiltanzer import history_runtime_projection as projection
    from seiltanzer.g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION
    from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
    from seiltanzer.measurement_path_runtime import runtime_counters_for_rows
    from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION
    from seiltanzer.passive_history_statistics import passive_history_statistics
    conn.execute('CREATE TEMP TABLE _runtime_retired_rows(source_rowid INTEGER PRIMARY KEY)')
    try:
        conn.executemany('INSERT INTO temp._runtime_retired_rows VALUES(?)', ((x,) for x in retired_rowids))
        conn.execute('CREATE TEMP VIEW history_retired_sources AS SELECT p.* '
            'FROM src.passive_market_observations p JOIN temp._runtime_retired_rows r ON p.rowid=r.source_rowid')
        index = projection.create_rejected_source_index(conn,conn,retired_rowids,
            dataset_contract_version=G1_DATASET_CONTRACT_VERSION, deadline=deadline, source_schema='src')
        statistics = passive_history_statistics(conn, current_feature_version=PASSIVE_SCHEMA_VERSION, source='retired')
        cursor = conn.execute('SELECT * FROM temp.history_retired_sources WHERE feature_contract_version=?',
                              (PASSIVE_SCHEMA_VERSION,))
        names = [c[0] for c in cursor.description]
        def source_rows():
            for raw in cursor:
                projection._check(deadline)
                yield dict(zip(names, raw))
        measurement = runtime_counters_for_rows(source_rows())
        evidence_n = conn.execute("SELECT count(*) FROM temp.history_retired_sources "
            "WHERE feature_contract_version=? AND evidence_eligible=1 AND observation_origin='background_collector' "
            "AND price_kind='direct' AND forecast_json LIKE ?", (PASSIVE_SCHEMA_VERSION,
            f'%"measurement_runtime_contract":"{MEASUREMENT_RUNTIME_VERSION}"%')).fetchone()[0]
        rollups = projection.write_runtime_rollups(conn, dict(contract=projection.ROLLUP_CONTRACT,
            source_contract_version=PASSIVE_SCHEMA_VERSION, measurement_contract_version=MEASUREMENT_RUNTIME_VERSION,
            dataset_contract_version=G1_DATASET_CONTRACT_VERSION, statistics=statistics,
            measurement=measurement, evidence_eligible_n=evidence_n, production_authority=False))
        projection.install_source_guards(conn)
        proof = dict(contract=projection.PROJECTION_CONTRACT, rejected_index=index, rollups=rollups)
        projection.validate_runtime_projection(conn, proof, retired_rowids=retired_rowids, deadline=deadline)
        return proof
    finally:
        conn.execute('DROP VIEW IF EXISTS temp.history_retired_sources')
        conn.execute('DROP TABLE temp._runtime_retired_rows')
