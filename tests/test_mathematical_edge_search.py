"""Source provenance and finite search checks; artificial bars test contracts only."""
import numpy as np
import pytest

from scripts.run_mathematical_edge import _validated_bars, build_report
from seiltanzer import mathematical_edge as edge
from seiltanzer.config import ALL_INSTRUMENTS


def test_absent_sources_show_all_configured_instruments_without_edge_claims():
    report = build_report([], {'BTCUSD': 'PROVIDER_BLOCKED'}, 1000.)
    assert set(report['instruments']) == set(ALL_INSTRUMENTS)
    assert set(report['instrument_matrix']) == set(ALL_INSTRUMENTS)
    assert len(report['instrument_matrix']) == 13
    assert report['requested_horizons_minutes'] == [15, 30, 60, 120]
    assert report['instruments']['BTCUSD']['reason'] == 'PROVIDER_BLOCKED'
    assert all(not row['search_completed'] for row in report['instrument_matrix'].values())
    assert all(row['management_role'] == 'NO_WEIGHT' for row in report['instrument_matrix'].values())
    assert report['net_economic_proof'] is False
    assert report['target_coverage']['adverse_excursion'] == 'GENERIC_2BP_DOWNSIDE_AND_UPSIDE_PATH_TOUCH_PROBABILITIES'


def test_source_validation_rejects_invalid_future_and_conflicting_duplicate_bars():
    row = dict(bar_end_ts=300., open=100., high=102., low=98., close=101.)
    valid, exclusions = _validated_bars([
        row, dict(row), dict(row, bar_end_ts=600.),
        dict(row, high=99.), dict(row, close=float('nan')),
    ], 300.)
    assert valid == [row]
    assert exclusions == {'future_or_unfinished': 1, 'invalid_ohlc': 2, 'duplicate_timestamp': 1}
    with pytest.raises(ValueError, match='conflicting duplicate'):
        _validated_bars([row, dict(row, close=100.)], 300.)


def test_120_minute_labels_are_nonoverlapping_and_reject_session_gaps():
    rows = [dict(bar_end_ts=(i+1)*300., open=100+i*.01, high=101+i*.01,
                 low=99+i*.01, close=100+i*.01) for i in range(200)]
    x, y, times, ends = edge.dataset(rows, 120)
    assert len(x) > 3
    assert np.all(ends-times == 7200)
    assert np.all(times[1:] >= ends[:-1])
    _, _, gapped_times, gapped_ends = edge.dataset(rows[:90]+rows[91:], 120)
    assert not any(start < rows[90]['bar_end_ts'] < end
                   for start, end in zip(gapped_times, gapped_ends))


def test_two_positive_validation_blocks_cannot_mask_negative_average(monkeypatch):
    rows = [dict(bar_end_ts=(i+1)*300., open=100+i*.01, high=101+i*.01,
                 low=99+i*.01, close=100+i*.01) for i in range(900)]
    # Two heads × three validation blocks, then both held-out heads are positive.
    values = iter([10., 10., 10., 10., -100., -100.] + [1.]*8)
    monkeypatch.setattr(edge, 'gain', lambda *args: next(values))
    monkeypatch.setattr(edge, 'train_path_heads', lambda *args: {})
    result = edge.train_instrument('NAS100', rows, rows[-1]['bar_end_ts'], horizons=(15,))
    assert result['status'] == 'NO_SUPPORTED_ADVANTAGE_YET'
    assert all(diag['validation_mean_gain_mbit'] < 0 for diag in result['diagnostics'].values())
    assert all(not diag['working_supported'] for diag in result['diagnostics'].values())
    assert result['candidate_audit']['15']['final_test_touched_for_selection'] is False


def test_same_bar_touch_cannot_claim_barrier_order_and_never_uses_t0_range():
    # Future flat bars have no event; the broad last feature bar predates T0.
    rows = [dict(bar_end_ts=(i+1)*300., open=100., high=100., low=100., close=100.)
            for i in range(19)]
    rows[12].update(high=110., low=90.)
    _, labels, times, _ = edge.path_dataset(rows, 15)
    assert times[0] == rows[12]['bar_end_ts']
    assert labels[0, 0] == labels[0, 1] == labels[0, 3] == 0
    assert np.isnan(labels[0, 2])
    rows[13].update(high=101., low=99.)
    _, labels, _, _ = edge.path_dataset(rows, 15)
    assert labels[0, 0] == labels[0, 1] == labels[0, 3] == 1
    assert np.isnan(labels[0, 2])
    rows[13].update(high=101., low=100.)
    rows[14].update(high=100., low=99.)
    _, labels, _, _ = edge.path_dataset(rows, 15)
    assert labels[0, 2] == 1


def test_constant_path_labels_cannot_be_discovered_edge():
    rows = [dict(bar_end_ts=(i+1)*300., open=100., high=100., low=100., close=100.)
            for i in range(900)]
    result = edge.train_path_heads(rows, [110000., 150000., 190000., 220000.], (15,))
    assert all(not row['working_supported'] for row in result.values())
    assert all(row['head'] is None for row in result.values())
