import json
import sqlite3
import threading

import pytest

from seiltanzer.g1_shadow_artifact_refinement import _artifact_payload, _artifact_valid
from seiltanzer.g1_shadow_runtime import _sha

from seiltanzer.config import Settings
from seiltanzer.passive_learning import PassiveLearningEngine
from test_g1c_shadow_calibration import _insert_pending_q


def _model():
    return {
        "algorithm_version": "g1c-platt-logit-v1",
        "model_family": "PLATT",
        "scope_key": "GLOBAL_TERMINAL_Q:proxy:inverse",
        "scope_json": json.dumps({
            "kind": "global_terminal_q_semantic",
            "q_relation": "proxy",
            "proxy_transform": "inverse",
        }, sort_keys=True),
        "training_cut_id": "cut-1",
        "training_cut_sha256": "a" * 64,
        "parameters_json": json.dumps({"a": 1.2, "b": -0.1}, sort_keys=True),
    }


def test_model_artifact_sha_revalidation_detects_parameter_tampering():
    model = _model()
    model["artifact_sha256"] = _sha(_artifact_payload(model))
    assert _artifact_valid(model) is True
    model["parameters_json"] = json.dumps({"a": 9.9, "b": -0.1}, sort_keys=True)
    assert _artifact_valid(model) is False


def test_model_artifact_sha_revalidation_detects_scope_tampering():
    model = _model()
    model["artifact_sha256"] = _sha(_artifact_payload(model))
    assert _artifact_valid(model) is True
    model["scope_json"] = json.dumps({
        "kind": "global_terminal_q_semantic",
        "q_relation": "proxy",
        "proxy_transform": "direct",
    }, sort_keys=True)
    assert _artifact_valid(model) is False


def _deadline_fixture(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / 'deadline.db'), Settings(), cache=None)
    captured = 1_780_000_000.0
    target = captured + 86400.0
    _insert_pending_q(engine, 'deadline-q', captured=captured, q_up=0.43)
    # Artificial frozen artifact: exercise admission and real SQLite insertion
    # without fitting/searching or claiming any empirical calibration evidence.
    model = _model()
    model.update(model_id='deadline-model', fit_run_id='fixture-fit',
                 created_ts=captured - 10.0, training_cutoff=captured - 20.0,
                 raw_n=60, effective_n=30, positive_n=30, negative_n=30,
                 unique_q_n=20, training_diagnostics_json='{}',
                 status='FITTED_UNVALIDATED', oos_validated=0,
                 production_selected=0, production_authority=0)
    model['artifact_sha256'] = _sha(_artifact_payload(model))
    with engine._lock, engine._conn:
        engine._conn.execute(
            'INSERT INTO g1c_shadow_models (' + ','.join(model) + ') VALUES ('
            + ','.join('?' for _ in model) + ')', tuple(model.values()))
    return engine, target


@pytest.mark.parametrize('seconds_after_expiry', [0.0, 1.0, -0.5])
def test_prediction_rechecks_native_expiry_at_sqlite_insert(tmp_path, monkeypatch,
                                                          seconds_after_expiry):
    from seiltanzer import g1_shadow_artifact_refinement as artifact

    engine, target = _deadline_fixture(tmp_path)
    now = [target - 1.0]
    monkeypatch.setattr(artifact.time, 'time', lambda: now[0])
    previous_predict = artifact._g1c._predict_parameters

    def advance_after_initial_deadline_check(*args):
        probability = previous_predict(*args)
        now[0] = target + seconds_after_expiry
        return probability

    monkeypatch.setattr(artifact._g1c, '_predict_parameters',
                        advance_after_initial_deadline_check)
    try:
        result = engine.g1c_predict_observation('deadline-q')
        rows = engine._conn.execute(
            'SELECT created_ts,authority,production_used FROM g1c_shadow_predictions'
        ).fetchall()
        if seconds_after_expiry >= 0:
            assert result['status'] == 'PREDICTION_TOO_LATE'
            assert result['predictions_created'] == 0
            assert not rows
        else:
            assert result['status'] == 'PREDICTED'
            assert result['predictions_created'] == 1
            assert len(rows) == 1
            assert rows[0]['created_ts'] == target + seconds_after_expiry
            assert rows[0]['authority'] == 'research_only'
            assert rows[0]['production_used'] == 0
        assert result['production_used'] is False
    finally:
        engine.close()


def test_sqlite_writer_wait_cannot_backdate_prediction_before_expiry(tmp_path, monkeypatch):
    from seiltanzer import g1_shadow_artifact_refinement as artifact

    engine, target = _deadline_fixture(tmp_path)
    writer = sqlite3.connect(str(tmp_path / 'deadline.db'), check_same_thread=False)
    writer.execute('BEGIN IMMEDIATE')
    now = [target - 1.0]
    monkeypatch.setattr(artifact.time, 'time', lambda: now[0])
    waiting = threading.Event()
    advanced = threading.Event()

    def trace(statement):
        if statement.startswith('BEGIN IMMEDIATE') or statement.startswith('INSERT OR IGNORE INTO g1c_shadow_predictions'):
            waiting.set()

    engine._conn.set_trace_callback(trace)

    def release_writer_after_expiry():
        if waiting.wait(2):
            # Hold the actual second-connection reservation while SQLite's
            # first writer statement waits, rather than merely blocking Python.
            threading.Event().wait(0.05)
            now[0] = target + 1.0
            advanced.set()
        writer.rollback()

    thread = threading.Thread(target=release_writer_after_expiry)
    thread.start()
    try:
        result = engine.g1c_predict_observation('deadline-q')
        assert waiting.is_set() and advanced.is_set()
        assert result['status'] == 'PREDICTION_TOO_LATE'
        assert result['predictions_created'] == 0
        assert engine._conn.execute('SELECT COUNT(*) FROM g1c_shadow_predictions').fetchone()[0] == 0
    finally:
        thread.join(timeout=3)
        engine._conn.set_trace_callback(None)
        writer.close()
        engine.close()


def test_prediction_preserves_callers_transaction(tmp_path, monkeypatch):
    from seiltanzer import g1_shadow_artifact_refinement as artifact

    engine, target = _deadline_fixture(tmp_path)
    monkeypatch.setattr(artifact.time, 'time', lambda: target - 1.0)
    try:
        engine._conn.execute('BEGIN')
        result = engine.g1c_predict_observation('deadline-q')
        assert result['predictions_created'] == 1
        assert engine._conn.in_transaction is True
        engine._conn.rollback()
        assert engine._conn.execute('SELECT COUNT(*) FROM g1c_shadow_predictions').fetchone()[0] == 0
    finally:
        engine.close()
