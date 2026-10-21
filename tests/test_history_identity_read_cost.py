"""Retained identity validation must avoid reading wide observation payloads."""
import sqlite3

import pytest

from seiltanzer.history_storage_state import identity_proof


class RecordedConnection(sqlite3.Connection):
    identity_query = None

    def execute(self, sql, parameters=()):
        if sql.startswith('SELECT ') and 'typeof(observation_id)' in sql:
            self.identity_query = (sql, parameters)
        return super().execute(sql, parameters)


@pytest.mark.parametrize('table', ['passive_market_observations', 'g1s_observations'])
def test_wide_rows_use_existing_covering_identity_index_with_same_typed_root(table):
    conn = sqlite3.connect(':memory:', factory=RecordedConnection)
    try:
        conn.execute(f'CREATE TABLE {table}(observation_id, payload BLOB)')
        values = [None, b'\x00\xff', 7, 1.25, 'идентификатор', 'later']
        conn.executemany(f'INSERT INTO {table} VALUES (?,?)',
                         [(value, b'x' * 4096) for value in values])
        expected = identity_proof(conn, table, 5)
        conn.execute(f'CREATE INDEX "capture""identity" ON {table}(observation_id)')
        assert identity_proof(conn, table, 5) == expected
        query, parameters = conn.identity_query
        plan = conn.execute('EXPLAIN QUERY PLAN ' + query, parameters).fetchall()
        # A rowid table walk fetches large source payload pages on production.
        # The existing identity index contains all typed IDs and implicit rowids.
        assert any('COVERING INDEX capture"identity' in row[3] for row in plan), plan
        conn.execute(f'UPDATE {table} SET observation_id=? WHERE rowid=1', ('changed',))
        assert identity_proof(conn, table, 5) != expected
    finally:
        conn.close()


@pytest.mark.parametrize('index_sql', [
    None,
    'CREATE INDEX partial_identity ON passive_market_observations(observation_id) WHERE observation_id IS NOT NULL',
    'CREATE INDEX expression_identity ON passive_market_observations(lower(observation_id))',
    'CREATE INDEX wide_identity ON passive_market_observations(observation_id,payload)',
])
def test_missing_or_noncovering_identity_index_keeps_complete_proof(index_sql):
    conn = sqlite3.connect(':memory:', factory=RecordedConnection)
    try:
        conn.execute('CREATE TABLE passive_market_observations(observation_id, payload)')
        conn.executemany('INSERT INTO passive_market_observations VALUES (?,?)',
                         [(None, 'null identity'), ('kept', 'payload')])
        expected = identity_proof(conn, 'passive_market_observations', 2)
        if index_sql:
            conn.execute(index_sql)
        assert identity_proof(conn, 'passive_market_observations', 2) == expected
        assert 'INDEXED BY' not in conn.identity_query[0]
    finally:
        conn.close()


def test_covering_index_does_not_admit_oversized_capture_identity():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE passive_market_observations(observation_id TEXT UNIQUE)')
        conn.execute('INSERT INTO passive_market_observations VALUES (?)', ('x' * 65537,))
        with pytest.raises(ValueError, match='identity exceeds byte bound'):
            identity_proof(conn, 'passive_market_observations', 1)
