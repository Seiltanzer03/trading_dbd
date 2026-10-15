"""Preserve execution cash flows while avoiding work on event-free segments."""
from dataclasses import replace
import hashlib
import json

import numpy as np
import pytest

from seiltanzer import execution_simulator as execution


def _spec():
    return execution.ExecutionSpec.from_values(current_r=.2, max_r=.2,
        take_r=2., rungs=(.5, 1., 1.5), rung_fraction_original=.1, be_after_r=1.)


def test_quiet_segments_do_not_enter_event_ordering(monkeypatch):
    calls = []
    ordered = sorted

    def observe(events):
        calls.append(len(events))
        return ordered(events)

    monkeypatch.setattr(execution, 'sorted', observe, raising=False)
    path = [.2, *([.21, .2] * 500)]
    result = execution.replay_execution_path(path, _spec())
    assert result.outcome_r == .2
    assert result.exit_step == 1000
    assert result.exit_reason == 'horizon'
    assert result.filled_rungs == ()
    assert len(calls) <= 1, 'quiet MC steps must not pay Python event-ordering cost'


@pytest.mark.parametrize('batch', [False, True])
def test_execution_results_match_frozen_linear_replay_reference(batch):
    base = _spec()
    specs = [base, replace(base, spike_r=.5, spike_fraction=.25),
        replace(base, spike_r=.75, spike_fraction=.8),
        replace(base, time_stop_fraction=.371), replace(base, time_stop_fraction=1.),
        replace(base, stop_r=.1), replace(base, max_r=1.4, be_execution_confirmed=False),
        replace(base, max_r=1.4, be_execution_confirmed=True),
        replace(base, rung_fraction_original=.5), replace(base, be_after_r=-.1),
        replace(base, take_r=.5)]
    rng = np.random.default_rng(173)
    paths = np.column_stack((np.full(128, .2),
        .2 + np.cumsum(rng.normal(0, .19, (128, 160)), axis=1))).tolist()
    paths.extend([[.2], [.2, 2.1, -2], [.2, 1.2, -1.2, 2.2],
        [.2, .5-1e-10, .5, .5+1e-10, .5-1e-11, 2.1],
        [.2, .2, .2], [.2, .1+1e-10, .1, -.1]])
    results = []
    for spec in specs:
        if batch:
            # Fixed-length MC paths use the batch path; short adversarial paths
            # are grouped separately so their actual horizon remains unchanged.
            outcomes = execution.replay_execution_paths(paths[:128], spec)
            outcomes.extend(execution.replay_execution_paths([path], spec)[0]
                            for path in paths[128:])
        else:
            outcomes = [execution.replay_execution_path(path, spec) for path in paths]
        results.extend(result.as_dict() for result in outcomes)
    digest = hashlib.sha256(json.dumps(results, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert digest == '9b0739acd2c0a899d776f18967ba87152620985d15d7e78e9c89b5e8e4c0522d'


@pytest.mark.parametrize('paths', [[], [.2, .3], [[.2, float('nan')]], [[.3, .4]]])
def test_batch_keeps_path_geometry_and_finiteness_validation(paths):
    with pytest.raises(ValueError):
        execution.replay_execution_paths(paths, _spec())


def test_bounded_chunks_preserve_every_path_in_original_order():
    paths = [[.2, end] for end in (-1.1, 2.1, .4)] * 171
    results = execution.replay_execution_paths(paths, _spec())
    assert len(results) == 513
    assert results == [execution.replay_execution_path(path, _spec()) for path in paths]
