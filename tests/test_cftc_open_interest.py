"""Actual-body fixture contracts; no fixture implies broker mapping or outcomes."""
from copy import deepcopy
import hashlib
import json

import pytest

from seiltanzer import edge_family_sources as sources
from seiltanzer.edge_family_adapters import build_edge_family_evidence
from seiltanzer.edge_family_history import position_history_features, history_provenance_reason

T0 = 1790866800.0


def body():
    return json.dumps([
        dict(report_date_as_yyyy_mm_dd='2026-09-29T00:00:00.000', cftc_contract_market_code='088691',
             noncomm_positions_long_all='100', noncomm_positions_short_all='30', open_interest_all='240'),
        dict(report_date_as_yyyy_mm_dd='2026-09-22T00:00:00.000', cftc_contract_market_code='088691',
             noncomm_positions_long_all='80', noncomm_positions_short_all='40', open_interest_all='200'),
        dict(report_date_as_yyyy_mm_dd='2026-09-15T00:00:00.000', cftc_contract_market_code='088691',
             noncomm_positions_long_all='90', noncomm_positions_short_all='35', open_interest_all='150'),
    ]).encode()


def parse(raw=None):
    parser = getattr(sources, 'parse_cot_open_interest', None)
    assert callable(parser), 'missing optional open-interest producer'
    return parser(body() if raw is None else raw, contract='088691', receipt=T0, source_id='cot-body')


def evidence(records, *, instrument='CFTC088691', cutoff=T0):
    return build_edge_family_evidence(dict(instrument=instrument, captured_ts=cutoff,
        edge_family_sources={'positioning': records}))['families']['positioning']


def test_exact_pair_body_receipt_and_independent_change_proof():
    record = parse()
    assert record['kind'] == 'observed_open_interest'
    assert record['net_position'] == 240
    assert record['position_series']['category'] == 'TOTAL_OPEN_INTEREST_FUTURES_ONLY'
    assert record['position_series']['body_sha256'] == hashlib.sha256(body()).hexdigest()
    assert record['published_at'] == record['received_ts'] == record['available_at'] == T0
    assert record['position_change_history'][0]['available_at'] == T0
    result = position_history_features(record, T0)
    assert result['features'] == {'positioning.observed_open_interest.change': 40,
        'positioning.observed_open_interest.previous_report_age_days': 7}
    for name, value in result['features'].items():
        meta = {**result['feature_provenance'][name], 'observed_ts': record['observed_ts'],
                'report_ts': record['report_ts'], 'published_at': record['published_at']}
        assert history_provenance_reason(name, meta, captured=T0, horizon=15, instrument='CFTC088691') is None


@pytest.mark.parametrize('order', [False, True])
def test_mixed_kind_features_do_not_overwrite_net_position(order):
    net = sources.parse_cot(body(), contract='088691', receipt=T0, source_id='cot-body')
    oi = parse()
    row = evidence([oi, net] if order else [net, oi])
    assert row['features']['positioning.net'] == 70
    assert row['features']['positioning.cot_report.change'] == 30
    assert row['features']['positioning.open_interest'] == 240
    assert row['features']['positioning.percentile'] == 1
    assert row['features']['positioning.history_n'] == 2
    assert row['features']['positioning.open_interest_percentile'] == 1
    assert row['features']['positioning.open_interest_history_n'] == 1
    assert row['features']['positioning.observed_open_interest.change'] == 40
    assert row['forecast_available'] is False
    assert row['voting_weight'] == 0
    assert len(row['evidence_family_ids']) == 1
    assert row['feature_provenance']['positioning.net']['dependency_group'] == row['feature_provenance']['positioning.open_interest']['dependency_group']


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('invalid', [None, True, -1, 'nan', 'inf', 1.5, 'bad'])
def test_invalid_immediate_pair_refuses_optional_oi_only(index, invalid):
    rows = json.loads(body())
    if invalid is None:
        rows[index].pop('open_interest_all')
    else:
        rows[index]['open_interest_all'] = invalid
    raw = json.dumps(rows).encode()
    with pytest.raises((ValueError, KeyError, TypeError)):
        parse(raw)
    assert sources.parse_cot(raw, contract='088691', receipt=T0, source_id='cot-body')['net_position'] == 70


def test_zero_count_is_actual_fact_and_shuffled_reports_use_exact_dates():
    rows = json.loads(body())
    rows[0]['open_interest_all'] = 0
    record = parse(json.dumps(list(reversed(rows))).encode())
    assert record['net_position'] == 0
    assert evidence([record])['features']['positioning.observed_open_interest.change'] == -200


@pytest.mark.parametrize('where', ['current', 'previous'])
def test_frozen_negative_oi_refused_without_rejecting_signed_net(where):
    record = parse()
    if where == 'current':
        record['net_position'] = -1
    else:
        record['position_change_history'][0]['net_position'] = -1
    assert not evidence([record])['features']
    assert not position_history_features(record, T0)['features']
    signed = sources.parse_cot(body(), contract='088691', receipt=T0, source_id='cot-body')
    signed['net_position'] = -20
    assert evidence([signed])['features']['positioning.net'] == -20


def test_future_receipt_proxy_and_corrupted_identity_stay_refused():
    record = parse()
    assert not evidence([record], cutoff=T0 - 1)['features']
    assert not evidence([sources._proxy(record, 'XAU')], instrument='XAU')['features']
    changed = deepcopy(record)
    changed['position_change_history'][0]['provenance']['category'] = 'NONCOMMERCIAL_LONG_MINUS_SHORT_FUTURES_ONLY'
    assert not evidence([changed])['features']


def test_mixed_actual_body_histories_pass_trainer_identity_binding(monkeypatch):
    # Contract fixture mapping/costs, not evidence of a real broker connection.
    import test_edge_family_training as fixture
    from seiltanzer.edge_family_training import _row_reason
    monkeypatch.setattr(fixture, 'BASE', T0)
    row = fixture.dataset(count=1)['rows'][0]
    records = [sources.parse_cot(body(), contract='088691', receipt=T0, source_id='cot-body'), parse()]
    for record in records:
        record['proxy_mapping'] = dict(source_instrument='CFTC088691', target_instrument='NAS100',
            validated=True, mapping_id='contract-fixture-only', validated_at=T0 - 1)
    selected = evidence(records, instrument='NAS100')
    row.update(family_id='positioning', features=selected['features'],
        feature_provenance=selected['feature_provenance'],
        feature_windows_sec={key: value['window_seconds'] for key, value in selected['feature_provenance'].items()
            if 'window_seconds' in value})
    assert _row_reason(row, T0 + 3600) is None


@pytest.mark.parametrize('oi_present', [True, False])
def test_bundle_reuses_one_cftc_get_and_retains_net_when_oi_missing(oi_present):
    raw = body()
    if not oi_present:
        rows = json.loads(raw)
        rows[1].pop('open_interest_all')
        raw = json.dumps(rows).encode()
    calls = []
    def fetch(url):
        calls.append(url)
        if url.startswith(sources.CFTC):
            return raw
        raise ValueError('fixture unrelated endpoint unavailable')
    bundle = sources.build_bundle(instruments=['XAU'], fetch=fetch, clock=lambda: T0,
        budget=sources.SourceBudget(min_interval=0))
    records = bundle['instruments']['XAU']['edge_family_sources']['positioning']
    assert [r['kind'] for r in records] == (['cot_report', 'observed_open_interest'] if oi_present else ['cot_report'])
    assert records[0]['net_position'] == 70
    assert bundle['raw_sources']['cftc:XAU']['parse_status'] == 'PARSED'
    if not oi_present:
        assert any(error['phase'] == 'open_interest' and error['reason'] == 'COT_OPEN_INTEREST_COUNT_MISSING_OR_INVALID' for error in bundle['errors'])
    assert sum(url.startswith(sources.CFTC) for url in calls) == 1
    assert not bundle['instruments']['XAU']['readiness']['positioning']['available']
