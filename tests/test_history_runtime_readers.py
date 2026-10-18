"""Actual live readers preserve full-history semantics after cold retirement."""
import sqlite3

import pytest

from seiltanzer.config import Settings
from seiltanzer.passive_learning import PassiveLearningEngine
from seiltanzer import g1_dataset_runtime as dataset
from seiltanzer import g1_dataset_refinement as refinement
from seiltanzer import measurement_path_runtime as measurement
from seiltanzer import research_scalability as scalability
from seiltanzer import history_storage_state as state
from test_history_rejected_source_index import rejected_source
from test_history_runtime_state import runtime_state


@pytest.fixture
def reader_pair(runtime_state, rejected_source, tmp_path):
    full, _ = rejected_source
    conn, data = runtime_state
    state.write_history_state(conn, data)
    conn.commit()
    path = tmp_path/'runtime-hot.db'
    with sqlite3.connect(path) as target:
        conn.backup(target)
    hot = PassiveLearningEngine(str(path), Settings(), cache=None)
    yield full, hot
    hot.close()


def without_clock(report):
    return {k:v for k,v in report.items() if k not in ('generated_ts','data_cutoff_ts')}


def test_actual_g1_exclusions_and_readiness_survive_retired_negative_source(reader_pair):
    full, hot = reader_pair
    assert without_clock(dataset.g1_dataset_exclusions(hot)) == without_clock(dataset.g1_dataset_exclusions(full))
    assert without_clock(refinement.status_refined(hot)) == without_clock(refinement.status_refined(full))
    assert without_clock(dataset.g1_dataset_cohorts(hot)) == without_clock(dataset.g1_dataset_cohorts(full))
    assert dataset._detect_source_mutations(hot) == dataset._detect_source_mutations(full) == 0
    assert [r['observation_id'] for r in dataset._eligible_rows(hot)] == ['keep']
    assert [r['observation_id'] for r in dataset._eligible_rows(full)] == ['keep']
    assert dataset.create_g1_dataset_cut(hot,cutoff_ts=1800000000.)['manifest_sha256'] == \
           dataset.create_g1_dataset_cut(full,cutoff_ts=1800000000.)['manifest_sha256']


def test_actual_passive_and_measurement_counts_preserve_original_errors(reader_pair):
    full, hot = reader_pair
    assert measurement._runtime_counters(hot) == measurement._runtime_counters(full)
    old_full, old_hot = measurement._ORIGINAL_STATUS(full), measurement._ORIGINAL_STATUS(hot)
    fields = ('raw_n','resolution_counts','current_f32_n','legacy_f2_n','legacy_f3_n','legacy_f31_n',
              'fixed_horizon_raw_n','option_native_raw_n','terminal_q_eligible_n',
              'terminal_q_resolved_n','first_touch_clean_n','first_touch_ambiguous_n','first_touch_point_only_n')
    assert {k:old_hot.get(k) for k in fields} == {k:old_full.get(k) for k in fields}
    light_full, light_hot = scalability._light_passive_status(full), scalability._light_passive_status(hot)
    assert {k:light_hot[k] for k in ('raw_n','resolution_counts','current_f32_n','evidence_eligible_n')} == {
            k:light_full[k] for k in ('raw_n','resolution_counts','current_f32_n','evidence_eligible_n')}
